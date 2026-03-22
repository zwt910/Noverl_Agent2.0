from __future__ import annotations

from 后端.chat_session import NovelChatSession, WELCOME_HINT, select_novel_by_menu_choice
from 后端.novel_manager import list_novels


def _select_or_create_novel_chat():
    novels = list_novels()
    print("\n=== 对话模式：选择作品 ===")
    if novels:
        print("已有作品：")
        for idx, name in enumerate(novels, start=1):
            print(f"{idx}. {name}")
    else:
        print("当前还没有任何作品。")

    print("0. 新建作品")
    while True:
        raw = input("请输入选项编号：").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("请输入有效数字。")
            continue

        if choice == 0:
            name = input("请输入新作品名称：").strip()
            return select_novel_by_menu_choice(0, new_name=name)
        if 1 <= choice <= len(novels):
            return select_novel_by_menu_choice(choice)
        print("无效选项，请重新输入。")


def main() -> None:
    print("欢迎进入「小说创作辅助」多智能体对话模式。")
    novel_ctx = _select_or_create_novel_chat()
    print(f"\n当前作品：{novel_ctx.novel_name}")

    session = NovelChatSession(novel_ctx)

    print(
        "\n团队分工：\n"
        "  · 主编 — 与你对话、理解需求、追问缺省信息并派发任务\n"
        "  · 编剧 — 创作 / 修改剧情大纲\n"
        "  · 写手 — 按大纲写正文草稿，或在会话内改草稿（未保存）\n"
        "  · 编辑 — 润色磁盘上已保存的章节\n"
        f"\n{WELCOME_HINT}\n"
        "输入「切换作品」可换书；「退出」结束。\n"
    )
    if session.state.dialogue_memory:
        print("（已载入本书近期对话记录，主编与各角色可延续上下文。）\n")

    while True:
        user = input("\n你：").strip()
        if not user:
            continue

        result = session.handle_user_message(user)
        for m in result.messages:
            print(f"{m.role}：", m.content)

        if result.needs_novel_picker:
            novel_ctx = _select_or_create_novel_chat()
            session.apply_novel_context(novel_ctx)
            print(f"主编：已切换到作品「{novel_ctx.novel_name}」。")
            continue

        if result.session_ended:
            break


if __name__ == "__main__":
    main()
