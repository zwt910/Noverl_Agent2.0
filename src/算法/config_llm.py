from __future__ import annotations

"""
统一的大模型配置模块。

本模块提供 `get_llm` 方法，返回一个已经配置好的 langchain Chat 模型，
供其他模块在需要调用大模型时统一使用。

配置方式（任选其一，按优先级）：
0. 项目根目录 `N_Agent/.env` 中的 `NOVEL_AGENT_API_KEY=...`（启动时自动加载，不覆盖系统已有变量）。
1. 环境变量 `NOVEL_AGENT_API_KEY` —— 本项目专用，推荐。
2. 环境变量 `OPENAI_API_KEY` —— 与 OpenAI 兼容接口的常见写法。

可选环境变量：
- `NOVEL_AGENT_BASE_URL` 或 `OPENAI_BASE_URL`：覆盖默认 API 地址。

示例（终端）：
    export NOVEL_AGENT_API_KEY="你的密钥"
    export PYTHONPATH="/path/to/N_Agent/src"
    python3 main_chat.py
"""

import os
from pathlib import Path
from typing import Any, Optional


def _load_local_env_file() -> None:
    """
    从项目根目录 N_Agent/.env 加载 KEY=VALUE。
    不覆盖已在操作系统里设置好的同名环境变量（与 python-dotenv 行为一致）。
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


_load_local_env_file()

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI


def _default_base_url() -> str:
    return (
        os.environ.get("NOVEL_AGENT_BASE_URL", "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or "https://ai.nengyongai.cn/v1"
    )


def resolve_api_key() -> str:
    """从环境变量解析 API Key；未配置时返回空字符串。"""
    return (
        os.environ.get("NOVEL_AGENT_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )


# 模块级常量（供只读展示；实际调用以 resolve_api_key() 为准）
BASE_URL = _default_base_url()
MODEL_NAME = os.environ.get("NOVEL_AGENT_MODEL", "").strip() or "deepseek-v3-250324"
API_KEY = resolve_api_key()  # 导入时刻的快照，可能与运行时环境变量不同步


def get_llm(
    model: Optional[str] = None,
    **chat_kwargs: Any,
) -> BaseChatModel:
    """
    获取一个已经配置好的 Chat 模型实例。

    参数：
    - model: 可选，覆盖默认的模型名称。
    - **chat_kwargs: 透传给底层 ChatOpenAI 的可选参数，例如
      temperature、max_tokens、top_p 等，用于控制生成风格。
    """
    api_key = resolve_api_key()
    if not api_key:
        raise ValueError(
            "未配置大模型 API Key，服务端会返回 401（No token provided）。\n"
            "请在运行前设置环境变量，例如：\n"
            "  export NOVEL_AGENT_API_KEY='你的密钥'\n"
            "或：\n"
            "  export OPENAI_API_KEY='你的密钥'\n"
            "若使用自定义网关，还可设置 NOVEL_AGENT_BASE_URL。"
        )

    base_url = (
        os.environ.get("NOVEL_AGENT_BASE_URL", "").strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or BASE_URL
    )

    return ChatOpenAI(
        model=model or MODEL_NAME,
        base_url=base_url,
        api_key=api_key,
        **chat_kwargs,
    )


__all__ = ["get_llm", "BASE_URL", "API_KEY", "MODEL_NAME", "resolve_api_key"]
