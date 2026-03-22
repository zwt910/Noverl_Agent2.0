from __future__ import annotations

"""
基于 JSON 文件的简单角色管理器。

功能概述：
- 使用固定的 JSON 结构存储小说中的角色信息，便于后续增删改查。
- 角色字段为固定 9 项：姓名、性别、年龄、身份、背景、性格特点、关系网、当前状态、人物发展。
- 通过角色姓名作为唯一标识。

存储格式（示例）：
{
  "roles": {
    "张三": {
      "姓名": "张三",
      "性别": "男",
      "年龄": 25,
      "身份": "剑士",
      "背景": "出身小镇",
      "性格特点": "冷静、果断",
      "关系网": "与李四为好友",
      "当前状态": "重伤，位于王都城门",
      "人物发展": "从懵懂少年成长为剑道高手"
    },
    "李四": { ... }
  }
}

注意：
- 实际存储时，只会写入你显式设置过的字段，未设置的字段不会出现在 JSON 中。
- 读取时，支持“查看指定角色的全部非空信息”（自动过滤掉空值/空对象）。
"""

# 角色信息表的固定字段列表（姓名必填，其余可选）
ROLE_FIELDS = frozenset({
    "姓名", "性别", "年龄", "身份", "背景", "性格特点", "关系网", "当前状态", "人物发展"
})

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional


class JsonRoleManagerError(Exception):
    """JSON 角色管理器通用异常基类。"""


class RoleNotFoundError(JsonRoleManagerError):
    """未找到指定角色。"""


class DuplicateRoleNameError(JsonRoleManagerError):
    """角色姓名重复。"""


class InvalidFieldError(JsonRoleManagerError):
    """无效的字段名（不在固定字段列表中）。"""


@dataclass
class JsonRoleManagerConfig:
    """
    JSON 角色管理器配置。

    参数：
    - storage_path: 角色信息 JSON 文件的路径（必填，由外部传入）。
    """

    storage_path: Path


class JsonRoleManager:
    """
    以 JSON 文件为存储介质的小说角色管理器。

    交互能力：
    1. 支持查看指定角色的全部非空信息。
    2. 支持修改指定角色的某个字段的信息（如果修改的是姓名，
       则只有当姓名与其他角色不重复时才允许修改）。
    3. 支持添加角色的某个非空字段的信息（角色不存在时会自动创建，
       并且会自动填入 姓名 = 角色名）。
    4. 支持删除某个角色的全部信息。
    5. 支持删除某个角色的指定字段信息，即将这个字段的信息置为空
       （内部用 None 表示，读取时会过滤掉）。

    字段约定（共 9 项）：
    - 姓名（必填，作为唯一标识）
    - 性别、年龄、身份、背景、性格特点、关系网、当前状态、人物发展（均为可选）
    """
    _VALID_FIELDS = ROLE_FIELDS

    def __init__(self, config: JsonRoleManagerConfig) -> None:
        self.config = config
        self._roles: Dict[str, Dict[str, Any]] = self._load_roles(self.config.storage_path)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def get_role_info(self, role_name: str) -> Dict[str, Any]:
        """
        查看指定角色的全部非空信息。

        返回的数据结构与存储结构类似，但会自动过滤掉：
        - 值为 None 的字段
        - 空字典 / 空列表
        """
        role = self._roles.get(role_name)
        if role is None:
            raise RoleNotFoundError(f"角色 '{role_name}' 不存在。")

        return self._filter_non_empty(role)

    def update_role_field(self, role_name: str, field_name: str, new_value: Any) -> None:
        """
        修改指定角色的某个字段的信息。

        - 如果修改的是姓名字段，则会检查新姓名是否与其他角色重复。
        - field_name 必须是有效字段（姓名、性别、年龄、身份、背景、性格特点、关系网、当前状态、人物发展）。
        """
        self._ensure_field_valid(field_name)

        if role_name not in self._roles:
            raise RoleNotFoundError(f"角色 '{role_name}' 不存在。")

        role_data = self._roles[role_name]

        # 特殊处理：修改姓名
        if field_name == "姓名":
            new_name = str(new_value).strip()
            if not new_name:
                raise ValueError("角色姓名不能为空。")
            if new_name != role_name and new_name in self._roles:
                raise DuplicateRoleNameError(f"角色姓名 '{new_name}' 已存在，不能重复。")

            role_data["姓名"] = new_name
            del self._roles[role_name]
            self._roles[new_name] = role_data
        else:
            role_data[field_name] = new_value
            self._roles[role_name] = role_data

        self._save_roles()

    def add_role_field(self, role_name: str, field_name: str, value: Any) -> None:
        """
        添加角色的某个非空字段信息。

        - 如果角色不存在，则会创建一个新角色。
        - 新角色会自动设置 姓名 = role_name（如果尚未设置）。
        """
        self._ensure_field_valid(field_name)

        role_data = self._roles.get(role_name)
        if role_data is None:
            role_data = {}

        # 确保姓名存在
        if "姓名" not in role_data or not str(role_data.get("姓名") or "").strip():
            if role_name in self._roles:
                raise DuplicateRoleNameError(f"角色姓名 '{role_name}' 已存在。")
            role_data["姓名"] = role_name

        # 如果是添加姓名字段，还要做重复检查
        if field_name == "姓名":
            new_name = str(value).strip()
            if not new_name:
                raise ValueError("角色姓名不能为空。")
            if new_name != role_name and new_name in self._roles:
                raise DuplicateRoleNameError(f"角色姓名 '{new_name}' 已存在，不能重复。")
            role_data["姓名"] = new_name
            if role_name in self._roles:
                del self._roles[role_name]
            self._roles[new_name] = role_data
        else:
            role_data[field_name] = value
            self._roles[role_name] = role_data

        self._save_roles()

    def delete_role(self, role_name: str) -> None:
        """
        删除某个角色的全部信息。
        """
        if role_name not in self._roles:
            raise RoleNotFoundError(f"角色 '{role_name}' 不存在。")

        del self._roles[role_name]
        self._save_roles()

    def clear_role_field(self, role_name: str, field_name: str) -> None:
        """
        删除某个角色的指定字段信息，即将该字段置为空（None）。

        - field_name 必须是有效字段。
        - 对姓名字段置空时，仅清空字段，不改变字典 key；
          此时如果再次 get_role_info，该字段会被过滤掉。
        """
        self._ensure_field_valid(field_name)

        role_data = self._roles.get(role_name)
        if role_data is None:
            raise RoleNotFoundError(f"角色 '{role_name}' 不存在。")

        role_data[field_name] = None
        self._roles[role_name] = role_data
        self._save_roles()

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _ensure_field_valid(self, field_name: str) -> None:
        if field_name not in self._VALID_FIELDS:
            raise InvalidFieldError(
                f"字段 '{field_name}' 无效。有效字段为：{', '.join(sorted(self._VALID_FIELDS))}"
            )

    @staticmethod
    def _load_roles(storage_path: Path) -> Dict[str, Dict[str, Any]]:
        if not storage_path.exists():
            return {}

        with storage_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            # 兼容两种结构：
            # 1. { "roles": { name: role_data } }
            # 2. { name: role_data }
            if "roles" in data and isinstance(data["roles"], dict):
                return data["roles"]
            return data

        return {}

    def _save_roles(self) -> None:
        self.config.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"roles": self._roles}
        with self.config.storage_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _filter_non_empty(obj: Any) -> Any:
        """
        递归过滤掉：
        - 值为 None 的字段
        - 空字典 / 空列表
        """
        if obj is None:
            return None

        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                filtered = JsonRoleManager._filter_non_empty(v)
                if filtered is None:
                    continue
                if isinstance(filtered, (dict, list)) and not filtered:
                    continue
                result[k] = filtered
            return result

        if isinstance(obj, list):
            items = []
            for item in obj:
                filtered = JsonRoleManager._filter_non_empty(item)
                if filtered is None:
                    continue
                if isinstance(filtered, (dict, list)) and not filtered:
                    continue
                items.append(filtered)
            return items

        return obj


__all__ = [
    "ROLE_FIELDS",
    "JsonRoleManagerConfig",
    "JsonRoleManager",
    "JsonRoleManagerError",
    "RoleNotFoundError",
    "DuplicateRoleNameError",
    "InvalidFieldError",
]

