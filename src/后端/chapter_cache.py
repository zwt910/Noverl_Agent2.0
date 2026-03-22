from __future__ import annotations

from pathlib import Path
from typing import Optional

from 算法.chapter_summarizer import summarize_chapter
from .novel_manager import NovelContext


def _chapter_dir(novel_ctx: NovelContext) -> Path:
    return novel_ctx.novel_dir / "chapter"


def chapter_path(novel_ctx: NovelContext, chapter_index: int) -> Path:
    """
    返回指定章节正文文件路径：chapter/第X章.txt
    """
    return _chapter_dir(novel_ctx) / f"第{chapter_index}章.txt"


def chapter_summary_path(novel_ctx: NovelContext, chapter_index: int) -> Path:
    """
    返回指定章节总结文件路径：chapter/第X章总结.txt
    """
    return _chapter_dir(novel_ctx) / f"第{chapter_index}章总结.txt"


def get_or_create_chapter_summary(
    novel_ctx: NovelContext,
    chapter_index: int,
    llm: Optional[object] = None,
) -> str:
    """
    获取指定章节的剧情总结，如不存在则调用 LLM 生成并写入缓存文件。
    """
    summary_file = chapter_summary_path(novel_ctx, chapter_index)
    if summary_file.exists():
        return summary_file.read_text(encoding="utf-8")

    chapter_file = chapter_path(novel_ctx, chapter_index)
    summary_text = summarize_chapter(chapter_file, llm=llm)
    summary_file.write_text(summary_text, encoding="utf-8")
    return summary_text


def get_multi_chapter_summaries(
    novel_ctx: NovelContext,
    upto_index: int,
    window_size: Optional[int] = None,
    llm: Optional[object] = None,
) -> str:
    """
    组合前几章的剧情总结为一个长文本。

    参数：
    - upto_index: 需要生成大纲的章节号，前几章总结的上界（包含该章）。
    - window_size: 可选，窗口大小；如果为 None，则从第 1 章开始。
    """
    if upto_index <= 0:
        return ""

    if window_size is None or window_size <= 0:
        start = 1
    else:
        start = max(1, upto_index - window_size + 1)

    parts = []
    for idx in range(start, upto_index + 1):
        try:
            parts.append(get_or_create_chapter_summary(novel_ctx, idx, llm=llm))
        except FileNotFoundError:
            # 对于尚未写出的章节，跳过即可。
            continue

    return "\n\n".join(parts)


__all__ = [
    "chapter_path",
    "chapter_summary_path",
    "get_or_create_chapter_summary",
    "get_multi_chapter_summaries",
]

