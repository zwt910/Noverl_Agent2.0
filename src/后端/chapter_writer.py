from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from 算法.config_llm import get_llm
from .chapter_cache import chapter_path
from .history_manager import log_chapter_save
from .novel_manager import NovelContext, load_intro, load_main_plot


@dataclass
class ChapterSessionState:
    """
    单次“章节正文创作/修改会话”的状态，仅存在于内存中。
    """

    novel_ctx: NovelContext
    chapter_index: int
    current_text: str
    history: List[str]


def _load_prev_chapter_text(novel_ctx: NovelContext, chapter_index: int) -> str:
    """
    读取上一章正文内容，如不存在则返回空字符串。
    """
    if chapter_index <= 1:
        return ""
    prev_path = chapter_path(novel_ctx, chapter_index - 1)
    if not prev_path.exists():
        return ""
    return prev_path.read_text(encoding="utf-8")


def _build_generate_prompt(
    chapter_index: int,
    outline_text: str,
    prev_chapter_text: str,
    intro: str,
    main_plot: str,
) -> List[object]:
    system_content = (
        "你是一名擅长网络爽文与长篇连载的专业小说作者助手。\n"
        "你的任务是根据给定的大纲与前文正文，撰写下一章的正文内容，"
        "需要尽量模仿前一章的叙事风格与视角，保证阅读体验连贯。"
    )

    lines = [
        f"目标章节：第{chapter_index}章",
        "",
        "【小说简介】",
        intro.strip() or "(暂无简介)",
        "",
        "【当前主线剧情】",
        main_plot.strip() or "(暂无主线描述)",
        "",
        "【本章剧情大纲】",
        outline_text.strip(),
        "",
    ]

    if prev_chapter_text.strip():
        lines.extend(
            [
                "【上一章正文（用于学习文风与衔接剧情）】",
                prev_chapter_text.strip(),
                "",
            ]
        )

    lines.extend(
        [
            "请根据以上信息，完整撰写“第{idx}章”的正文。".format(idx=chapter_index),
            "要求：",
            "1. 使用与上一章尽量一致的文风、视角与人称；如未提供上一章，则选择适合本题材的主流网文风格。",
            "2. 严格遵循给定大纲的主要剧情走向，但可以在细节上合理发挥。",
            "3. 注意人物性格与世界观设定的一致性，不要自相矛盾。",
            "4. 直接输出正文内容，不要解释你的写作思路。",
        ]
    )

    user_content = "\n".join(lines)
    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def _build_revise_prompt(
    chapter_index: int,
    current_text: str,
    intro: str,
    main_plot: str,
    user_requirements: str,
) -> List[object]:
    system_content = (
        "你是一名专业小说文稿编辑，擅长在保持原有剧情与设定不变的前提下，"
        "根据作者要求对正文进行润色与改写。"
    )

    lines = [
        f"目标章节：第{chapter_index}章",
        "",
        "【小说简介】",
        intro.strip() or "(暂无简介)",
        "",
        "【当前主线剧情】",
        main_plot.strip() or "(暂无主线描述)",
        "",
        "【上一版本正文】",
        current_text.strip(),
        "",
        "【作者的修改要求】",
        user_requirements.strip()
        or "作者未给出具体要求，你可以在不改变剧情走向的前提下进行整体优化。",
        "",
        "请在保留原有剧情发展的前提下，生成一份新的章节正文：",
        "1. 可以在节奏、语言、细节描写上进行明显优化。",
        "2. 不要删改关键剧情节点，也不要引入严重违背设定的新元素。",
        "3. 直接输出修改后的正文，不要解释修改理由。",
    ]

    user_content = "\n".join(lines)
    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def _invoke_chapter_llm(messages: List[object], llm: Optional[BaseChatModel]) -> str:
    if llm is None:
        llm = get_llm(temperature=0.7)
    resp = llm.invoke(messages)
    content = getattr(resp, "content", str(resp))
    return str(content).strip()


def generate_chapter(
    novel_ctx: NovelContext,
    chapter_index: int,
    outline_text: str,
    llm: Optional[BaseChatModel] = None,
) -> ChapterSessionState:
    """
    根据给定大纲与前一章正文，生成下一章初稿。
    """
    prev_text = _load_prev_chapter_text(novel_ctx, chapter_index)
    intro = load_intro(novel_ctx)
    main_plot = load_main_plot(novel_ctx)

    messages = _build_generate_prompt(
        chapter_index=chapter_index,
        outline_text=outline_text,
        prev_chapter_text=prev_text,
        intro=intro,
        main_plot=main_plot,
    )
    text = _invoke_chapter_llm(messages, llm=llm)
    return ChapterSessionState(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        current_text=text,
        history=[text],
    )


def revise_chapter(
    current_text: str,
    novel_ctx: NovelContext,
    chapter_index: int,
    user_requirements: str,
    llm: Optional[BaseChatModel] = None,
    previous_history: Optional[List[str]] = None,
) -> ChapterSessionState:
    """
    在上一版本正文基础上，根据作者的修改要求进行迭代修改，并维护会话内历史版本列表。
    """
    intro = load_intro(novel_ctx)
    main_plot = load_main_plot(novel_ctx)
    messages = _build_revise_prompt(
        chapter_index=chapter_index,
        current_text=current_text,
        intro=intro,
        main_plot=main_plot,
        user_requirements=user_requirements,
    )
    text = _invoke_chapter_llm(messages, llm=llm)
    history: List[str] = list(previous_history or [])
    if not history or history[-1] != current_text:
        history.append(current_text)
    history.append(text)
    return ChapterSessionState(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        current_text=text,
        history=history,
    )


def save_chapter(state: ChapterSessionState, source_agent: str = "writer") -> str:
    """
    将当前正文版本落盘到 chapter/ 目录，并记录历史日志。
    返回生成的文件路径字符串。
    """
    novel_ctx = state.novel_ctx
    path = chapter_path(novel_ctx, state.chapter_index)
    path.write_text(state.current_text, encoding="utf-8")

    log_chapter_save(
        novel_ctx=novel_ctx,
        chapter_index=state.chapter_index,
        chapter_path=path,
        source_agent=source_agent,
        extra_info={"history_length": len(state.history)},
    )
    return str(path)


__all__ = [
    "ChapterSessionState",
    "generate_chapter",
    "revise_chapter",
    "save_chapter",
]

