"""
作品内可编辑文本文件的列表、读写、创建、重命名、删除（章节 / 剧情大纲 / 当前主线）。
文件名限于单层 .txt，防止路径穿透。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .novel_manager import NovelContext

_SAFE_FILE = re.compile(r"^[^\x00/\\]+\.txt$")
_CHAPTER_NUM = re.compile(r"^第(\d+)章\.txt$")
_OUTLINE_NUM = re.compile(r"^第(\d+)章_剧情大纲\.txt$")
_UNNAMED_MAIN = re.compile(r"^未命名主线(\d+)\.txt$")
def _validate_txt_filename(name: str) -> str:
    n = (name or "").strip()
    if not n or not _SAFE_FILE.match(n) or ".." in n:
        raise ValueError("文件名无效：须为单层 .txt 名称")
    return n


def _sort_chapter_key(item: Dict[str, Any]) -> Tuple[int, int, str]:
    ch = item.get("chapter")
    if isinstance(ch, int):
        return (0, ch, item["name"])
    return (1, 0, item["name"])


def _sort_outline_key(item: Dict[str, Any]) -> Tuple[int, int, str]:
    ch = item.get("chapter")
    if isinstance(ch, int):
        return (0, ch, item["name"])
    return (1, 0, item["name"])


def _sort_main_plot_key(item: Dict[str, Any]) -> Tuple[int, str]:
    m = _UNNAMED_MAIN.match(item["name"])
    if m:
        return (0, int(m.group(1)), item["name"])
    return (1, item["name"])


def list_chapter_files(novel_ctx: NovelContext) -> List[Dict[str, Any]]:
    d = novel_ctx.novel_dir / "chapter"
    if not d.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    for p in d.iterdir():
        if not p.is_file() or not p.name.lower().endswith(".txt"):
            continue
        m = _CHAPTER_NUM.match(p.name)
        if m:
            i = int(m.group(1))
            items.append({"name": p.name, "label": f"第{i}章", "chapter": i})
        else:
            stem = p.name[:-4] if p.name.lower().endswith(".txt") else p.name
            items.append({"name": p.name, "label": stem, "chapter": None})
    items.sort(key=_sort_chapter_key)
    return items


def list_outline_files(novel_ctx: NovelContext) -> List[Dict[str, Any]]:
    d = novel_ctx.novel_dir / "plot"
    if not d.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    for p in d.iterdir():
        if not p.is_file() or not p.name.lower().endswith(".txt"):
            continue
        m = _OUTLINE_NUM.match(p.name)
        if m:
            i = int(m.group(1))
            items.append({"name": p.name, "label": f"第{i}章 · 剧情大纲", "chapter": i})
        else:
            stem = p.name[:-4] if p.name.lower().endswith(".txt") else p.name
            items.append({"name": p.name, "label": stem, "chapter": None})
    items.sort(key=_sort_outline_key)
    return items


def _main_plot_dir(novel_ctx: NovelContext) -> Path:
    return novel_ctx.novel_dir / "main_plot"


def list_main_plot_files(novel_ctx: NovelContext) -> List[Dict[str, Any]]:
    d = _main_plot_dir(novel_ctx)
    if not d.is_dir():
        return []
    items: List[Dict[str, Any]] = []
    for p in d.iterdir():
        if not p.is_file() or not p.name.lower().endswith(".txt"):
            continue
        stem = p.name[:-4]
        items.append({"name": p.name, "label": stem})
    items.sort(key=_sort_main_plot_key)
    return items


def read_chapter_file(novel_ctx: NovelContext, name: str) -> str:
    name = _validate_txt_filename(name)
    path = novel_ctx.novel_dir / "chapter" / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8")


def write_chapter_file(novel_ctx: NovelContext, name: str, content: str) -> None:
    name = _validate_txt_filename(name)
    path = novel_ctx.novel_dir / "chapter" / name
    (novel_ctx.novel_dir / "chapter").mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else "", encoding="utf-8")


def read_outline_file(novel_ctx: NovelContext, name: str) -> str:
    name = _validate_txt_filename(name)
    path = novel_ctx.novel_dir / "plot" / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8")


def write_outline_file(novel_ctx: NovelContext, name: str, content: str) -> None:
    name = _validate_txt_filename(name)
    path = novel_ctx.novel_dir / "plot" / name
    (novel_ctx.novel_dir / "plot").mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else "", encoding="utf-8")


def read_main_plot_named(novel_ctx: NovelContext, name: str) -> str:
    name = _validate_txt_filename(name)
    path = _main_plot_dir(novel_ctx) / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path.read_text(encoding="utf-8")


def write_main_plot_named(novel_ctx: NovelContext, name: str, content: str) -> None:
    name = _validate_txt_filename(name)
    d = _main_plot_dir(novel_ctx)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(content if content is not None else "", encoding="utf-8")


def _next_new_filename(existing: List[str], base_stem: str) -> str:
    stems = {Path(n).stem for n in existing}
    if base_stem not in stems:
        return f"{base_stem}.txt"
    n = 1
    while f"{base_stem}{n}" in stems:
        n += 1
    return f"{base_stem}{n}.txt"


def _next_unnamed_main(existing_names: List[str]) -> str:
    nums: List[int] = []
    for n in existing_names:
        m = _UNNAMED_MAIN.match(n)
        if m:
            nums.append(int(m.group(1)))
    k = max(nums) + 1 if nums else 1
    return f"未命名主线{k}.txt"


def create_chapter_file(novel_ctx: NovelContext) -> str:
    d = novel_ctx.novel_dir / "chapter"
    d.mkdir(parents=True, exist_ok=True)
    names = [p.name for p in d.iterdir() if p.is_file() and p.name.lower().endswith(".txt")]
    fname = _next_new_filename(names, "新建文件")
    (d / fname).write_text("", encoding="utf-8")
    return fname


def create_outline_file(novel_ctx: NovelContext) -> str:
    d = novel_ctx.novel_dir / "plot"
    d.mkdir(parents=True, exist_ok=True)
    names = [p.name for p in d.iterdir() if p.is_file() and p.name.lower().endswith(".txt")]
    fname = _next_new_filename(names, "新建文件")
    (d / fname).write_text("", encoding="utf-8")
    return fname


def create_main_plot_file(novel_ctx: NovelContext) -> str:
    d = _main_plot_dir(novel_ctx)
    d.mkdir(parents=True, exist_ok=True)
    names = [p.name for p in d.iterdir() if p.is_file() and p.name.lower().endswith(".txt")]
    fname = _next_unnamed_main(names)
    (d / fname).write_text("", encoding="utf-8")
    return fname


def delete_chapter_file(novel_ctx: NovelContext, name: str) -> None:
    name = _validate_txt_filename(name)
    path = novel_ctx.novel_dir / "chapter" / name
    if path.is_file():
        path.unlink()


def delete_outline_file(novel_ctx: NovelContext, name: str) -> None:
    name = _validate_txt_filename(name)
    path = novel_ctx.novel_dir / "plot" / name
    if path.is_file():
        path.unlink()


def delete_main_plot_file(novel_ctx: NovelContext, name: str) -> None:
    name = _validate_txt_filename(name)
    path = _main_plot_dir(novel_ctx) / name
    if path.is_file():
        path.unlink()


def rename_chapter_file(novel_ctx: NovelContext, old_name: str, new_name: str) -> str:
    old_name = _validate_txt_filename(old_name)
    new_name = _validate_txt_filename(new_name)
    if old_name == new_name:
        return new_name
    src = novel_ctx.novel_dir / "chapter" / old_name
    dst = novel_ctx.novel_dir / "chapter" / new_name
    if not src.is_file():
        raise FileNotFoundError(old_name)
    if dst.exists():
        raise ValueError("目标文件名已存在")
    src.rename(dst)
    return new_name


def rename_outline_file(novel_ctx: NovelContext, old_name: str, new_name: str) -> str:
    old_name = _validate_txt_filename(old_name)
    new_name = _validate_txt_filename(new_name)
    if old_name == new_name:
        return new_name
    src = novel_ctx.novel_dir / "plot" / old_name
    dst = novel_ctx.novel_dir / "plot" / new_name
    if not src.is_file():
        raise FileNotFoundError(old_name)
    if dst.exists():
        raise ValueError("目标文件名已存在")
    src.rename(dst)
    return new_name


def rename_main_plot_file(novel_ctx: NovelContext, old_name: str, new_name: str) -> str:
    old_name = _validate_txt_filename(old_name)
    new_name = _validate_txt_filename(new_name)
    if old_name == new_name:
        return new_name
    d = _main_plot_dir(novel_ctx)
    src = d / old_name
    dst = d / new_name
    if not src.is_file():
        raise FileNotFoundError(old_name)
    if dst.exists():
        raise ValueError("目标文件名已存在")
    src.rename(dst)
    return new_name


def write_intro(novel_ctx: NovelContext, content: str) -> None:
    path = novel_ctx.novel_dir / "小说简介.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else "", encoding="utf-8")


__all__ = [
    "list_chapter_files",
    "list_outline_files",
    "list_main_plot_files",
    "read_chapter_file",
    "write_chapter_file",
    "read_outline_file",
    "write_outline_file",
    "read_main_plot_named",
    "write_main_plot_named",
    "create_chapter_file",
    "create_outline_file",
    "create_main_plot_file",
    "delete_chapter_file",
    "delete_outline_file",
    "delete_main_plot_file",
    "rename_chapter_file",
    "rename_outline_file",
    "rename_main_plot_file",
    "write_intro",
]
