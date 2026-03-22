from __future__ import annotations

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from 算法.config_llm import get_llm
from .chapter_cache import chapter_path
from .chapter_writer import ChapterSessionState, save_chapter
from .novel_manager import NovelContext, load_intro, load_main_plot


def _build_optimize_prompt(
    chapter_index: int,
    original_text: str,
    intro: str,
    main_plot: str,
    user_requirements: str,
) -> list:
    system_content = (
        "你是一名专业的小说文字编辑，擅长从文风、逻辑、节奏、用词等方面，"
        "在不改变原有剧情走向的前提下，对章节正文进行整体优化。"
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
        "【原始章节正文】",
        original_text.strip(),
        "",
        "【作者的修改/优化要求】",
        user_requirements.strip()
        or "作者未给出具体要求，你可以从文风、节奏与可读性方面进行整体优化。",
        "",
        "请在不改变主要剧情节点与人物设定的前提下，对上述正文进行整体优化改写：",
        "1. 可以调整段落划分与节奏，使阅读更加流畅、有起伏。",
        "2. 可以在细节描写与心理描写上适当加强，但不要加入明显与原设定冲突的内容。",
        "3. 直接输出优化后的正文内容，不要解释你的修改。",
    ]

    user_content = "\n".join(lines)
    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def _build_iterate_prompt(
    chapter_index: int,
    current_text: str,
    intro: str,
    main_plot: str,
    user_requirements: str,
) -> list:
    system_content = (
        "你是一名专业的小说文字编辑，擅长基于上一版修改稿继续迭代优化。"
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
        "【当前版本正文】",
        current_text.strip(),
        "",
        "【作者的进一步修改/优化要求】",
        user_requirements.strip()
        or "作者未给出具体要求，你可以在上一版基础上做细节层面的进一步润色与调整。",
        "",
        "请在不改变主要剧情节点与人物设定的前提下，对上述正文进行进一步优化：",
        "1. 保持整体走向与上一版大致一致。",
        "2. 重点关注作者提出的修改要求。",
        "3. 直接输出新的正文版本，不要解释你的修改。",
    ]

    user_content = "\n".join(lines)
    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def _invoke_editor_llm(messages: list, llm: Optional[BaseChatModel]) -> str:
    if llm is None:
        llm = get_llm(temperature=0.5)
    resp = llm.invoke(messages)
    content = getattr(resp, "content", str(resp))
    return str(content).strip()


def optimize_chapter(
    novel_ctx: NovelContext,
    chapter_index: int,
    user_requirements: str,
    llm: Optional[BaseChatModel] = None,
) -> ChapterSessionState:
    """
    对已有章节正文进行第一次整体优化，返回会话状态。
    """
    path = chapter_path(novel_ctx, chapter_index)
    original_text = path.read_text(encoding="utf-8")
    intro = load_intro(novel_ctx)
    main_plot = load_main_plot(novel_ctx)

    messages = _build_optimize_prompt(
        chapter_index=chapter_index,
        original_text=original_text,
        intro=intro,
        main_plot=main_plot,
        user_requirements=user_requirements,
    )
    new_text = _invoke_editor_llm(messages, llm=llm)

    return ChapterSessionState(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        current_text=new_text,
        history=[original_text, new_text],
    )


def iterate_optimization(
    current_text: str,
    novel_ctx: NovelContext,
    chapter_index: int,
    user_requirements: str,
    llm: Optional[BaseChatModel] = None,
) -> ChapterSessionState:
    """
    在上一版本优化稿基础上继续优化，返回新的会话状态。
    """
    intro = load_intro(novel_ctx)
    main_plot = load_main_plot(novel_ctx)

    messages = _build_iterate_prompt(
        chapter_index=chapter_index,
        current_text=current_text,
        intro=intro,
        main_plot=main_plot,
        user_requirements=user_requirements,
    )
    new_text = _invoke_editor_llm(messages, llm=llm)
    history = [current_text, new_text]

    return ChapterSessionState(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        current_text=new_text,
        history=history,
    )


def save_optimized_chapter(state: ChapterSessionState) -> str:
    """
    将当前优化后的正文版本保存到原章节文件，并记录“编辑来源”的历史。
    """
    return save_chapter(state, source_agent="editor")


__all__ = [
    "optimize_chapter",
    "iterate_optimization",
    "save_optimized_chapter",
]

