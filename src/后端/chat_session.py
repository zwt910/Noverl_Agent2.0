from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from 后端.chat_agents import (
    CHIEF_DELEGATION_MAP,
    HANDOFF_MAX_HOPS,
    ChapterEditorAgent,
    ChapterWriterAgent,
    ChiefEditorAgent,
    ConversationState,
    OutlineAgent,
    outline_create_requirements_ok,
    sanitize_handoff_for_target,
)
from 后端.history_manager import append_dialogue_turn, load_recent_dialogue
from 后端.novel_manager import NovelContext, create_novel, list_novels, switch_novel

AGENT_DISPLAY_ROLE = {
    "navigator": "主编",
    "outline": "编剧",
    "writer": "写手",
    "editor": "编辑",
}

WELCOME_HINT = (
    "团队：主编对接需求；编剧管大纲；写手管正文草稿；编辑管已保存章节润色。\n"
    "创作剧情大纲请说明：第几章、用户要求（无则说「无」）、读取前文的章节数（正整数）。\n"
    "可直接让主编「查看第 N 章正文」或「查看第 N 章剧情大纲」，中间栏会打开对应文件。"
)


def _creative_preview_payload(
    state: ConversationState,
    last_agent_key: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    在编剧 / 写手 / 编辑 产出可落盘的长文本后，供 Web 端「对话创作」预览条使用。
    last_agent_key 为 agents 字典键：outline / writer / editor。
    """
    if not last_agent_key or state.novel_ctx is None:
        return None
    if last_agent_key == "outline" and state.current_outline_state is not None:
        text = (state.current_outline_state.current_outline or "").strip()
        if len(text) < 40:
            return None
        ch = int(state.current_outline_state.chapter_index)
        return {
            "role": "编剧",
            "kind": "outline",
            "chapter": ch,
            "filename": f"第{ch}章_剧情大纲.txt",
            "content": state.current_outline_state.current_outline,
        }
    if last_agent_key == "writer" and state.current_chapter_state is not None:
        text = (state.current_chapter_state.current_text or "").strip()
        if len(text) < 80:
            return None
        ch = int(state.current_chapter_state.chapter_index)
        return {
            "role": "写手",
            "kind": "chapter",
            "chapter": ch,
            "filename": f"第{ch}章.txt",
            "content": state.current_chapter_state.current_text,
        }
    if last_agent_key == "editor" and state.current_chapter_state is not None:
        text = (state.current_chapter_state.current_text or "").strip()
        if len(text) < 80:
            return None
        ch = int(state.current_chapter_state.chapter_index)
        return {
            "role": "编辑",
            "kind": "chapter",
            "chapter": ch,
            "filename": f"第{ch}章.txt",
            "content": state.current_chapter_state.current_text,
        }
    return None


def _record_turn(state: ConversationState, role: str, text: str) -> None:
    t = (text or "").strip()
    if not t:
        return
    state.dialogue_memory.append({"role": role, "text": t})
    if state.novel_ctx is not None:
        append_dialogue_turn(state.novel_ctx, role, t)


def _run_sub_agent_with_handoff(
    agents: Dict[str, Any],
    start_key: str,
    user_input: str,
) -> Tuple[str, List[Tuple[str, str]]]:
    lines: List[Tuple[str, str]] = []
    a = start_key
    r: Dict[str, Any] = agents[a].handle(user_input)
    lines.append((a, r.get("response", "")))

    if r.get("intent") == "返回导航" or r.get("handoff_to") == "navigator":
        return "navigator", lines

    hops = 0
    while r.get("handoff_to") in ("outline", "writer", "editor") and hops < HANDOFF_MAX_HOPS:
        hops += 1
        to = r["handoff_to"]
        a = to
        r = agents[to].handle("", handoff=sanitize_handoff_for_target(r))
        lines.append((a, r.get("response", "")))
        if r.get("intent") == "返回导航" or r.get("handoff_to") == "navigator":
            return "navigator", lines

    return a, lines


def _delegate_from_chief(
    agents: Dict[str, Any],
    start_key: str,
    handoff_intent: str,
    params: Dict[str, Any],
) -> Tuple[str, List[Tuple[str, str]]]:
    lines: List[Tuple[str, str]] = []
    ho = {
        "handoff_intent": handoff_intent,
        "params": dict(params or {}),
        "handoff_params": {},
    }
    a = start_key
    r: Dict[str, Any] = agents[a].handle("", handoff=ho)
    lines.append((a, r.get("response", "")))

    if r.get("intent") == "返回导航" or r.get("handoff_to") == "navigator":
        return "navigator", lines

    hops = 0
    while r.get("handoff_to") in ("outline", "writer", "editor") and hops < HANDOFF_MAX_HOPS:
        hops += 1
        to = r["handoff_to"]
        a = to
        r = agents[to].handle("", handoff=sanitize_handoff_for_target(r))
        lines.append((a, r.get("response", "")))
        if r.get("intent") == "返回导航" or r.get("handoff_to") == "navigator":
            return "navigator", lines

    return a, lines


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class TurnResult:
    messages: List[ChatMessage] = field(default_factory=list)
    active_agent: str = "navigator"
    novel_name: str = ""
    needs_novel_picker: bool = False
    session_ended: bool = False


# iter_chat_events 产出：进度、气泡、Web 创作预览载荷、结束元数据
ChatStreamEvent = Union[
    Tuple[str, str],  # ("progress", text)
    Tuple[str, ChatMessage],  # ("message", ChatMessage)
    Tuple[str, Dict[str, Any]],  # ("done", meta) | ("creative_preview", payload)
]


class NovelChatSession:
    """封装 main_chat 的对话状态机，供 CLI 与 Web 共用。"""

    def __init__(self, novel_ctx: NovelContext) -> None:
        self.state = ConversationState(novel_ctx=novel_ctx)
        self.state.dialogue_memory.extend(load_recent_dialogue(novel_ctx))
        self.chief = ChiefEditorAgent("navigator", self.state)
        self.outline_agent = OutlineAgent("outline", self.state)
        self.writer_agent = ChapterWriterAgent("writer", self.state)
        self.editor_agent = ChapterEditorAgent("editor", self.state)
        self.agents: Dict[str, Any] = {
            "navigator": self.chief,
            "outline": self.outline_agent,
            "writer": self.writer_agent,
            "editor": self.editor_agent,
        }
        self.active: str = "navigator"

    @property
    def novel_name(self) -> str:
        return self.state.novel_ctx.novel_name if self.state.novel_ctx else ""

    def apply_novel_context(self, novel_ctx: NovelContext) -> None:
        self.state.novel_ctx = novel_ctx
        self.state.clear_for_new_novel()
        self.active = "navigator"

    def iter_chat_events(self, user: str) -> Iterator[ChatStreamEvent]:
        """供 Web SSE 逐步推送：进度、各角色气泡、done 元数据。"""
        out = TurnResult(active_agent=self.active, novel_name=self.novel_name)
        user = (user or "").strip()
        if not user:
            yield (
                "done",
                {
                    "active_agent": out.active_agent,
                    "novel_name": out.novel_name,
                    "needs_novel_picker": out.needs_novel_picker,
                    "session_ended": out.session_ended,
                },
            )
            return

        if user in ("退出", "exit", "quit"):
            cm = ChatMessage("系统", "对话已结束，未保存的草稿仍在当前会话内存中。")
            out.messages.append(cm)
            out.session_ended = True
            yield ("message", cm)
            yield (
                "done",
                {
                    "active_agent": out.active_agent,
                    "novel_name": out.novel_name,
                    "needs_novel_picker": out.needs_novel_picker,
                    "session_ended": out.session_ended,
                },
            )
            return

        yield ("progress", "正在处理…")
        _record_turn(self.state, "用户", user)

        if self.active == "navigator":
            yield ("progress", "主编正在理解您的意图（调用模型中）…")
            result = self.chief.handle(user)
            intent = result.get("intent")
            resp = result.get("response", "")

            if intent == "切换作品":
                text = resp or "请选择要进入的作品。"
                _record_turn(self.state, "主编", text)
                cm = ChatMessage("主编", text)
                out.messages.append(cm)
                yield ("message", cm)
                out.needs_novel_picker = True
                out.active_agent = self.active
                yield (
                    "done",
                    {
                        "active_agent": out.active_agent,
                        "novel_name": out.novel_name,
                        "needs_novel_picker": out.needs_novel_picker,
                        "session_ended": out.session_ended,
                    },
                )
                return

            if intent == "退出程序":
                _record_turn(self.state, "主编", "好的，再见。")
                cm = ChatMessage("主编", "好的，再见。")
                out.messages.append(cm)
                yield ("message", cm)
                out.session_ended = True
                out.active_agent = self.active
                yield (
                    "done",
                    {
                        "active_agent": out.active_agent,
                        "novel_name": out.novel_name,
                        "needs_novel_picker": out.needs_novel_picker,
                        "session_ended": out.session_ended,
                    },
                )
                return

            if intent == "查看章节正文":
                params = result.get("params") or {}
                ch = int(params["chapter"])
                text = (resp or "").strip() or f"已为你打开第 {ch} 章正文预览，可在中间栏查看与编辑。"
                _record_turn(self.state, "主编", text)
                cm = ChatMessage("主编", text)
                out.messages.append(cm)
                yield ("message", cm)
                yield (
                    "preview_navigate",
                    {"tab": "chapters", "filename": f"第{ch}章.txt"},
                )
                self.active = "navigator"
                out.active_agent = self.active
                yield (
                    "done",
                    {
                        "active_agent": out.active_agent,
                        "novel_name": out.novel_name,
                        "needs_novel_picker": out.needs_novel_picker,
                        "session_ended": out.session_ended,
                    },
                )
                return

            if intent == "查看剧情大纲":
                params = result.get("params") or {}
                ch = int(params["chapter"])
                text = (resp or "").strip() or f"已为你打开第 {ch} 章剧情大纲预览，可在中间栏查看与编辑。"
                _record_turn(self.state, "主编", text)
                cm = ChatMessage("主编", text)
                out.messages.append(cm)
                yield ("message", cm)
                yield (
                    "preview_navigate",
                    {"tab": "outlines", "filename": f"第{ch}章_剧情大纲.txt"},
                )
                self.active = "navigator"
                out.active_agent = self.active
                yield (
                    "done",
                    {
                        "active_agent": out.active_agent,
                        "novel_name": out.novel_name,
                        "needs_novel_picker": out.needs_novel_picker,
                        "session_ended": out.session_ended,
                    },
                )
                return

            if intent in CHIEF_DELEGATION_MAP:
                _record_turn(self.state, "主编", resp)
                cm = ChatMessage("主编", resp)
                out.messages.append(cm)
                yield ("message", cm)
                target, sub_intent = CHIEF_DELEGATION_MAP[intent]
                params = result.get("params") or {}
                yield (
                    "progress",
                    "编剧/写手/编辑处理中，可能需要数十秒，请稍候…",
                )
                self.active, line_pairs = _delegate_from_chief(
                    self.agents, target, sub_intent, params
                )
                for key, msg in line_pairs:
                    role = AGENT_DISPLAY_ROLE.get(key, key)
                    sub_cm = ChatMessage(role, msg)
                    out.messages.append(sub_cm)
                    _record_turn(self.state, role, msg)
                    yield ("message", sub_cm)
                out.active_agent = self.active
                last_key = line_pairs[-1][0] if line_pairs else None
                cp = _creative_preview_payload(self.state, last_key)
                if cp is not None:
                    yield ("creative_preview", cp)
                yield (
                    "done",
                    {
                        "active_agent": out.active_agent,
                        "novel_name": out.novel_name,
                        "needs_novel_picker": out.needs_novel_picker,
                        "session_ended": out.session_ended,
                    },
                )
                return

            _record_turn(self.state, "主编", resp)
            cm = ChatMessage("主编", resp)
            out.messages.append(cm)
            yield ("message", cm)
            self.active = "navigator"
            out.active_agent = self.active
            yield (
                "done",
                {
                    "active_agent": out.active_agent,
                    "novel_name": out.novel_name,
                    "needs_novel_picker": out.needs_novel_picker,
                    "session_ended": out.session_ended,
                },
            )
            return

        yield ("progress", "当前模块处理中（调用模型中）…")
        self.active, line_pairs = _run_sub_agent_with_handoff(
            self.agents, self.active, user
        )
        for key, msg in line_pairs:
            role = AGENT_DISPLAY_ROLE.get(key, key)
            sub_cm = ChatMessage(role, msg)
            out.messages.append(sub_cm)
            _record_turn(self.state, role, msg)
            yield ("message", sub_cm)
        out.active_agent = self.active
        last_key = line_pairs[-1][0] if line_pairs else None
        cp = _creative_preview_payload(self.state, last_key)
        if cp is not None:
            yield ("creative_preview", cp)
        yield (
            "done",
            {
                "active_agent": out.active_agent,
                "novel_name": out.novel_name,
                "needs_novel_picker": out.needs_novel_picker,
                "session_ended": out.session_ended,
            },
        )

    def handle_user_message(self, user: str) -> TurnResult:
        messages: List[ChatMessage] = []
        meta: Dict[str, Any] = {}
        for kind, payload in self.iter_chat_events(user):
            if kind == "message":
                messages.append(payload)
            elif kind == "done":
                meta = payload
        return TurnResult(
            messages=messages,
            active_agent=meta.get("active_agent", "navigator"),
            novel_name=meta.get("novel_name", self.novel_name),
            needs_novel_picker=bool(meta.get("needs_novel_picker")),
            session_ended=bool(meta.get("session_ended")),
        )

    def handle_outline_wizard(
        self,
        chapter: int,
        requirements: str,
        prev_chapters: int,
    ) -> TurnResult:
        """
        用表单参数直接走「创作剧情大纲」委派链，跳过主编从自然语言抽取参数。
        requirements 可为空，视为无额外要求（与字面「无」等价）。
        """
        messages: List[ChatMessage] = []
        meta: Dict[str, Any] = {}
        for kind, payload in self.iter_outline_wizard_events(chapter, requirements, prev_chapters):
            if kind == "message":
                messages.append(payload)
            elif kind == "done":
                meta = payload
        return TurnResult(
            messages=messages,
            active_agent=meta.get("active_agent", "navigator"),
            novel_name=meta.get("novel_name", self.novel_name),
            needs_novel_picker=bool(meta.get("needs_novel_picker")),
            session_ended=bool(meta.get("session_ended")),
        )

    def iter_outline_wizard_events(
        self,
        chapter: int,
        requirements: str,
        prev_chapters: int,
    ) -> Iterator[ChatStreamEvent]:
        out = TurnResult(active_agent=self.active, novel_name=self.novel_name)
        if self.state.novel_ctx is None:
            cm = ChatMessage("系统", "未绑定作品，请先选择作品。")
            yield ("message", cm)
            yield (
                "done",
                {
                    "active_agent": self.active,
                    "novel_name": self.novel_name,
                    "needs_novel_picker": True,
                    "session_ended": False,
                },
            )
            return

        req_key = (requirements or "").strip() or "无"
        params: Dict[str, Any] = {
            "chapter": chapter,
            "requirements": req_key,
            "prev_chapters": prev_chapters,
        }
        ok, ask = outline_create_requirements_ok(params)
        summary = (
            f"[大纲向导] 第{chapter}章；用户要求：{req_key}；读取前文 {prev_chapters} 章"
        )
        _record_turn(self.state, "用户", summary)

        if not ok:
            _record_turn(self.state, "主编", ask)
            cm = ChatMessage("主编", ask)
            yield ("message", cm)
            self.active = "navigator"
            out.active_agent = self.active
            yield (
                "done",
                {
                    "active_agent": out.active_agent,
                    "novel_name": out.novel_name,
                    "needs_novel_picker": out.needs_novel_picker,
                    "session_ended": out.session_ended,
                },
            )
            return

        chief_resp = (
            f"好的，按向导参数为第{chapter}章创作剧情大纲"
            f"（前文参考 {prev_chapters} 章）。"
        )
        _record_turn(self.state, "主编", chief_resp)
        yield ("message", ChatMessage("主编", chief_resp))
        yield ("progress", "编剧处理中，可能需要数十秒，请稍候…")

        target, sub_intent = CHIEF_DELEGATION_MAP["创作剧情大纲"]
        self.active, line_pairs = _delegate_from_chief(
            self.agents, target, sub_intent, params
        )
        for key, msg in line_pairs:
            role = AGENT_DISPLAY_ROLE.get(key, key)
            sub_cm = ChatMessage(role, msg)
            _record_turn(self.state, role, msg)
            yield ("message", sub_cm)
        out.active_agent = self.active
        last_key = line_pairs[-1][0] if line_pairs else None
        cp = _creative_preview_payload(self.state, last_key)
        if cp is not None:
            yield ("creative_preview", cp)
        yield (
            "done",
            {
                "active_agent": out.active_agent,
                "novel_name": out.novel_name,
                "needs_novel_picker": out.needs_novel_picker,
                "session_ended": out.session_ended,
            },
        )


def select_novel_by_menu_choice(choice: int, new_name: Optional[str] = None) -> NovelContext:
    """与 CLI 一致：0=新建，1..n 选已有。"""
    novels = list_novels()
    if choice == 0:
        name = (new_name or "").strip()
        if not name:
            raise ValueError("新建作品需要提供名称")
        return create_novel(name)
    if 1 <= choice <= len(novels):
        return switch_novel(novels[choice - 1])
    raise ValueError("无效的作品选项")


def select_novel_existing(name: str) -> NovelContext:
    return switch_novel(name.strip())


__all__ = [
    "AGENT_DISPLAY_ROLE",
    "ChatMessage",
    "ChatStreamEvent",
    "NovelChatSession",
    "TurnResult",
    "WELCOME_HINT",
    "select_novel_by_menu_choice",
    "select_novel_existing",
]
