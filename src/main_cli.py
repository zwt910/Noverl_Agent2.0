from __future__ import annotations

from pathlib import Path
from typing import Optional

from 后端.chapter_cache import get_or_create_chapter_summary
from 后端.chapter_editor import (
    ChapterSessionState as EditorSessionState,  # type: ignore
    optimize_chapter,
    iterate_optimization,
    save_optimized_chapter,
)
from 后端.chapter_writer import (
    ChapterSessionState,
    generate_chapter,
    revise_chapter,
    save_chapter,
)
from 算法.create_plot import (
    OutlineSessionState,
    generate_outline,
    revise_outline,
    save_outline,
)
from 后端.chat_agents import normalize_outline_user_requirements
from 后端.novel_manager import (
    NovelContext,
    create_novel,
    list_novels,
    switch_novel,
)


def _input_int(prompt: str) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("请输入有效的数字。")


def _select_or_create_novel() -> NovelContext:
    novels = list_novels()
    print("\n=== 选择作品 ===")
    if novels:
        print("已有作品：")
        for idx, name in enumerate(novels, start=1):
            print(f"{idx}. {name}")
    else:
        print("当前还没有任何作品。")

    print("0. 新建作品")

    choice = _input_int("请输入选项编号：")
    if choice == 0:
        name = input("请输入新作品名称：").strip()
        return create_novel(name)

    if 1 <= choice <= len(novels):
        return switch_novel(novels[choice - 1])

    print("无效选项，将重新选择。")
    return _select_or_create_novel()


def _menu_main() -> None:
    print(
        "\n=== 小说智能体 CLI ===\n"
        "1. 剧情大纲生成\n"
        "2. 正文续写\n"
        "3. 正文优化修改\n"
        "4. 切换作品\n"
        "0. 退出程序\n"
    )


def _handle_outline_flow(novel_ctx: NovelContext) -> None:
    chapter_index = _input_int("请输入要创作大纲的章节号（整数）：")
    while True:
        user_req = input(
            "请输入对本章大纲的「用户要求」（无特别要求请直接输入「无」）：\n"
        ).strip()
        if user_req:
            break
        print("不能为空，请填写要求或输入「无」。")
    while True:
        prev_n = _input_int(
            "请输入要纳入剧情摘要的前文章节数（正整数，例如 5 表示向前最多参考 5 章）："
        )
        if prev_n >= 1:
            break
        print("请输入至少为 1 的正整数。")
    prev_n = min(prev_n, 80)

    state = generate_outline(
        novel_ctx,
        chapter_index,
        user_requirements=normalize_outline_user_requirements(user_req),
        window_size=prev_n,
    )
    while True:
        print("\n=== 当前大纲 ===\n")
        print(state.current_outline)
        print(
            "\n操作选项：\n"
            "1. 根据反馈修改大纲\n"
            "2. 对当前大纲满意并保存\n"
            "3. 放弃本次大纲并返回主菜单\n"
        )
        choice = _input_int("请输入选项编号：")
        if choice == 1:
            fb = input("请输入你的修改意见：\n")
            state = revise_outline(
                current_outline=state.current_outline,
                novel_ctx=novel_ctx,
                chapter_index=chapter_index,
                user_feedback=fb,
            )
        elif choice == 2:
            path = save_outline(state)
            print(f"大纲已保存到：{path}")
            return
        elif choice == 3:
            print("已放弃当前大纲，不做任何保存。")
            return
        else:
            print("无效选项，请重新选择。")


def _load_outline_text_if_exists(novel_ctx: NovelContext, chapter_index: int) -> Optional[str]:
    plot_dir = novel_ctx.novel_dir / "plot"
    fname = f"第{chapter_index}章_剧情大纲.txt"
    path = plot_dir / fname
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _handle_write_flow(novel_ctx: NovelContext) -> None:
    chapter_index = _input_int("请输入要创作正文的章节号（整数）：")
    outline_text = _load_outline_text_if_exists(novel_ctx, chapter_index)
    if not outline_text:
        print("未找到该章节的大纲，将尝试仅根据前文总结和简介进行创作，效果可能略差。")
        # 触发一次前几章总结的缓存生成，便于后续扩展使用
        if chapter_index > 1:
            try:
                get_or_create_chapter_summary(novel_ctx, chapter_index - 1)
            except FileNotFoundError:
                pass
        outline_text = input("如有简单大纲或提示，请在此输入（可留空）：\n")

    state: ChapterSessionState = generate_chapter(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        outline_text=outline_text,
    )

    while True:
        print("\n=== 当前正文 ===\n")
        print(state.current_text)
        print(
            "\n操作选项：\n"
            "1. 根据反馈修改正文\n"
            "2. 对当前正文满意并保存\n"
            "3. 放弃本次正文并返回主菜单\n"
        )
        choice = _input_int("请输入选项编号：")
        if choice == 1:
            fb = input("请输入你的修改要求：\n")
            state = revise_chapter(
                current_text=state.current_text,
                novel_ctx=novel_ctx,
                chapter_index=chapter_index,
                user_requirements=fb,
            )
        elif choice == 2:
            path = save_chapter(state, source_agent="writer")
            print(f"正文已保存到：{path}")
            return
        elif choice == 3:
            print("已放弃当前正文，不做任何保存。")
            return
        else:
            print("无效选项，请重新选择。")


def _handle_edit_flow(novel_ctx: NovelContext) -> None:
    chapter_index = _input_int("请输入要优化修改的章节号（整数）：")
    req = input("请输入你对本章的修改/优化要求（可留空）：\n")

    state = optimize_chapter(
        novel_ctx=novel_ctx,
        chapter_index=chapter_index,
        user_requirements=req,
    )

    while True:
        print("\n=== 当前优化后正文 ===\n")
        print(state.current_text)
        print(
            "\n操作选项：\n"
            "1. 根据新的反馈继续优化\n"
            "2. 对当前版本满意并保存\n"
            "3. 放弃本次修改并返回主菜单\n"
        )
        choice = _input_int("请输入选项编号：")
        if choice == 1:
            fb = input("请输入你的进一步修改/优化要求：\n")
            state = iterate_optimization(
                current_text=state.current_text,
                novel_ctx=novel_ctx,
                chapter_index=chapter_index,
                user_requirements=fb,
            )
        elif choice == 2:
            path = save_optimized_chapter(state)
            print(f"修改后的正文已保存到：{path}")
            return
        elif choice == 3:
            print("已放弃当前修改，不做任何保存。")
            return
        else:
            print("无效选项，请重新选择。")


def main() -> None:
    print("欢迎使用“小说创作辅助智能体”命令行版。")
    novel_ctx = _select_or_create_novel()
    print(f"当前作品：{novel_ctx.novel_name}")

    while True:
        _menu_main()
        choice = _input_int("请输入选项编号：")
        if choice == 0:
            print("已退出程序，未保存的内容将被丢弃。")
            break
        elif choice == 1:
            _handle_outline_flow(novel_ctx)
        elif choice == 2:
            _handle_write_flow(novel_ctx)
        elif choice == 3:
            _handle_edit_flow(novel_ctx)
        elif choice == 4:
            novel_ctx = _select_or_create_novel()
            print(f"已切换到作品：{novel_ctx.novel_name}")
        else:
            print("无效选项，请重新选择。")


if __name__ == "__main__":
    main()

