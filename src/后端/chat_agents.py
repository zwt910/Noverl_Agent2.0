from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from 算法.config_llm import get_llm
from 算法.create_plot import OutlineSessionState, generate_outline, revise_outline, save_outline
from .chapter_writer import ChapterSessionState, generate_chapter, revise_chapter, save_chapter
from .chapter_editor import optimize_chapter, iterate_optimization, save_optimized_chapter
from .novel_manager import NovelContext

# 跨智能体移交时 main_chat 使用的路由键（与 active 一致）
VALID_HANDOFF_TARGETS = frozenset({"outline", "writer", "editor", "navigator"})

# main_chat 链式移交上限
HANDOFF_MAX_HOPS = 3

_REVISION_JUDGE_MAX_CHARS = 4000

_REVISION_JUDGE_SYSTEM = """你是小说创作流程中的「修改要求审核员」，只做一件事：判断作者的修改说明是否足够具体、可执行。

「足够具体」指：写手或编辑能据此知道要动哪些层面（如对话、节奏、描写、视角、某段情节、人物口吻、信息密度等）或明确的改写目标；不必完美，但不能是空话。

「不足够」指：过于抽象、几乎没有信息量，或等价于「优化一下」「改好点」「润色」这类无法单独执行的指令。

只输出一个 JSON 对象，不要 Markdown、不要其它解释文字。格式严格为：
{"specific_enough": true 或 false, "follow_up_question": "若 specific_enough 为 false，填一句中文友好追问；若为 true，填空字符串 ""}
"""


def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last <= first:
        return None
    try:
        obj = json.loads(text[first : last + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def revision_request_is_specific(
    request: Any,
    *,
    llm: Optional[BaseChatModel] = None,
) -> tuple[bool, str]:
    """
    由专用 LLM 判断修改要求是否足够具体（主编、写手、编辑共用）。
    返回 (是否通过, 未通过时给用户的追问话术)。
    """
    if request is None:
        return False, "请具体说明希望如何修改，不要留空。"
    text = str(request).strip()
    if not text:
        return False, "请具体说明希望如何修改，不要留空。"

    snippet = text if len(text) <= _REVISION_JUDGE_MAX_CHARS else text[:_REVISION_JUDGE_MAX_CHARS] + "\n…（已截断，仅用于审核）"
    model = llm or get_llm(temperature=0.1)
    try:
        resp = model.invoke(
            [
                SystemMessage(content=_REVISION_JUDGE_SYSTEM),
                HumanMessage(
                    content="请审核以下「修改要求」是否足够具体、可执行：\n\n" + snippet
                ),
            ]
        )
        raw = getattr(resp, "content", str(resp))
        raw = str(raw).strip()
        data = _parse_json_object(raw)
        if not data:
            return (
                False,
                "未能理解审核结果。请用更具体的一句话说明修改方向（例如要改对话、节奏或某段情节）。",
            )
        ok = data.get("specific_enough")
        if ok is True:
            return True, ""
        if ok is False:
            fq = data.get("follow_up_question")
            if isinstance(fq, str) and fq.strip():
                return False, fq.strip()
            return (
                False,
                "你的修改说明还比较笼统。请补充：具体想改哪里、改成什么样或达到什么效果？",
            )
        return (
            False,
            "你的修改说明还比较笼统。请补充：具体想改哪里、改成什么样或达到什么效果？",
        )
    except Exception:
        return (
            False,
            "暂时无法完成修改要求的判断，请稍后用更具体的一句话重试，或说明要改的内容与期望效果。",
        )


def _writer_reply_with_full_text(message: str, chapter_index: int, body: str) -> str:
    """生成正文后附带全文，便于用户直接审阅。"""
    body = (body or "").strip()
    if not body:
        return message
    return f"{message}\n\n---\n【第{chapter_index}章 · 正文全文】\n\n{body}"


# 生成大纲时「前文剧情摘要」窗口上限（章数）
_MAX_PREV_CHAPTERS_WINDOW = 80


def parse_prev_chapters_for_outline(raw: Any) -> Optional[int]:
    """
    解析「纳入剧情摘要的前文章节数」为正整数；非法则返回 None。
    数值会封顶到 _MAX_PREV_CHAPTERS_WINDOW。
    """
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 1:
        return None
    return min(n, _MAX_PREV_CHAPTERS_WINDOW)


def normalize_outline_user_requirements(raw: Any) -> str:
    """
    将 params.requirements 转为写入大纲生成的文案。
    用户可输入「无」等表示没有额外创作要求，对应空字符串交给模型。
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    core = s.rstrip("。.").strip().lower()
    if core in ("无", "没有", "无要求", "暂无", "none", "无特殊要求", "没什么要求"):
        return ""
    return s


def outline_create_requirements_ok(params: Dict[str, Any]) -> tuple[bool, str]:
    """
    创建 / 重新生成剧情大纲时，校验「用户要求」与「读取前文章节数」是否已由用户给出。
    - requirements：必须在 params 中出现；可为具体文字，或字面「无」表示无额外要求；不得缺 key、不得为空串。
    - prev_chapters：必须由用户给出合法正整数。
    返回 (是否通过, 未通过时的追问话术)。
    """
    if "requirements" not in params:
        return False, (
            "请补充本章的「用户要求」（创作方向、重点等）；若没有特别要求，请明确回复「无」。"
        )
    req = params.get("requirements")
    if req is None:
        return False, (
            "请补充本章的「用户要求」；若没有特别要求，请明确回复「无」。"
        )
    if not str(req).strip():
        return False, (
            "「用户要求」不能为空。请描述你的创作要求，或输入「无」表示没有额外要求。"
        )
    if params.get("prev_chapters") is None:
        return False, (
            "请指定「读取前文的章节数」（正整数，必填）。"
            "例如填 5 表示在已有章节总结中，向前最多参考 5 章；第 1 章无前文时也请给出一个数字。"
        )
    if parse_prev_chapters_for_outline(params.get("prev_chapters")) is None:
        return False, "「读取前文的章节数」必须是正整数（如 1～20），请重新说明。"
    return True, ""


def merge_handoff_params(handoff: Dict[str, Any]) -> Dict[str, Any]:
    """合并上一轮 params 与 handoff_params。"""
    merged = dict(handoff.get("params") or {})
    hp = handoff.get("handoff_params")
    if isinstance(hp, dict):
        merged.update(hp)
    return merged


def sanitize_handoff_for_target(prev: Dict[str, Any]) -> Dict[str, Any]:
    """供下一智能体 handle(handoff=...) 使用的精简结构。"""
    return {
        "params": dict(prev.get("params") or {}),
        "handoff_intent": prev.get("handoff_intent"),
        "handoff_params": dict(prev.get("handoff_params") or {}),
        "intent": prev.get("intent"),
        "response": prev.get("response"),
    }


def _norm_handoff_target(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    t = raw.strip().lower()
    return t if t in VALID_HANDOFF_TARGETS else None


def _handoff_output_format_extra() -> Dict[str, Any]:
    return {
        "handoff_to": "若需切换到其他模块则填 outline/writer/editor/navigator，否则填 null",
        "handoff_intent": "目标模块要执行的业务意图名称（与目标模块已有意图字符串完全一致）；无移交时填 null",
        "handoff_params": "移交给目标模块的参数对象，键与目标意图的 required_params 对齐；无移交时可为 {}",
    }


@dataclass
class ConversationState:
    novel_ctx: Optional[NovelContext] = None
    current_chapter: Optional[int] = None
    current_outline_state: Optional[OutlineSessionState] = None
    current_chapter_state: Optional[ChapterSessionState] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    """近期多轮对话摘要，供各智能体理解上下文（与 messages 技术日志并存）。"""
    dialogue_memory: List[Dict[str, str]] = field(default_factory=list)

    def clear_for_new_novel(self) -> None:
        self.current_chapter = None
        self.current_outline_state = None
        self.current_chapter_state = None
        self.messages.clear()
        self.dialogue_memory.clear()
        if self.novel_ctx is not None:
            from .history_manager import load_recent_dialogue

            self.dialogue_memory.extend(load_recent_dialogue(self.novel_ctx))


# 注入各智能体 system prompt 时的「近期对话」条数上限
DIALOGUE_MEMORY_MAX_TURNS = 16


class BaseAgent:
    def __init__(self, name: str, state: ConversationState, llm: Optional[BaseChatModel] = None):
        self.name = name
        self.state = state
        self.llm = llm or get_llm(temperature=0.2)

    def _invoke(self, prompt: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 LLM，让其按照约定的 JSON 输出格式返回。
        """
        sys = SystemMessage(content=json.dumps(prompt, ensure_ascii=False, indent=2))
        human = HumanMessage(content=str(prompt.get("user_input", "")))

        history_snippets: List[str] = []
        mem = self.state.dialogue_memory[-DIALOGUE_MEMORY_MAX_TURNS:]
        for turn in mem:
            role = str(turn.get("role") or "用户").strip() or "用户"
            text = str(turn.get("text") or "").strip()
            if text:
                history_snippets.append(f"{role}: {text}")

        if history_snippets:
            history_text = (
                "以下是近期对话记录（含用户与各角色），供你理解上下文与指代：\n\n"
                + "\n\n".join(history_snippets)
            )
            history_msg = SystemMessage(content=history_text)
            messages = [history_msg, sys, human]
        else:
            messages = [sys, human]

        resp = self.llm.invoke(messages)
        text = getattr(resp, "content", str(resp))
        text = str(text).strip()

        # 尝试从返回文本中截取合法 JSON
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            json_text = text[first : last + 1]
        else:
            json_text = text

        data: Dict[str, Any]
        try:
            parsed = json.loads(json_text)
            if isinstance(parsed, dict):
                data = parsed
            else:
                raise ValueError("顶层不是对象")
        except Exception:
            data = {
                "intent": "unknown",
                "params": {},
                "response": "抱歉，我没有正确理解你的意图，请尝试用更明确的说法描述你的需求。",
            }

        # 记录对话历史，便于后续调试与上下文扩展
        self.state.messages.append(
            {
                "agent": self.name,
                "prompt": prompt,
                "raw_response": text,
                "parsed": data,
            }
        )
        return data


# 主编派发的业务意图与下游智能体首跳意图的对应关系（main_chat 使用）
CHIEF_DELEGATION_MAP = {
    "创作剧情大纲": ("outline", "创建大纲"),
    "修改剧情大纲": ("outline", "修改大纲"),
    "撰写章节正文": ("writer", "根据大纲撰写正文"),
    "修改撰写中的正文": ("writer", "修改正文"),
    "编辑优化已保存章节": ("editor", "选择正文并修改"),
}

# 兼容旧版导航意图命名
_LEGACY_CHIEF_INTENT = {
    "创建大纲": "创作剧情大纲",
    "正文撰写": "撰写章节正文",
    "正文修改": "编辑优化已保存章节",
}


class ChiefEditorAgent(BaseAgent):
    """
    主编：与用户直接对话，识别意图、抽取参数；参数不足时追问；再委派编剧/写手/编辑执行。
    """

    def handle(self, user_input: str) -> Dict[str, Any]:
        ctx = self.state
        context = {
            "identity": "你是小说创作团队的主编，也是用户的主要对话对象",
            "task": (
                "理解用户自然语言，归类到下列意图之一；从用户话里抽取 params（如 chapter、"
                "requirements、prev_chapters、feedback、request）。"
                "「创作剧情大纲」必须识别两项：requirements（用户要求，无特别要求时字面填「无」）与 "
                "prev_chapters（正整数，读取前文摘要的章节数，必填）。任一项缺失则追问，不要编造。"
                "若信息不足不要编造，应在 response 里友好追问。"
                "凡涉及「修改撰写中的正文」「编辑优化已保存章节」，必须把用户说的具体改写方向完整写入 "
                "params.request；若用户只说「优化/改一下」等模糊话，intent 仍可归为对应项，但 params.request "
                "不要臆造，应在 response 中追问直至用户给出可执行要求。"
                "除「闲聊与说明」外，intent 必须使用下列名称之一（精确字符串）。"
            ),
            "context": (
                f"当前作品：{ctx.novel_ctx.novel_name if ctx.novel_ctx else '未选择'}，"
                f"当前章节上下文：{ctx.current_chapter or '未指定'}"
            ),
            "user_input": user_input,
            "intents": {
                "创作剧情大纲": {
                    "description": (
                        "委派编剧：从零生成剧情大纲；须抽取 chapter、requirements（无要求时填「无」）、"
                        "prev_chapters（正整数，必读前文摘要的章节数）"
                    ),
                    "required_params": ["chapter", "requirements", "prev_chapters"],
                    "optional_params": [],
                },
                "修改剧情大纲": {
                    "description": "委派编剧：在已有大纲（磁盘或本会话）上按用户意见修改",
                    "required_params": ["chapter"],
                    "optional_params": ["feedback"],
                },
                "撰写章节正文": {
                    "description": "委派写手：根据 plot 目录下该章大纲撰写正文草稿（会话内可继续改）",
                    "required_params": ["chapter"],
                    "optional_params": [],
                },
                "修改撰写中的正文": {
                    "description": (
                        "委派写手：修改当前会话里、尚未落盘的正文草稿；"
                        "params.request 必须是可执行的具体改写方向（不能仅为「优化」「改一下」等）"
                    ),
                    "required_params": ["request"],
                    "optional_params": ["chapter"],
                },
                "编辑优化已保存章节": {
                    "description": (
                        "委派编辑：按用户要求润色磁盘上已保存章节；"
                        "params.request 必须具体说明改什么、期望效果，否则应先追问用户"
                    ),
                    "required_params": ["chapter", "request"],
                    "optional_params": [],
                },
                "切换作品": {
                    "description": "用户要换另一部小说作品",
                    "required_params": [],
                    "optional_params": [],
                },
                "退出程序": {
                    "description": "结束对话模式",
                    "required_params": [],
                    "optional_params": [],
                },
                "闲聊与说明": {
                    "description": "问候、问你能做什么、与创作无关的闲聊；简要说明四大角色分工即可",
                    "required_params": [],
                    "optional_params": [],
                },
                "查看章节正文": {
                    "description": (
                        "用户只想阅读/预览磁盘上某章正文（chapter 目录下第 N 章 .txt），"
                        "不委派写手；与「撰写章节正文」区分"
                    ),
                    "required_params": ["chapter"],
                    "optional_params": [],
                },
                "查看剧情大纲": {
                    "description": (
                        "用户只想阅读/预览某章的剧情大纲文件（plot 目录下第 N 章_剧情大纲.txt），"
                        "不委派编剧修改"
                    ),
                    "required_params": ["chapter"],
                    "optional_params": [],
                },
            },
            "output_format": {
                "intent": "识别的意图名称（上表之一）",
                "params": {
                    "chapter": "整数章节号（多数创作类意图需要）",
                    "requirements": "创作剧情大纲/重新生成：用户要求；无则填字面「无」",
                    "prev_chapters": "创作剧情大纲/重新生成：正整数，用户指定的前文摘要章节数（必填）",
                    "feedback": "修改剧情大纲时的意见",
                    "request": "修改正文或编辑时的具体改写要求",
                },
                "response": "给用户的回复：委派前用一两句话确认理解；追问时写清缺什么",
            },
        }
        result = self._invoke(context)

        intent_raw = result.get("intent")
        params = dict(result.get("params") or {})
        if not isinstance(intent_raw, str):
            intent_raw = "闲聊与说明"
        intent = _LEGACY_CHIEF_INTENT.get(intent_raw.strip(), intent_raw.strip())

        if intent in CHIEF_DELEGATION_MAP:
            if intent == "创作剧情大纲":
                if "chapter" not in params:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": "要为第几章创作剧情大纲？请说章节号，例如「第 3 章」或只输入数字 3。",
                    }
                ok_outline, ask_outline = outline_create_requirements_ok(params)
                if not ok_outline:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": ask_outline,
                    }
            if intent == "修改剧情大纲" and "chapter" not in params:
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "要修改第几章的剧情大纲？请告诉我章节号。",
                }
            if intent == "撰写章节正文" and "chapter" not in params:
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "要写第几章正文？请告诉我章节号（需已有或可随后补充该章大纲）。",
                }
            if intent == "修改撰写中的正文":
                if "request" not in params:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": "希望怎么改当前正文？请具体说明，例如「对话更紧凑」「加强悬念」。",
                    }
                ok_req, ask_req = revision_request_is_specific(
                    params.get("request"), llm=self.llm
                )
                if not ok_req:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": ask_req,
                    }
            if intent == "编辑优化已保存章节":
                if "chapter" not in params:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": "要优化磁盘上第几章已保存的正文？请说章节号。",
                    }
                if "request" not in params:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": "希望从哪些方面优化这一章？例如节奏、文笔、信息密度等。",
                    }
                ok_ed, ask_ed = revision_request_is_specific(
                    params.get("request"), llm=self.llm
                )
                if not ok_ed:
                    return {
                        "intent": "unknown",
                        "params": params,
                        "response": ask_ed,
                    }

        if intent == "查看章节正文":
            if "chapter" not in params:
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "要查看第几章的正文？请说章节号，例如「第 3 章」或只输入数字 3。",
                }
            try:
                ch = int(params["chapter"])
            except (TypeError, ValueError):
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "章节号请用整数，例如 3。",
                }
            if ch < 1 or ch > 9999:
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "章节号应在 1～9999 之间。",
                }
            params["chapter"] = ch
            self.state.current_chapter = ch

        if intent == "查看剧情大纲":
            if "chapter" not in params:
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "要查看第几章的剧情大纲？请说章节号，例如「第 3 章」或只输入数字 3。",
                }
            try:
                ch = int(params["chapter"])
            except (TypeError, ValueError):
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "章节号请用整数，例如 3。",
                }
            if ch < 1 or ch > 9999:
                return {
                    "intent": "unknown",
                    "params": params,
                    "response": "章节号应在 1～9999 之间。",
                }
            params["chapter"] = ch
            self.state.current_chapter = ch

        out = dict(result)
        out["intent"] = intent
        out["params"] = params
        return out


NavigatorAgent = ChiefEditorAgent


class OutlineAgent(BaseAgent):
    """
    大纲创作智能体。
    """

    def _execute_intent(self, intent: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.state
        chapter = ctx.current_chapter or 1
        params = dict(params or {})

        if not intent:
            return {
                "intent": "unknown",
                "params": params,
                "response": "抱歉，未能识别具体意图，请重新说明。",
            }

        if intent in {"创建大纲", "重新生成大纲"}:
            if "chapter" not in params:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "请先告诉我要为第几章创建/重新生成大纲（例如：3）。",
                }
            ok_co, ask_co = outline_create_requirements_ok(params)
            if not ok_co:
                return {
                    "intent": intent,
                    "params": params,
                    "response": ask_co,
                }
            ch = int(params.get("chapter", chapter))
            self.state.current_chapter = ch
            ws = parse_prev_chapters_for_outline(params.get("prev_chapters"))
            if ws is None:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "前文章节数无效，请提供正整数。",
                }
            req = normalize_outline_user_requirements(params.get("requirements"))
            self.state.current_outline_state = generate_outline(
                self.state.novel_ctx,
                ch,
                user_requirements=req,
                window_size=ws,
            )
            verb = "已为" if intent == "创建大纲" else "已重新为"
            return {
                "intent": intent,
                "params": params,
                "response": f"{verb}第{ch}章生成大纲（参考前文章节摘要窗口：{ws} 章）。",
            }

        if intent == "修改大纲":
            ch = int(params.get("chapter", chapter))
            self.state.current_chapter = ch
            st_ex = self.state.current_outline_state
            if st_ex is not None and st_ex.chapter_index != ch:
                self.state.current_outline_state = None
            if not self.state.current_outline_state:
                plot_path = self.state.novel_ctx.novel_dir / "plot" / f"第{ch}章_剧情大纲.txt"
                if plot_path.exists():
                    text = plot_path.read_text(encoding="utf-8")
                    self.state.current_outline_state = OutlineSessionState(
                        novel_ctx=self.state.novel_ctx,
                        chapter_index=ch,
                        current_outline=text,
                        history=[text],
                    )
                else:
                    return {
                        "intent": intent,
                        "params": params,
                        "response": (
                            f"第{ch}章暂无已保存的剧情大纲文件。"
                            "请先创作该章大纲，或说明要修改的是哪一章。"
                        ),
                    }
            if "feedback" not in params or not str(params.get("feedback", "")).strip():
                return {
                    "intent": intent,
                    "params": params,
                    "response": (
                        f"已就绪第{ch}章大纲。"
                        "请具体说明修改方向（冲突、节奏、人物、伏笔等）。"
                    ),
                }
            fb = params.get("feedback", "")
            st = revise_outline(
                current_outline=self.state.current_outline_state.current_outline,
                novel_ctx=self.state.novel_ctx,
                chapter_index=self.state.current_chapter or ch,
                user_feedback=fb,
                previous_history=self.state.current_outline_state.history,
            )
            self.state.current_outline_state = st
            return {
                "intent": intent,
                "params": params,
                "response": "已根据你的反馈更新当前大纲。",
            }

        if intent == "满意当前大纲" and self.state.current_outline_state:
            path = save_outline(self.state.current_outline_state)
            return {
                "intent": intent,
                "params": params,
                "response": f"当前大纲已保存到文件：{path}",
            }
        if intent == "查看已有大纲" and self.state.current_outline_state:
            return {
                "intent": intent,
                "params": params,
                "response": self.state.current_outline_state.current_outline,
            }
        if intent == "查看大纲历史版本" and self.state.current_outline_state:
            history = self.state.current_outline_state.history or []
            if not history:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "当前会话内还没有任何大纲历史版本。",
                }
            lines = ["当前会话中的大纲历史版本："]
            for idx, ver in enumerate(history):
                preview = ver.strip().splitlines()[0] if ver.strip() else "(空内容)"
                if len(preview) > 60:
                    preview = preview[:60] + "..."
                lines.append(f"{idx}: {preview}")
            return {
                "intent": intent,
                "params": params,
                "response": "\n".join(lines),
            }
        if intent == "回滚大纲到版本" and self.state.current_outline_state:
            version_index = params.get("version_index")
            try:
                vi = int(version_index)
            except Exception:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "请提供要回滚的大纲版本编号（整数），例如 0 或 1。",
                }
            history = self.state.current_outline_state.history or []
            if vi < 0 or vi >= len(history):
                return {
                    "intent": intent,
                    "params": params,
                    "response": f"无效的版本编号 {vi}，当前可用版本范围为 0 ~ {len(history) - 1}。",
                }
            self.state.current_outline_state.current_outline = history[vi]
            return {
                "intent": intent,
                "params": params,
                "response": f"已将当前大纲回滚到会话内的第 {vi} 号版本。",
            }
        if intent == "返回导航":
            return {
                "intent": intent,
                "params": params,
                "response": "好的，已返回导航模式，你可以继续描述新的需求。",
            }

        return {
            "intent": intent or "unknown",
            "params": params,
            "response": "当前状态下无法完成该操作，请确认是否已有大纲会话或意图是否匹配。",
        }

    def _build_outline_prompt(self, user_input: str) -> Dict[str, Any]:
        ctx = self.state
        chapter = ctx.current_chapter or 1
        return {
            "identity": "你是创作团队中的「编剧」，只负责剧情大纲：新建、修改、保存与会话内版本管理",
            "task": (
                "识别用户意图，维护当前章节大纲。"
                "创建/重新生成大纲必须抽取 requirements（可为「无」）与正整数 prev_chapters（前文章节数，必填）。"
                "若用户要撰写/修改正文草稿或优化已保存章节，应使用移交类意图并填写 handoff 字段。"
            ),
            "context": f"当前作品：{ctx.novel_ctx.novel_name if ctx.novel_ctx else '未选择'}，当前章节：{chapter}，当前大纲内容：{(ctx.current_outline_state.current_outline[:50] + '...') if ctx.current_outline_state else '暂无'}",
            "user_input": user_input,
            "intents": {
                "创建大纲": {
                    "description": (
                        "为指定章节创建新大纲；须给出 requirements（用户要求，无则「无」）与 "
                        "prev_chapters（正整数：纳入剧情摘要的前文章节数，必填）"
                    ),
                    "required_params": ["chapter", "requirements", "prev_chapters"],
                    "optional_params": [],
                },
                "修改大纲": {
                    "description": "根据用户反馈修改当前大纲",
                    "required_params": ["feedback"],
                    "optional_params": [],
                },
                "重新生成大纲": {
                    "description": (
                        "放弃当前版本重新生成；须给出 requirements（无则「无」）与 prev_chapters（必填正整数）"
                    ),
                    "required_params": ["chapter", "requirements", "prev_chapters"],
                    "optional_params": [],
                },
                "满意当前大纲": {
                    "description": "对当前大纲满意并保存",
                    "required_params": [],
                    "optional_params": [],
                },
                "查看已有大纲": {
                    "description": "查看当前章节已生成的大纲内容",
                    "required_params": [],
                    "optional_params": [],
                },
                "查看大纲历史版本": {
                    "description": "查看当前会话中的大纲历史版本预览",
                    "required_params": [],
                    "optional_params": [],
                },
                "回滚大纲到版本": {
                    "description": "将当前大纲回滚到会话内的某个历史版本",
                    "required_params": ["version_index"],
                    "optional_params": [],
                },
                "移交_正文撰写": {
                    "description": "用户要根据大纲撰写某章正文（跳转正文撰写智能体）",
                    "required_params": ["chapter"],
                    "optional_params": [],
                    "handoff_to": "writer",
                    "handoff_intent": "根据大纲撰写正文",
                },
                "移交_正文修改": {
                    "description": "用户要优化/润色磁盘上已有章节正文（跳转正文修改智能体）",
                    "required_params": ["chapter", "request"],
                    "optional_params": [],
                    "handoff_to": "editor",
                    "handoff_intent": "选择正文并修改",
                },
                "返回导航": {
                    "description": "返回分类导航智能体",
                    "required_params": [],
                    "optional_params": [],
                },
            },
            "output_format": {
                "intent": "识别的意图名称（含移交_前缀的移交类意图）",
                "params": {"参数名": "值"},
                "response": "给用户的回复内容",
                **_handoff_output_format_extra(),
            },
        }

    def _maybe_build_handoff(
        self, parsed: Dict[str, Any], params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """若模型声明移交其他模块，返回移交结果字典；否则 None。"""
        ht = _norm_handoff_target(parsed.get("handoff_to"))
        if ht is None or ht == "outline":
            return None
        merged = {**params, **(parsed.get("handoff_params") or {})}
        hi = parsed.get("handoff_intent")
        if not isinstance(hi, str) or not hi.strip():
            if ht == "writer":
                hi = "根据大纲撰写正文"
            elif ht == "editor":
                hi = "选择正文并修改"
            elif ht == "navigator":
                hi = "返回导航"
            else:
                hi = ""
        hi = hi.strip()

        if ht == "navigator":
            return {
                "intent": "返回导航",
                "params": params,
                "response": parsed.get("response", "好的，已返回导航模式，你可以继续描述新的需求。"),
                "handoff_to": "navigator",
                "handoff_intent": hi,
                "handoff_params": merged,
            }

        if ht == "writer":
            if "chapter" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_正文撰写"),
                    "params": params,
                    "response": "请告诉我要撰写第几章正文（例如：3）。",
                }
            return {
                "intent": parsed.get("intent", "移交_正文撰写"),
                "params": params,
                "response": parsed.get("response", "已切换到正文撰写模块，正在根据大纲生成该章正文。"),
                "handoff_to": "writer",
                "handoff_intent": hi or "根据大纲撰写正文",
                "handoff_params": merged,
            }

        if ht == "editor":
            if "chapter" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_正文修改"),
                    "params": params,
                    "response": "请告诉我要优化第几章的正文（例如：2）。",
                }
            if "request" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_正文修改"),
                    "params": params,
                    "response": "请简单说明你希望如何优化这一章，例如“节奏更紧张、对话更简洁”。",
                }
            return {
                "intent": parsed.get("intent", "移交_正文修改"),
                "params": params,
                "response": parsed.get("response", "已切换到正文优化模块，正在处理该章。"),
                "handoff_to": "editor",
                "handoff_intent": hi or "选择正文并修改",
                "handoff_params": merged,
            }

        return None

    def handle(self, user_input: str, *, handoff: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if handoff is not None:
            hi = handoff.get("handoff_intent")
            if not isinstance(hi, str) or not hi.strip():
                return {
                    "intent": "unknown",
                    "params": {},
                    "response": "移交失败：缺少目标意图 handoff_intent。",
                }
            params = merge_handoff_params(handoff)
            return self._execute_intent(hi.strip(), params)

        ctx = self.state
        chapter = ctx.current_chapter or 1
        parsed = self._invoke(self._build_outline_prompt(user_input))
        intent = parsed.get("intent")
        params = parsed.get("params") or {}

        ho = self._maybe_build_handoff(parsed, params)
        if ho is not None:
            return ho

        return self._execute_intent(intent, params)


class ChapterWriterAgent(BaseAgent):
    """
    正文撰写智能体。
    """

    def _execute_intent(self, intent: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.state
        chapter = ctx.current_chapter or 1
        params = dict(params or {})

        if not intent:
            return {
                "intent": "unknown",
                "params": params,
                "response": "抱歉，未能识别具体意图，请重新说明。",
            }

        if intent in {"根据大纲撰写正文", "重新生成正文"} and "chapter" not in params:
            return {
                "intent": intent,
                "params": params,
                "response": "请告诉我是要撰写第几章的正文（例如：3）。",
            }
        if intent == "修改正文" and "request" not in params:
            return {
                "intent": intent,
                "params": params,
                "response": "请具体说明你希望如何修改当前正文，例如“再写得生动一点”。",
            }
        if intent == "修改正文" and "request" in params:
            ok_rw, ask_rw = revision_request_is_specific(
                params.get("request"), llm=self.llm
            )
            if not ok_rw:
                return {
                    "intent": intent,
                    "params": params,
                    "response": ask_rw,
                }

        if intent in ("根据大纲撰写正文", "重新生成正文"):
            ch = int(params.get("chapter", chapter))
            self.state.current_chapter = ch
            plot_dir = self.state.novel_ctx.novel_dir / "plot"
            path = plot_dir / f"第{ch}章_剧情大纲.txt"
            if path.exists():
                outline_text = path.read_text(encoding="utf-8")
            else:
                outline_text = "（提示：当前未找到正式大纲，将根据已有信息自由发挥撰写。）"
            self.state.current_chapter_state = generate_chapter(
                self.state.novel_ctx,
                chapter_index=ch,
                outline_text=outline_text,
            )
            msg = "好的，已为第{}章生成正文草稿。".format(ch)
            body = self.state.current_chapter_state.current_text
            return {
                "intent": intent,
                "params": params,
                "response": _writer_reply_with_full_text(msg, ch, body),
            }
        if intent == "修改正文" and not self.state.current_chapter_state:
            return {
                "intent": intent,
                "params": params,
                "response": (
                    "当前没有进行中的正文草稿会话。你可以："
                    "① 先让我「根据大纲撰写正文」生成草稿；"
                    "② 若已保存到文件，请通过主编选择「编辑优化已保存章节」。"
                ),
            }
        if intent == "修改正文" and self.state.current_chapter_state and "chapter" in params:
            try:
                ch_w = int(params["chapter"])
            except (TypeError, ValueError):
                ch_w = self.state.current_chapter_state.chapter_index
            if ch_w != self.state.current_chapter_state.chapter_index:
                return {
                    "intent": intent,
                    "params": params,
                    "response": (
                        f"当前草稿是第{self.state.current_chapter_state.chapter_index}章，"
                        f"与第{ch_w}章不一致。请先撰写该章草稿，或说明要改的是当前会话中的正文。"
                    ),
                }
            self.state.current_chapter = ch_w
        if intent == "修改正文" and self.state.current_chapter_state:
            req = params.get("request", "")
            st = revise_chapter(
                current_text=self.state.current_chapter_state.current_text,
                novel_ctx=self.state.novel_ctx,
                chapter_index=self.state.current_chapter or 1,
                user_requirements=req,
                previous_history=self.state.current_chapter_state.history,
            )
            self.state.current_chapter_state = st
            ch_done = self.state.current_chapter_state.chapter_index
            msg = "已根据你的要求优化当前正文。"
            body = self.state.current_chapter_state.current_text
            return {
                "intent": intent,
                "params": params,
                "response": _writer_reply_with_full_text(msg, ch_done, body),
            }
        if intent == "满意当前正文" and self.state.current_chapter_state:
            path = save_chapter(self.state.current_chapter_state, source_agent="writer")
            return {
                "intent": intent,
                "params": params,
                "response": f"当前正文已保存到文件：{path}",
            }
        if intent == "查看正文历史版本" and self.state.current_chapter_state:
            history = self.state.current_chapter_state.history or []
            if not history:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "当前会话内还没有任何正文历史版本。",
                }
            lines = ["当前会话中的正文历史版本："]
            for idx, ver in enumerate(history):
                preview = ver.strip().splitlines()[0] if ver.strip() else "(空内容)"
                if len(preview) > 60:
                    preview = preview[:60] + "..."
                lines.append(f"{idx}: {preview}")
            return {
                "intent": intent,
                "params": params,
                "response": "\n".join(lines),
            }
        if intent == "查看当前正文":
            if not self.state.current_chapter_state:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "当前还没有正文会话。请先用“根据大纲撰写正文”生成正文，或用“修改正文”产生一个新版本。",
                }
            return {
                "intent": intent,
                "params": params,
                # 直接输出全文：不要截断预览
                "response": self.state.current_chapter_state.current_text,
            }
        if intent == "回滚正文到版本" and self.state.current_chapter_state:
            version_index = params.get("version_index")
            try:
                vi = int(version_index)
            except Exception:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "请提供要回滚的正文版本编号（整数），例如 0 或 1。",
                }
            history = self.state.current_chapter_state.history or []
            if vi < 0 or vi >= len(history):
                return {
                    "intent": intent,
                    "params": params,
                    "response": f"无效的版本编号 {vi}，当前可用版本范围为 0 ~ {len(history) - 1}。",
                }
            self.state.current_chapter_state.current_text = history[vi]
            return {
                "intent": intent,
                "params": params,
                "response": f"已将当前正文回滚到会话内的第 {vi} 号版本。",
            }
        if intent == "返回导航":
            return {
                "intent": intent,
                "params": params,
                "response": "好的，已返回导航模式。",
            }

        return {
            "intent": intent or "unknown",
            "params": params,
            "response": "当前状态下无法完成该操作，请确认是否已有正文会话或意图是否匹配。",
        }

    def _build_writer_prompt(self, user_input: str) -> Dict[str, Any]:
        ctx = self.state
        chapter = ctx.current_chapter or 1
        return {
            "identity": "你是创作团队中的「写手」，负责根据大纲撰写章节正文草稿，并在会话内多轮修改（未保存前）",
            "task": "识别用户意图，撰写或修改会话内正文。若用户要改大纲或润色磁盘上已保存文件，应移交编剧或编辑。",
            "context": f"当前作品：{ctx.novel_ctx.novel_name if ctx.novel_ctx else '未选择'}，当前章节：{chapter}",
            "user_input": user_input,
            "intents": {
                "根据大纲撰写正文": {
                    "description": "根据指定章节的大纲撰写正文",
                    "required_params": ["chapter"],
                    "optional_params": [],
                },
                "修改正文": {
                    "description": "根据反馈修改当前会话正文；request 须为具体可执行要求，不能仅为「优化一下」等空话",
                    "required_params": ["request"],
                    "optional_params": [],
                },
                "重新生成正文": {
                    "description": "放弃当前版本，重新根据大纲生成正文",
                    "required_params": ["chapter"],
                    "optional_params": [],
                },
                "满意当前正文": {
                    "description": "对当前正文满意并保存",
                    "required_params": [],
                    "optional_params": [],
                },
                "查看当前正文": {
                    "description": "直接输出当前会话中的正文全文（不是历史版本列表/预览）",
                    "required_params": [],
                    "optional_params": [],
                },
                "查看正文历史版本": {
                    "description": "查看当前会话中的正文历史版本预览",
                    "required_params": [],
                    "optional_params": [],
                },
                "回滚正文到版本": {
                    "description": "将当前正文回滚到会话内的某个历史版本",
                    "required_params": ["version_index"],
                    "optional_params": [],
                },
                "移交_大纲创作": {
                    "description": "用户要为某章创建新大纲（跳转编剧）；须含 requirements（无则「无」）与 prev_chapters",
                    "required_params": ["chapter", "requirements", "prev_chapters"],
                    "optional_params": [],
                    "handoff_to": "outline",
                    "handoff_intent": "创建大纲",
                },
                "移交_大纲修改": {
                    "description": "用户要修改当前会话中的大纲（跳转大纲智能体，需已有大纲会话）",
                    "required_params": ["feedback"],
                    "optional_params": [],
                    "handoff_to": "outline",
                    "handoff_intent": "修改大纲",
                },
                "移交_正文修改": {
                    "description": "用户要优化磁盘上已有章节正文（跳转正文修改智能体）",
                    "required_params": ["chapter", "request"],
                    "optional_params": [],
                    "handoff_to": "editor",
                    "handoff_intent": "选择正文并修改",
                },
                "返回导航": {
                    "description": "返回分类导航智能体",
                    "required_params": [],
                    "optional_params": [],
                },
            },
            "output_format": {
                "intent": "识别的意图名称",
                "params": {"参数名": "值"},
                "response": "给用户的回复内容",
                **_handoff_output_format_extra(),
            },
        }

    def _maybe_build_handoff(
        self, parsed: Dict[str, Any], params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        ht = _norm_handoff_target(parsed.get("handoff_to"))
        if ht is None or ht == "writer":
            return None
        merged = {**params, **(parsed.get("handoff_params") or {})}
        hi = parsed.get("handoff_intent")
        if isinstance(hi, str) and hi.strip():
            hi = hi.strip()
        else:
            hi = ""

        if ht == "navigator":
            return {
                "intent": "返回导航",
                "params": params,
                "response": parsed.get("response", "好的，已返回导航模式。"),
                "handoff_to": "navigator",
                "handoff_intent": hi or "返回导航",
                "handoff_params": merged,
            }

        if ht == "outline":
            if hi == "修改大纲":
                if "feedback" not in merged:
                    return {
                        "intent": parsed.get("intent", "移交_大纲修改"),
                        "params": params,
                        "response": "请说明希望如何修改大纲。",
                    }
                return {
                    "intent": parsed.get("intent", "移交_大纲修改"),
                    "params": params,
                    "response": parsed.get("response", "已切换到大纲模块，正在按你的意见修改大纲。"),
                    "handoff_to": "outline",
                    "handoff_intent": "修改大纲",
                    "handoff_params": merged,
                }
            # 创建 / 重新生成大纲
            if "chapter" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_大纲创作"),
                    "params": params,
                    "response": "请告诉我要为第几章创建大纲（例如：3）。",
                }
            ok_wo, ask_wo = outline_create_requirements_ok(merged)
            if not ok_wo:
                return {
                    "intent": parsed.get("intent", "移交_大纲创作"),
                    "params": params,
                    "response": ask_wo,
                }
            if hi == "重新生成大纲":
                eff_hi = "重新生成大纲"
            else:
                eff_hi = "创建大纲"
            return {
                "intent": parsed.get("intent", "移交_大纲创作"),
                "params": params,
                "response": parsed.get("response", "已切换到大纲模块，正在处理。"),
                "handoff_to": "outline",
                "handoff_intent": eff_hi,
                "handoff_params": merged,
            }

        if ht == "editor":
            if "chapter" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_正文修改"),
                    "params": params,
                    "response": "请告诉我要优化第几章的正文。",
                }
            if "request" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_正文修改"),
                    "params": params,
                    "response": "请说明希望如何优化该章正文。",
                }
            return {
                "intent": parsed.get("intent", "移交_正文修改"),
                "params": params,
                "response": parsed.get("response", "已切换到正文优化模块。"),
                "handoff_to": "editor",
                "handoff_intent": hi or "选择正文并修改",
                "handoff_params": merged,
            }

        return None

    def handle(self, user_input: str, *, handoff: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if handoff is not None:
            hi = handoff.get("handoff_intent")
            if not isinstance(hi, str) or not hi.strip():
                return {
                    "intent": "unknown",
                    "params": {},
                    "response": "移交失败：缺少目标意图 handoff_intent。",
                }
            params = merge_handoff_params(handoff)
            return self._execute_intent(hi.strip(), params)

        parsed = self._invoke(self._build_writer_prompt(user_input))
        intent = parsed.get("intent")
        params = parsed.get("params") or {}

        ho = self._maybe_build_handoff(parsed, params)
        if ho is not None:
            return ho

        return self._execute_intent(intent, params)


class ChapterEditorAgent(BaseAgent):
    """
    正文优化修改智能体。
    """

    def _execute_intent(self, intent: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
        ctx = self.state
        chapter = ctx.current_chapter or 1
        params = dict(params or {})

        if not intent:
            return {
                "intent": "unknown",
                "params": params,
                "response": "抱歉，未能识别具体意图，请重新说明。",
            }

        if intent == "选择正文并修改":
            if "chapter" not in params:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "请告诉我是要修改第几章的正文（例如：2）。",
                }
            if "request" not in params:
                return {
                    "intent": intent,
                    "params": params,
                    "response": "请简单说明你希望如何优化这一章，例如“节奏更紧张、对话更简洁”。",
                }
            ok_sel, ask_sel = revision_request_is_specific(
                params.get("request"), llm=self.llm
            )
            if not ok_sel:
                return {
                    "intent": intent,
                    "params": params,
                    "response": ask_sel,
                }

        if intent == "优化修改正文" and "request" not in params:
            return {
                "intent": intent,
                "params": params,
                "response": "请继续说明这一次你希望从哪些方面优化当前版本正文。",
            }
        if intent == "优化修改正文" and self.state.current_chapter_state and "request" in params:
            ok_it, ask_it = revision_request_is_specific(
                params.get("request"), llm=self.llm
            )
            if not ok_it:
                return {
                    "intent": intent,
                    "params": params,
                    "response": ask_it,
                }

        if intent == "选择正文并修改":
            ch = int(params.get("chapter", chapter))
            self.state.current_chapter = ch
            req = params.get("request", "")
            st = optimize_chapter(
                novel_ctx=self.state.novel_ctx,
                chapter_index=ch,
                user_requirements=req,
            )
            self.state.current_chapter_state = st
            return {
                "intent": intent,
                "params": params,
                "response": "已根据你的要求生成优化后的章节版本。",
            }
        if intent == "优化修改正文" and self.state.current_chapter_state:
            req = params.get("request", "")
            st = iterate_optimization(
                current_text=self.state.current_chapter_state.current_text,
                novel_ctx=self.state.novel_ctx,
                chapter_index=self.state.current_chapter or 1,
                user_requirements=req,
            )
            self.state.current_chapter_state = st
            return {
                "intent": intent,
                "params": params,
                "response": "已根据你的进一步要求继续优化正文。",
            }
        if intent == "满意当前正文" and self.state.current_chapter_state:
            path = save_optimized_chapter(self.state.current_chapter_state)
            return {
                "intent": intent,
                "params": params,
                "response": f"当前优化后的正文已保存到文件：{path}",
            }
        if intent == "返回导航":
            return {
                "intent": intent,
                "params": params,
                "response": "好的，已返回导航模式。",
            }

        return {
            "intent": intent or "unknown",
            "params": params,
            "response": "当前状态下无法完成该操作，请确认是否已有优化会话或意图是否匹配。",
        }

    def _build_editor_prompt(self, user_input: str) -> Dict[str, Any]:
        ctx = self.state
        chapter = ctx.current_chapter or 1
        return {
            "identity": "你是创作团队中的「编辑」，负责读取磁盘上已保存章节、按作者要求润色优化并可落盘",
            "task": "识别意图，优化已保存正文。若用户要从零写草稿或改大纲，应移交写手或编剧。",
            "context": f"当前作品：{ctx.novel_ctx.novel_name if ctx.novel_ctx else '未选择'}，当前章节：{chapter}",
            "user_input": user_input,
            "intents": {
                "选择正文并修改": {
                    "description": "选择一章已保存正文并优化；request 须具体说明改动方向与期望效果",
                    "required_params": ["chapter", "request"],
                    "optional_params": [],
                },
                "优化修改正文": {
                    "description": "在当前优化版本上继续改；request 须具体，不可过于笼统",
                    "required_params": ["request"],
                    "optional_params": [],
                },
                "满意当前正文": {
                    "description": "对当前优化版本满意并保存",
                    "required_params": [],
                    "optional_params": [],
                },
                "移交_大纲创作": {
                    "description": "用户要为某章创建新大纲（跳转编剧）；须含 requirements（无则「无」）与 prev_chapters",
                    "required_params": ["chapter", "requirements", "prev_chapters"],
                    "optional_params": [],
                    "handoff_to": "outline",
                    "handoff_intent": "创建大纲",
                },
                "移交_正文撰写": {
                    "description": "用户要根据大纲撰写某章正文（跳转正文撰写智能体）",
                    "required_params": ["chapter"],
                    "optional_params": [],
                    "handoff_to": "writer",
                    "handoff_intent": "根据大纲撰写正文",
                },
                "返回导航": {
                    "description": "返回分类导航智能体",
                    "required_params": [],
                    "optional_params": [],
                },
            },
            "output_format": {
                "intent": "识别的意图名称",
                "params": {"参数名": "值"},
                "response": "给用户的回复内容",
                **_handoff_output_format_extra(),
            },
        }

    def _maybe_build_handoff(
        self, parsed: Dict[str, Any], params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        ht = _norm_handoff_target(parsed.get("handoff_to"))
        if ht is None or ht == "editor":
            return None
        merged = {**params, **(parsed.get("handoff_params") or {})}
        hi = parsed.get("handoff_intent")
        if isinstance(hi, str) and hi.strip():
            hi = hi.strip()
        else:
            hi = ""

        if ht == "navigator":
            return {
                "intent": "返回导航",
                "params": params,
                "response": parsed.get("response", "好的，已返回导航模式。"),
                "handoff_to": "navigator",
                "handoff_intent": hi or "返回导航",
                "handoff_params": merged,
            }

        if ht == "outline":
            if "chapter" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_大纲创作"),
                    "params": params,
                    "response": "请告诉我要为第几章创建大纲。",
                }
            ok_edo, ask_edo = outline_create_requirements_ok(merged)
            if not ok_edo:
                return {
                    "intent": parsed.get("intent", "移交_大纲创作"),
                    "params": params,
                    "response": ask_edo,
                }
            return {
                "intent": parsed.get("intent", "移交_大纲创作"),
                "params": params,
                "response": parsed.get("response", "已切换到大纲模块。"),
                "handoff_to": "outline",
                "handoff_intent": hi or "创建大纲",
                "handoff_params": merged,
            }

        if ht == "writer":
            if "chapter" not in merged:
                return {
                    "intent": parsed.get("intent", "移交_正文撰写"),
                    "params": params,
                    "response": "请告诉我要撰写第几章正文。",
                }
            return {
                "intent": parsed.get("intent", "移交_正文撰写"),
                "params": params,
                "response": parsed.get("response", "已切换到正文撰写模块。"),
                "handoff_to": "writer",
                "handoff_intent": hi or "根据大纲撰写正文",
                "handoff_params": merged,
            }

        return None

    def handle(self, user_input: str, *, handoff: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if handoff is not None:
            hi = handoff.get("handoff_intent")
            if not isinstance(hi, str) or not hi.strip():
                return {
                    "intent": "unknown",
                    "params": {},
                    "response": "移交失败：缺少目标意图 handoff_intent。",
                }
            params = merge_handoff_params(handoff)
            return self._execute_intent(hi.strip(), params)

        parsed = self._invoke(self._build_editor_prompt(user_input))
        intent = parsed.get("intent")
        params = parsed.get("params") or {}

        ho = self._maybe_build_handoff(parsed, params)
        if ho is not None:
            return ho

        return self._execute_intent(intent, params)


__all__ = [
    "ConversationState",
    "ChiefEditorAgent",
    "NavigatorAgent",
    "OutlineAgent",
    "ChapterWriterAgent",
    "ChapterEditorAgent",
    "CHIEF_DELEGATION_MAP",
    "DIALOGUE_MEMORY_MAX_TURNS",
    "HANDOFF_MAX_HOPS",
    "merge_handoff_params",
    "normalize_outline_user_requirements",
    "revision_request_is_specific",
    "sanitize_handoff_for_target",
    "VALID_HANDOFF_TARGETS",
]
