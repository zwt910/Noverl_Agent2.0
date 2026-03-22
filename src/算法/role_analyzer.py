from __future__ import annotations

"""
基于章节文本，分析本章中出现的角色信息，并按章节单独存储。

每一章的每个角色仅记录以下字段：
- name（姓名，必填）
- gender（性别，可选）
- location（位置，可选）
- events（经历的事件，可选，文本自由描述）
- final_status（最终状态，可选，文本自由描述）

核心流程：
1. 读取章节文本文件内容。
2. 使用 LLM 分析本章出现的主要角色及其上述字段信息。
3. 将本章节的角色信息以 JSON 形式存入与章节同级目录下的
   `chapter_role` 文件夹中，文件名形如：`第1章_角色信息.json`。

说明：
- 不再更新全局的角色信息表（roles_data.json），只为每一章分别保存快照信息。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .config_llm import get_llm


@dataclass
class AnalyzeResult:
    """单次章节分析的结果信息，便于上层记录和调试。"""

    chapter_path: Path
    output_path: Path
    role_names: List[str]  # 本章中识别到的角色姓名列表


def get_default_llm() -> BaseChatModel:
    """
    获取默认的 LLM 实例。

    具体模型配置统一在 `config_llm.get_llm` 中维护。
    """
    return get_llm(temperature=0.1)


def _load_chapter_text(chapter_path: Path, encoding: str = "utf-8") -> str:
    if not chapter_path.exists():
        raise FileNotFoundError(f"章节文件不存在：{chapter_path}")
    return chapter_path.read_text(encoding=encoding)


def _load_schema() -> Dict[str, Any]:
    """
    从默认位置加载角色 schema（N_Agent/data/role_schema.json）。
    """
    # 当前文件：N_Agent/src/算法/role_analyzer.py
    # parents[2] -> N_Agent
    base_dir = Path(__file__).resolve().parents[2] / "data"
    schema_path = base_dir / "role_schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"未找到角色 schema 文件：{schema_path}")
    with schema_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_extraction_prompt(schema: Dict[str, Any], chapter_text: str) -> List[Any]:
    """
    构造给 LLM 的消息列表，要求其输出严格 JSON。

    输出格式约定为（用于写入“第X章_角色信息.json”）：

    {
      "chapter_title": "第1章",
      "roles": [
        {
          "name": "张三",
          "gender": "男",
          "location": "王都城门",
          "events": "本章中张三先与守卫发生冲突，被误会为刺客，随后证明清白。",
          "final_status": "离开城门，心情复杂但安全。"
        }
      ]
    }

    说明：
    - name：角色姓名，必填。
    - gender：性别，可选，自由文本，例如“男 / 女 / 未知 / 机器人”等。
    - location：本章结束时角色大致所在位置，可选，自由文本。
    - events：本章中该角色经历的关键事件，可选，自由文本，可以是一两句话的总结。
    - final_status：本章结束时该角色的状态（情绪、身体、处境等综合），可选，自由文本。
    """

    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)

    system_content = (
        "你是一个小说角色信息抽取助手。\n"
        "给你一段小说章节文本和角色信息的 schema，"
        "请找出本章节中出现的主要角色，并根据章节内容为每个角色提取以下字段：\n"
        "- name（姓名，必填）\n"
        "- gender（性别，可选）\n"
        "- location（位置，可选）\n"
        "- events（本章中经历的关键事件，可选，尽量详细些，100字以内）\n"
        "- final_status（本章结束时的综合状态，可选，自由文本）。\n\n"
        "要求：\n"
        "1. name 必须有明确的角色姓名。\n"
        "2. 其他字段仅在章节中有明确信息时填写；如果没有信息，可以省略该字段。\n"
        "3. gender/location/events/final_status 不限制具体格式，可以是自然语言文本。\n"
        "4. 输出必须是合法的 JSON，不能包含额外的注释或自然语言说明。\n"
        "5. 可以参考给定的 schema 了解角色可能的背景和状态，但最终只输出上述 5 个字段。\n"
    )

    user_content = (
        "下面是角色信息的 JSON schema：\n"
        "```json\n"
        f"{schema_str}\n"
        "```\n\n"
        "下面是本章节的正文内容，请根据该内容，识别主要角色并生成本章节的角色信息 JSON：\n"
        "```text\n"
        f"{chapter_text}\n"
        "```\n\n"
        "请直接输出 JSON，格式为：\n"
        "{\n"
        '  "chapter_title": "第1章",\n'
        '  "roles": [\n'
        "    {\n"
        '      "name": "角色名",\n'
        '      "gender": "可选",\n'
        '      "location": "可选",\n'
        '      "events": "可选，本章经历的事件摘要",\n'
        '      "final_status": "可选，本章结束时的综合状态"\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )

    return [
        SystemMessage(content=system_content),
        HumanMessage(content=user_content),
    ]


def _call_llm_for_roles(
    llm: BaseChatModel, schema: Dict[str, Any], chapter_text: str
) -> Dict[str, Any]:
    """
    调用 LLM，返回解析后的 JSON 对象。
    """
    messages = _build_extraction_prompt(schema, chapter_text)
    response = llm.invoke(messages)

    raw_text = response.content if hasattr(response, "content") else str(response)

    # 有些模型可能会在 JSON 前后加说明，这里尝试截取第一个大括号开始到最后一个大括号结束
    first_brace = raw_text.find("{")
    last_brace = raw_text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        raw_text = raw_text[first_brace : last_brace + 1]

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 返回的内容不是合法 JSON：{e}\n原始内容：{raw_text}") from e

    if not isinstance(data, dict):
        raise ValueError(f"LLM 返回的 JSON 顶层必须是对象，实际类型：{type(data)}")

    return data


def _chapter_role_output_path(chapter_path: Path) -> Path:
    """
    根据章节路径计算对应的“章节角色信息”输出路径，并确保目录存在。

    目录结构示例：
    - 章节文件：.../chapter/第1章.txt
    - 输出文件：.../chapter/chapter_role/第1章_角色信息.json
    """
    chapter_dir = chapter_path.parent
    out_dir = chapter_dir / "chapter_role"
    out_dir.mkdir(parents=True, exist_ok=True)

    chapter_name = chapter_path.stem  # 例如 "第1章"
    filename = f"{chapter_name}_角色信息.json"
    return out_dir / filename


def analyze_chapter_and_update_roles(
    chapter_path: str | Path,
    llm: Optional[BaseChatModel] = None,
    role_manager: Optional[object] = None,
) -> List[AnalyzeResult]:
    """
    对指定章节进行角色分析，并将角色状态按章节单独保存为 JSON 文件。

    参数：
    - chapter_path: 章节文本文件路径。
    - llm: 可选，自定义的 langchain Chat 模型实例；如果为 None，则使用默认配置。
    - role_manager: 已不再使用，仅为兼容旧接口而保留，可传入 None。

    返回：
    - AnalyzeResult 列表，每个元素记录本章角色状态文件的路径及涉及的角色名。
    """

    chapter_path = Path(chapter_path)
    text = _load_chapter_text(chapter_path)

    schema = _load_schema()

    if llm is None:
        llm = get_default_llm()

    parsed = _call_llm_for_roles(llm, schema, text)

    # 规范化输出结构，仅保留我们需要的字段
    chapter_title = str(parsed.get("chapter_title") or chapter_path.stem)
    roles_raw = parsed.get("roles") or []

    roles: List[Dict[str, Any]] = []
    role_names: List[str] = []

    for role_info in roles_raw:
        if not isinstance(role_info, dict):
            continue

        name = str(role_info.get("name", "")).strip()
        if not name:
            # 姓名是必填项，没有姓名就跳过该条
            continue

        gender = role_info.get("gender")
        location = role_info.get("location")
        events = role_info.get("events")
        final_status = role_info.get("final_status")

        roles.append(
            {
                "name": name,
                "gender": gender,
                "location": location,
                "events": events,
                "final_status": final_status,
            }
        )
        role_names.append(name)

    output_payload = {
        "chapter_title": chapter_title,
        "chapter_file": chapter_path.name,
        "roles": roles,
    }

    out_path = _chapter_role_output_path(chapter_path)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output_payload, f, ensure_ascii=False, indent=2)

    return [
        AnalyzeResult(
            chapter_path=chapter_path,
            output_path=out_path,
            role_names=role_names,
        )
    ]


__all__ = [
    "AnalyzeResult",
    "analyze_chapter_and_update_roles",
    "get_default_llm",
]

