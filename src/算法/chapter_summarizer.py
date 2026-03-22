from __future__ import annotations

"""
章节剧情总结工具。

功能：
- 读取指定章节文本文件；
- 调用大模型总结本章节的主要剧情；
- 返回形如「第X章剧情：xxxxxxx」的单行文本。

说明：
- 大模型调用统一通过 `config_llm.get_llm` 完成。
"""

from pathlib import Path
from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .config_llm import get_llm


def _load_chapter_text(chapter_path: Path, encoding: str = "utf-8") -> str:
    if not chapter_path.exists():
        raise FileNotFoundError(f"章节文件不存在：{chapter_path}")
    return chapter_path.read_text(encoding=encoding)


def _build_summary_prompt(chapter_title: str, chapter_text: str) -> list:
    """
    构造给 LLM 的消息列表。
    """
    system_content = (
        "你是一名专业的小说编辑助手，擅长用简洁明了且精确的语言概括剧情。\n"
        "现在需要你根据给定的一章小说正文，总结这一章的主要剧情。\n"
        "总结要求：\n"
        "1. 使用中文描述，突出关键事件、冲突和人物变化。\n"
        "2. 不要加入新的设定或合理化推测，只基于原文内容。\n"
        "3. 总结长度控制在 300 字内 。\n"
        "4. 只输出剧情内容本身，不要解释你的思路。\n"
    )

    user_content = (
        f"章节标题：{chapter_title}\n\n"
        "下面是本章节的正文内容：\n"
        "```text\n"
        f"{chapter_text}\n"
        "```\n\n"
        "请用中文概括这一章的主要剧情，直接给出精准的剧情摘要。"
    )

    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def summarize_chapter(
    chapter_path: str | Path,
    llm: Optional[BaseChatModel] = None,
) -> str:
    """
    总结指定章节的剧情，返回形如：
        「第X章剧情：xxxxxxx」

    参数：
    - chapter_path: 章节文本文件路径。
    - llm: 可选，自定义的 langchain Chat 模型实例；为 None 时使用默认配置。
    """
    chapter_path = Path(chapter_path)
    chapter_text = _load_chapter_text(chapter_path)

    chapter_title = chapter_path.stem  # 例如 "第1章"

    if llm is None:
        llm = get_llm(temperature=0.3)

    messages = _build_summary_prompt(chapter_title, chapter_text)
    response = llm.invoke(messages)

    raw_summary = response.content if hasattr(response, "content") else str(response)
    # 去掉首尾空白
    raw_summary = str(raw_summary).strip()

    return f"{chapter_title}剧情：{raw_summary}"


__all__ = ["summarize_chapter"]

