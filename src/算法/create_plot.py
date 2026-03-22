from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .config_llm import get_llm
from 后端.chapter_cache import get_multi_chapter_summaries
from 后端.history_manager import log_outline_save
from 后端.novel_manager import NovelContext, load_intro, load_main_plot


@dataclass
class OutlineSessionState:
    """
    单次“大纲创作会话”的状态，仅存活于内存中。

    - current_outline: 当前版本的大纲全文
    - history: 历史版本列表（不落盘，仅用于会话内回溯）
    """

    novel_ctx: NovelContext
    chapter_index: int
    current_outline: str
    history: List[str]


def _build_outline_prompt(
    mode: str,
    chapter_index: int,
    summaries: str,
    intro: str,
    main_plot: str,
    user_text: str,
    previous_outline: Optional[str] = None,
) -> List[object]:
    """
    构造给 LLM 的消息列表。
    """
    if mode == "create":
        system_content = (
            "你是一名专业的小说策划编辑，擅长根据现有剧情与设定，"
            "为下一章设计结构清晰、冲突鲜明的剧情大纲。\n"
            "请严格按照要求输出下一章的大纲。"
        )
    else:
        system_content = (
            "你是一名专业的小说策划编辑，擅长根据作者反馈迭代优化剧情大纲。\n"
            "现在需要你在保持世界观与人物设定一致的前提下，改写当前章节的大纲。"
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
    ]

    if summaries.strip():
        lines.extend(
            [
                "【前几章剧情总结】",
                summaries.strip(),
                "",
            ]
        )

    if previous_outline:
        lines.extend(
            [
                "【上一版本章节大纲】",
                previous_outline.strip(),
                "",
            ]
        )

    if mode == "create":
        lines.extend(
            [
                "【本次创作要求】",
                user_text.strip() or "(作者未给出特别要求，可按爽感与节奏自行设计)",
                "",
                "请根据以上信息，为“第{idx}章”生成一份详细剧情大纲。".format(
                    idx=chapter_index
                ),
                "大纲要求：",
                "1. 使用中文分条列出本章的关键情节节点，可以使用 1.2.3. 这样的编号。",
                "2. 明确每个情节中出现的主要角色、主要矛盾与推进的主线信息。",
                "3. 注重冲突、爽点与悬念的设计，保证读者有继续阅读的欲望。",
                "4. 不要直接写成正文，而是以“概要描述”的方式呈现。",
            ]
        )
    else:
        lines.extend(
            [
                "【作者对大纲的修改意见】",
                user_text.strip() or "(暂无具体修改意见，可自行小幅优化)",
                "",
                "请在保持整体走向大致一致的前提下，根据以上修改意见，对当前大纲进行迭代优化。",
                "输出一份新的“第{idx}章”剧情大纲，仍然使用分条列出关键情节。".format(
                    idx=chapter_index
                ),
            ]
        )

    user_content = "\n".join(lines)

    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def _invoke_outline_llm(messages: List[object], llm: Optional[BaseChatModel]) -> str:
    if llm is None:
        llm = get_llm(temperature=0.6)
    resp = llm.invoke(messages)
    content = getattr(resp, "content", str(resp))
    return str(content).strip()


def generate_outline(
    novel_ctx: NovelContext,
    chapter_index: int,
    user_requirements: str = "",
    window_size: int = 5,
    llm: Optional[BaseChatModel] = None,
) -> OutlineSessionState:
    """
    生成“第 chapter_index 章”的初始剧情大纲。
    """
    summaries = get_multi_chapter_summaries(
        novel_ctx, upto_index=chapter_index - 1, window_size=window_size, llm=llm
    )
    intro = load_intro(novel_ctx)
    main_plot = load_main_plot(novel_ctx)

    messages = _build_outline_prompt(
        mode="create",
        chapter_index=chapter_index,
        summaries=summaries,
        intro=intro,
        main_plot=main_plot,
        user_text=user_requirements,
        previous_outline=None,
    )
    outline_text = _invoke_outline_llm(messages, llm=llm)
    return OutlineSessionState(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        current_outline=outline_text,
        history=[outline_text],
    )


def revise_outline(
    current_outline: str,
    novel_ctx: NovelContext,
    chapter_index: int,
    user_feedback: str,
    window_size: int = 5,
    llm: Optional[BaseChatModel] = None,
    previous_history: Optional[List[str]] = None,
) -> OutlineSessionState:
    """
    在上一版大纲基础上，根据作者反馈进行迭代修改，并维护会话内的历史版本列表。
    """
    summaries = get_multi_chapter_summaries(
        novel_ctx, upto_index=chapter_index - 1, window_size=window_size, llm=llm
    )
    intro = load_intro(novel_ctx)
    main_plot = load_main_plot(novel_ctx)

    messages = _build_outline_prompt(
        mode="revise",
        chapter_index=chapter_index,
        summaries=summaries,
        intro=intro,
        main_plot=main_plot,
        user_text=user_feedback,
        previous_outline=current_outline,
    )
    outline_text = _invoke_outline_llm(messages, llm=llm)

    history: List[str] = list(previous_history or [])
    if not history or history[-1] != current_outline:
        history.append(current_outline)
    history.append(outline_text)

    return OutlineSessionState(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        current_outline=outline_text,
        history=history,
    )


def save_outline(state: OutlineSessionState) -> str:
    """
    将当前大纲版本落盘到 plot/ 目录，并记录历史日志。
    返回生成的文件路径字符串。
    """
    novel_ctx = state.novel_ctx
    out_dir = novel_ctx.novel_dir / "plot"
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"第{state.chapter_index}章_剧情大纲.txt"
    path = out_dir / filename
    path.write_text(state.current_outline, encoding="utf-8")

    log_outline_save(
        novel_ctx=novel_ctx,
        chapter_index=state.chapter_index,
        outline_path=path,
        extra_info={"history_length": len(state.history)},
    )
    return str(path)


__all__ = [
    "OutlineSessionState",
    "generate_outline",
    "revise_outline",
    "save_outline",
]

