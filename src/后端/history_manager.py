from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .novel_manager import NovelContext


def _logs_dir(novel_ctx: NovelContext) -> Path:
    d = novel_ctx.novel_dir / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """
    将一条记录以 JSON Lines 形式追加到文件末尾。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)
        f.write("\n")


@dataclass
class OutlineSaveRecord:
    novel_name: str
    chapter_index: int
    outline_file: str
    timestamp: str
    extra_info: Optional[Dict[str, Any]] = None


@dataclass
class ChapterSaveRecord:
    novel_name: str
    chapter_index: int
    chapter_file: str
    source_agent: str  # 例如 "writer" / "editor"
    timestamp: str
    extra_info: Optional[Dict[str, Any]] = None


def log_outline_save(
    novel_ctx: NovelContext,
    chapter_index: int,
    outline_path: Path,
    extra_info: Optional[Dict[str, Any]] = None,
) -> None:
    """
    记录“大纲最终保存”的历史，仅保留日志，不保留中间版本文件。
    """
    record = OutlineSaveRecord(
        novel_name=novel_ctx.novel_name,
        chapter_index=chapter_index,
        outline_file=str(outline_path.name),
        timestamp=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        extra_info=extra_info,
    )
    log_path = _logs_dir(novel_ctx) / "outline_history.log"
    _append_jsonl(log_path, asdict(record))


def log_chapter_save(
    novel_ctx: NovelContext,
    chapter_index: int,
    chapter_path: Path,
    source_agent: str,
    extra_info: Optional[Dict[str, Any]] = None,
) -> None:
    """
    记录“章节正文最终保存”的历史。
    """
    record = ChapterSaveRecord(
        novel_name=novel_ctx.novel_name,
        chapter_index=chapter_index,
        chapter_file=str(chapter_path.name),
        source_agent=source_agent,
        timestamp=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        extra_info=extra_info,
    )
    log_name = (
        "chapter_edit_history.log" if source_agent == "editor" else "chapter_history.log"
    )
    log_path = _logs_dir(novel_ctx) / log_name
    _append_jsonl(log_path, asdict(record))


def dialogue_memory_path(novel_ctx: NovelContext) -> Path:
    return _logs_dir(novel_ctx) / "dialogue_memory.jsonl"


def append_dialogue_turn(novel_ctx: NovelContext, role: str, text: str) -> None:
    """
    追加一条对话到当前作品的持久化记忆（JSON Lines）。
    """
    path = dialogue_memory_path(novel_ctx)
    record = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "role": role,
        "text": (text or "")[:50000],
    }
    _append_jsonl(path, record)


def load_recent_dialogue(novel_ctx: NovelContext, limit: int = 48) -> List[Dict[str, Any]]:
    """
    从磁盘读取最近 limit 条对话，用于恢复会话上下文。
    """
    path = dialogue_memory_path(novel_ctx)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "role" in obj and "text" in obj:
                out.append({"role": str(obj["role"]), "text": str(obj["text"])})
        except Exception:
            continue
    return out


__all__ = [
    "log_outline_save",
    "log_chapter_save",
    "append_dialogue_turn",
    "load_recent_dialogue",
    "dialogue_memory_path",
]

