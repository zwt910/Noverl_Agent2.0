from __future__ import annotations

import re
from typing import Dict, List

from .novel_manager import NovelContext

_CHAPTER_FILE = re.compile(r"^第(\d+)章\.txt$")
_OUTLINE_FILE = re.compile(r"^第(\d+)章_剧情大纲\.txt$")


def list_chapters(novel_ctx: NovelContext) -> List[Dict[str, object]]:
    d = novel_ctx.novel_dir / "chapter"
    if not d.is_dir():
        return []
    indices: List[int] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        m = _CHAPTER_FILE.match(p.name)
        if m:
            indices.append(int(m.group(1)))
    return [{"chapter": i, "label": f"第{i}章"} for i in sorted(indices)]


def read_chapter_text(novel_ctx: NovelContext, chapter: int) -> str:
    path = novel_ctx.novel_dir / "chapter" / f"第{chapter}章.txt"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def list_outlines(novel_ctx: NovelContext) -> List[Dict[str, object]]:
    d = novel_ctx.novel_dir / "plot"
    if not d.is_dir():
        return []
    indices: List[int] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        m = _OUTLINE_FILE.match(p.name)
        if m:
            indices.append(int(m.group(1)))
    return [{"chapter": i, "label": f"第{i}章 · 剧情大纲"} for i in sorted(indices)]


def read_outline_text(novel_ctx: NovelContext, chapter: int) -> str:
    path = novel_ctx.novel_dir / "plot" / f"第{chapter}章_剧情大纲.txt"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


__all__ = [
    "list_chapters",
    "read_chapter_text",
    "list_outlines",
    "read_outline_text",
]
