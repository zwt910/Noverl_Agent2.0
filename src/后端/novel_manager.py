from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

# 仅允许单层目录名，防止 ..、绝对路径或子路径穿透 novels 根目录
_SAFE_NOVEL_NAME = re.compile(r"^[^\x00/\\]+$")


def validate_novel_directory_name(name: str) -> str:
    """
    校验作品目录名安全可用；通过则返回 strip 后的名称。
    禁止空串、.、..、含路径分隔符或空字节。
    """
    n = (name or "").strip()
    if not n or n in (".", ".."):
        raise ValueError("作品名称无效：不能为空或为 . / ..")
    if not _SAFE_NOVEL_NAME.match(n):
        raise ValueError("作品名称无效：不能包含路径分隔符或空字符")
    if ".." in n:
        raise ValueError("作品名称无效：不能包含 ..")
    return n


def _project_root() -> Path:
    """
    返回 N_Agent 项目的根目录（包含 data/ 和 src/ 的那一级）。
    当前文件位于：N_Agent/src/后端/novel_manager.py
    """
    return Path(__file__).resolve().parents[2]


def _novels_root() -> Path:
    """
    返回存放所有作品的根目录：N_Agent/data/novels
    """
    return _project_root() / "data" / "novels"


@dataclass
class NovelContext:
    """
    表示当前正在操作的作品上下文。

    - novel_name: 作品名称（目录名）
    - novel_dir: 作品根目录路径：.../data/novels/{novel_name}
    """

    novel_name: str
    novel_dir: Path


def list_novels() -> List[str]:
    """
    列出当前所有已存在的作品名称（即 novels 目录下的子目录名）。
    """
    root = _novels_root()
    if not root.exists():
        return []

    return sorted(
        p.name for p in root.iterdir() if p.is_dir()
    )


def get_novel_dir(novel_name: str) -> Path:
    """
    根据作品名返回作品目录路径，若不存在则抛出 FileNotFoundError。
    """
    novel_name = validate_novel_directory_name(novel_name)
    root = _novels_root()
    novel_dir = root / novel_name
    if not novel_dir.exists():
        raise FileNotFoundError(f"作品不存在：{novel_name}（路径：{novel_dir}）")
    return novel_dir


def _ensure_basic_dirs(novel_dir: Path) -> None:
    """
    确保作品目录下的基础子目录存在：
    - chapter/
    - plot/
    - main_plot/
    - role_inf/
    """
    (novel_dir / "chapter").mkdir(parents=True, exist_ok=True)
    (novel_dir / "plot").mkdir(parents=True, exist_ok=True)
    (novel_dir / "main_plot").mkdir(parents=True, exist_ok=True)
    (novel_dir / "role_inf").mkdir(parents=True, exist_ok=True)
    (novel_dir / "logs").mkdir(parents=True, exist_ok=True)


def ensure_novel_files(novel_dir: Path) -> None:
    """
    在作品目录下创建/初始化关键说明文件：
    - 小说简介.txt
    - main_plot/ 下至少一条主线（未命名主线1.txt）；旧版根目录 当前主线剧情.txt 会迁入
    """
    _ensure_basic_dirs(novel_dir)

    intro_path = novel_dir / "小说简介.txt"
    if not intro_path.exists():
        intro_path.write_text(
            "请在此撰写本小说的整体背景设定、世界观与主要冲突。\n",
            encoding="utf-8",
        )

    main_plot_dir = novel_dir / "main_plot"
    main_plot_dir.mkdir(parents=True, exist_ok=True)
    legacy = novel_dir / "当前主线剧情.txt"
    txts = list(main_plot_dir.glob("*.txt"))
    if legacy.exists() and not txts:
        legacy.rename(main_plot_dir / "未命名主线1.txt")
        txts = list(main_plot_dir.glob("*.txt"))
    if not txts:
        (main_plot_dir / "未命名主线1.txt").write_text(
            "请在此简要描述当前推进中的主线剧情（可随创作进度更新；支持多条主线文件）。\n",
            encoding="utf-8",
        )


def create_novel(novel_name: str) -> NovelContext:
    """
    创建一个新的作品目录及其基础结构，返回对应的 NovelContext。
    """
    novel_name = validate_novel_directory_name(novel_name)
    root = _novels_root()
    root.mkdir(parents=True, exist_ok=True)

    novel_dir = root / novel_name
    novel_dir.mkdir(parents=True, exist_ok=True)

    ensure_novel_files(novel_dir)
    return NovelContext(novel_name=novel_name, novel_dir=novel_dir)


def switch_novel(novel_name: str) -> NovelContext:
    """
    切换到已有作品，返回新的 NovelContext。

    不负责维护全局变量，仅提供纯函数接口，供 CLI 或对话模式管理当前上下文时调用。
    """
    novel_name = validate_novel_directory_name(novel_name)
    novel_dir = get_novel_dir(novel_name)
    ensure_novel_files(novel_dir)
    return NovelContext(novel_name=novel_name, novel_dir=novel_dir)


def load_intro(novel_ctx: NovelContext) -> str:
    """
    读取当前作品的《小说简介.txt》内容。
    """
    path = novel_ctx.novel_dir / "小说简介.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_main_plot(novel_ctx: NovelContext) -> str:
    """
    读取当前作品全部主线文本，供模型上下文使用：main_plot 目录下所有 .txt 按文件名排序拼接；
    若目录为空则回退读取根目录遗留的 当前主线剧情.txt。
    """
    main_plot_dir = novel_ctx.novel_dir / "main_plot"
    parts: List[str] = []
    if main_plot_dir.is_dir():
        for p in sorted(main_plot_dir.glob("*.txt"), key=lambda x: x.name):
            if p.is_file():
                parts.append(p.read_text(encoding="utf-8"))
    if parts:
        return "\n\n---\n\n".join(parts)
    legacy = novel_ctx.novel_dir / "当前主线剧情.txt"
    if legacy.exists():
        return legacy.read_text(encoding="utf-8")
    return ""


__all__ = [
    "NovelContext",
    "list_novels",
    "create_novel",
    "get_novel_dir",
    "ensure_novel_files",
    "load_intro",
    "load_main_plot",
    "switch_novel",
    "validate_novel_directory_name",
]

