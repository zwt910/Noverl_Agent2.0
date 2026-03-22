from __future__ import annotations

"""
简单的命令行 Demo：使用第 1 章章节文本测试角色分析与更新流程。

运行方式示例：
- 在项目根目录下直接运行：
    python N_Agent/src/算法/demo_role_analyzer.py
- 或使用模块方式运行：
    python -m N_Agent.src.算法.demo_role_analyzer
"""

from pathlib import Path
import sys

# 确保项目根目录（包含 N_Agent 的目录）在 sys.path 中
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from N_Agent.src.算法.role_analyzer import (
    AnalyzeResult,
    analyze_chapter_and_update_roles,
)


def main() -> None:
    # 当前文件路径：N_Agent/src/算法/demo_role_analyzer.py
    # parents[0] -> 算法
    # parents[1] -> src
    # parents[2] -> N_Agent
    project_root = Path(__file__).resolve().parents[2]

    chapter_path = project_root / "data/novels/test_novel_name1/chapter/第5章.txt"

    print(f"使用章节文件：{chapter_path}")

    results = analyze_chapter_and_update_roles(chapter_path)

    print("\n分析完成。章节角色状态文件：\n")
    for r in results:
        assert isinstance(r, AnalyzeResult)
        print(f"- 章节：{r.chapter_path.name}")
        print(f"  输出文件：{r.output_path}")
        if r.role_names:
            print(f"  角色列表：{', '.join(r.role_names)}")


def json_dumps_pretty(data) -> str:
    try:
        import json
    except ImportError:  # 理论上不会发生
        return str(data)

    return json.dumps(data, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

