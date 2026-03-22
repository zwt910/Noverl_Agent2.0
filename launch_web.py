"""
从项目根目录一键启动 Web（与双击 run_web.bat 等价）。
会自动切换工作目录到 src 并执行 main_web，从而在默认浏览器中打开产品页。
"""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(root, "src")
    main_web = os.path.join(src, "main_web.py")
    if not os.path.isfile(main_web):
        print(f"找不到 {main_web}，请在项目根目录运行。", file=sys.stderr)
        sys.exit(1)
    os.chdir(src)
    if src not in sys.path:
        sys.path.insert(0, src)
    runpy.run_path(main_web, run_name="__main__")


if __name__ == "__main__":
    main()
