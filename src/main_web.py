"""
本地 Web 入口：提供 REST API + 静态前端。

推荐一键启动（自动在系统默认浏览器中打开产品页）：
    1）在项目根目录双击 run_web.bat
    2）或在项目根目录执行： python launch_web.py
    3）或在 src 目录执行： python main_web.py

依赖：pip install -r requirements.txt（或仅 Web：requirements-web.txt）

开发时可手动（不自动开浏览器、可热重载）：
    cd src && uvicorn main_web:app --reload --host 127.0.0.1 --port 8765

环境变量：
- NOVERL_NO_BROWSER=1：不自动打开浏览器
- NOVERL_PORT=端口号：强制使用该端口（被占用则启动失败）
- 未设置 NOVERL_PORT 时：若 8765 已被占用，会自动尝试 8766、8767… 直至可用
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from 后端.chat_session import NovelChatSession, TurnResult, WELCOME_HINT, select_novel_existing
from 后端.novel_browser import read_chapter_text, read_outline_text
from 后端.novel_files import (
    create_chapter_file,
    create_main_plot_file,
    create_outline_file,
    delete_chapter_file,
    delete_main_plot_file,
    delete_outline_file,
    list_chapter_files,
    list_main_plot_files,
    list_outline_files,
    read_chapter_file,
    read_main_plot_named,
    read_outline_file,
    rename_chapter_file,
    rename_main_plot_file,
    rename_outline_file,
    write_chapter_file,
    write_intro,
    write_main_plot_named,
    write_outline_file,
)
from 后端.novel_manager import create_novel, list_novels, load_intro, load_main_plot

ROOT_DIR = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT_DIR / "web"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
_PORT_SCAN_ATTEMPTS = 40

app = FastAPI(title="Noverl Agent", version="1.0")

_sessions: dict[str, NovelChatSession] = {}

SESSION_EXPIRED_DETAIL = {
    "code": "session_expired",
    "message": "会话不存在或已失效，请重新选择作品",
}

_LOCK_REGISTRY = threading.Lock()
_session_locks: dict[str, threading.Lock] = {}


def raise_session_not_found() -> None:
    raise HTTPException(status_code=404, detail=SESSION_EXPIRED_DETAIL)


def _session_lock(session_id: str) -> threading.Lock:
    with _LOCK_REGISTRY:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.Lock()
        return _session_locks[session_id]


def _map_chat_exception(exc: BaseException) -> tuple[str, str]:
    s = str(exc).lower()
    if "timeout" in s or "timed out" in s:
        return "timeout", "调用模型超时，请稍后重试或检查网络。"
    if (
        "401" in s
        or "unauthorized" in s
        or "invalid api key" in s
        or ("api key" in s and "invalid" in s)
    ):
        return "auth", "API 密钥无效或未配置，请检查环境变量与 .env。"
    if "429" in s or "rate limit" in s or "too many requests" in s:
        return "rate_limit", "请求过于频繁或额度不足，请稍后重试。"
    return "llm_error", f"处理失败：{exc!s}。请检查网络、模型服务与密钥后重试。"


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class NovelSelectBody(BaseModel):
    """mode=new 时创建名为 name 的作品；mode=existing 时切换到已有 name。"""

    mode: str = Field(pattern="^(new|existing)$")
    name: str = Field(min_length=1, max_length=200)


class ChatMessageBody(BaseModel):
    message: str = Field(default="", max_length=200_000)


class OutlineWizardBody(BaseModel):
    """剧情大纲创作向导：与主编「创作剧情大纲」所需参数一致。"""

    chapter: int = Field(ge=1, le=9999)
    requirements: str = Field(default="", max_length=50_000)
    prev_chapters: int = Field(ge=1, le=80)


class FileSaveBody(BaseModel):
    filename: str = Field(min_length=5, max_length=220)
    content: str = Field(default="", max_length=2_000_000)


class FileRenameBody(BaseModel):
    old_name: str = Field(min_length=5, max_length=220)
    new_name: str = Field(min_length=5, max_length=220)


class IntroSaveBody(BaseModel):
    content: str = Field(default="", max_length=500_000)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/novels")
def api_novels() -> dict:
    return {"novels": list_novels()}


@app.post("/api/session")
def api_create_session(body: NovelSelectBody) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="作品名称不能为空")
    try:
        if body.mode == "new":
            ctx = create_novel(name)
        else:
            ctx = select_novel_existing(name)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"作品不存在：{name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e))

    sid = str(uuid.uuid4())
    sess = NovelChatSession(ctx)
    _sessions[sid] = sess
    return {
        "session_id": sid,
        "novel_name": ctx.novel_name,
        "welcome_hint": WELCOME_HINT,
        "has_memory": bool(sess.state.dialogue_memory),
    }


@app.get("/api/session/{session_id}")
def api_session_info(session_id: str) -> dict:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()
    agent_key = sess.active
    return {
        "novel_name": sess.novel_name,
        "active_agent": agent_key,
    }


def _require_chat_session(session_id: str) -> NovelChatSession:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()
    if sess.state.novel_ctx is None:
        raise HTTPException(status_code=400, detail="未绑定作品")
    return sess


@app.get("/api/session/{session_id}/browse/chapters")
def browse_list_chapters(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    return {"items": list_chapter_files(sess.state.novel_ctx)}


@app.get("/api/session/{session_id}/browse/chapters/{chapter:int}")
def browse_chapter_content(session_id: str, chapter: int) -> dict:
    sess = _require_chat_session(session_id)
    try:
        content = read_chapter_text(sess.state.novel_ctx, chapter)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"未找到第{chapter}章正文")
    return {
        "chapter": chapter,
        "title": f"第{chapter}章 · 正文",
        "content": content,
    }


@app.get("/api/session/{session_id}/browse/outlines")
def browse_list_outlines(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    return {"items": list_outline_files(sess.state.novel_ctx)}


@app.get("/api/session/{session_id}/browse/outlines/{chapter:int}")
def browse_outline_content(session_id: str, chapter: int) -> dict:
    sess = _require_chat_session(session_id)
    try:
        content = read_outline_text(sess.state.novel_ctx, chapter)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"未找到第{chapter}章剧情大纲文件")
    return {
        "chapter": chapter,
        "title": f"第{chapter}章 · 剧情大纲",
        "content": content,
    }


@app.get("/api/session/{session_id}/browse/intro")
def browse_intro(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    return {
        "title": "小说简介",
        "content": load_intro(sess.state.novel_ctx),
    }


@app.get("/api/session/{session_id}/browse/main-plots")
def browse_list_main_plots(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    return {"items": list_main_plot_files(sess.state.novel_ctx)}


@app.get("/api/session/{session_id}/browse/main-plots/content")
def browse_main_plot_one(session_id: str, filename: str = Query(..., min_length=5, max_length=220)) -> dict:
    sess = _require_chat_session(session_id)
    try:
        content = read_main_plot_named(sess.state.novel_ctx, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="未找到该主线文件") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    stem = filename[:-4] if filename.lower().endswith(".txt") else filename
    return {"title": stem, "filename": filename, "content": content}


@app.put("/api/session/{session_id}/browse/main-plots/content")
def browse_save_main_plot(session_id: str, body: FileSaveBody) -> dict:
    sess = _require_chat_session(session_id)
    try:
        write_main_plot_named(sess.state.novel_ctx, body.filename, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/api/session/{session_id}/browse/main-plots/create")
def browse_create_main_plot(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    name = create_main_plot_file(sess.state.novel_ctx)
    return {"filename": name}


@app.delete("/api/session/{session_id}/browse/main-plots/file")
def browse_delete_main_plot(session_id: str, filename: str = Query(..., min_length=5, max_length=220)) -> dict:
    sess = _require_chat_session(session_id)
    try:
        delete_main_plot_file(sess.state.novel_ctx, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/api/session/{session_id}/browse/main-plots/rename")
def browse_rename_main_plot(session_id: str, body: FileRenameBody) -> dict:
    sess = _require_chat_session(session_id)
    try:
        new_name = rename_main_plot_file(sess.state.novel_ctx, body.old_name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原文件不存在") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"filename": new_name}


@app.get("/api/session/{session_id}/browse/chapters/content")
def browse_chapter_by_name(session_id: str, filename: str = Query(..., min_length=5, max_length=220)) -> dict:
    sess = _require_chat_session(session_id)
    try:
        content = read_chapter_file(sess.state.novel_ctx, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="未找到该章节文件") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"filename": filename, "title": filename[:-4], "content": content}


@app.put("/api/session/{session_id}/browse/chapters/content")
def browse_save_chapter(session_id: str, body: FileSaveBody) -> dict:
    sess = _require_chat_session(session_id)
    try:
        write_chapter_file(sess.state.novel_ctx, body.filename, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/api/session/{session_id}/browse/chapters/create")
def browse_create_chapter(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    name = create_chapter_file(sess.state.novel_ctx)
    return {"filename": name}


@app.delete("/api/session/{session_id}/browse/chapters/file")
def browse_delete_chapter(session_id: str, filename: str = Query(..., min_length=5, max_length=220)) -> dict:
    sess = _require_chat_session(session_id)
    try:
        delete_chapter_file(sess.state.novel_ctx, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/api/session/{session_id}/browse/chapters/rename")
def browse_rename_chapter(session_id: str, body: FileRenameBody) -> dict:
    sess = _require_chat_session(session_id)
    try:
        new_name = rename_chapter_file(sess.state.novel_ctx, body.old_name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原文件不存在") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"filename": new_name}


@app.get("/api/session/{session_id}/browse/outlines/content")
def browse_outline_by_name(session_id: str, filename: str = Query(..., min_length=5, max_length=220)) -> dict:
    sess = _require_chat_session(session_id)
    try:
        content = read_outline_file(sess.state.novel_ctx, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="未找到该大纲文件") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"filename": filename, "title": filename[:-4], "content": content}


@app.put("/api/session/{session_id}/browse/outlines/content")
def browse_save_outline(session_id: str, body: FileSaveBody) -> dict:
    sess = _require_chat_session(session_id)
    try:
        write_outline_file(sess.state.novel_ctx, body.filename, body.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/api/session/{session_id}/browse/outlines/create")
def browse_create_outline(session_id: str) -> dict:
    sess = _require_chat_session(session_id)
    name = create_outline_file(sess.state.novel_ctx)
    return {"filename": name}


@app.delete("/api/session/{session_id}/browse/outlines/file")
def browse_delete_outline(session_id: str, filename: str = Query(..., min_length=5, max_length=220)) -> dict:
    sess = _require_chat_session(session_id)
    try:
        delete_outline_file(sess.state.novel_ctx, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@app.post("/api/session/{session_id}/browse/outlines/rename")
def browse_rename_outline(session_id: str, body: FileRenameBody) -> dict:
    sess = _require_chat_session(session_id)
    try:
        new_name = rename_outline_file(sess.state.novel_ctx, body.old_name, body.new_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="原文件不存在") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"filename": new_name}


@app.put("/api/session/{session_id}/browse/intro")
def browse_save_intro(session_id: str, body: IntroSaveBody) -> dict:
    sess = _require_chat_session(session_id)
    write_intro(sess.state.novel_ctx, body.content)
    return {"ok": True}


def _turn_result_to_json(result: TurnResult) -> dict:
    return {
        "messages": [{"role": m.role, "content": m.content} for m in result.messages],
        "active_agent": result.active_agent,
        "novel_name": result.novel_name,
        "needs_novel_picker": result.needs_novel_picker,
        "session_ended": result.session_ended,
    }


@app.post("/api/session/{session_id}/message")
def api_chat(session_id: str, body: ChatMessageBody) -> dict:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()

    with _session_lock(session_id):
        try:
            result = sess.handle_user_message(body.message)
        except Exception as e:
            code, msg = _map_chat_exception(e)
            raise HTTPException(
                status_code=502,
                detail={"code": code, "message": msg},
            ) from e
    return _turn_result_to_json(result)


def _sse_pack(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.post("/api/session/{session_id}/message/stream")
def api_chat_stream(session_id: str, body: ChatMessageBody) -> StreamingResponse:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()

    def event_gen():
        with _session_lock(session_id):
            try:
                for kind, payload in sess.iter_chat_events(body.message):
                    if kind == "progress":
                        yield _sse_pack({"type": "progress", "text": payload})
                    elif kind == "message":
                        yield _sse_pack(
                            {
                                "type": "message",
                                "role": payload.role,
                                "content": payload.content,
                            }
                        )
                    elif kind == "creative_preview":
                        yield _sse_pack({"type": "creative_preview", **payload})
                    elif kind == "preview_navigate":
                        yield _sse_pack({"type": "preview_navigate", **payload})
                    elif kind == "done":
                        yield _sse_pack({"type": "done", **payload})
            except Exception as e:
                code, msg = _map_chat_exception(e)
                yield _sse_pack({"type": "error", "code": code, "message": msg})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.post("/api/session/{session_id}/message/outline-wizard")
def api_outline_wizard(session_id: str, body: OutlineWizardBody) -> dict:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()

    with _session_lock(session_id):
        try:
            result = sess.handle_outline_wizard(
                body.chapter,
                body.requirements,
                body.prev_chapters,
            )
        except Exception as e:
            code, msg = _map_chat_exception(e)
            raise HTTPException(
                status_code=502,
                detail={"code": code, "message": msg},
            ) from e
    return _turn_result_to_json(result)


@app.post("/api/session/{session_id}/message/outline-wizard/stream")
def api_outline_wizard_stream(session_id: str, body: OutlineWizardBody) -> StreamingResponse:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()

    def event_gen():
        with _session_lock(session_id):
            try:
                for kind, payload in sess.iter_outline_wizard_events(
                    body.chapter,
                    body.requirements,
                    body.prev_chapters,
                ):
                    if kind == "progress":
                        yield _sse_pack({"type": "progress", "text": payload})
                    elif kind == "message":
                        yield _sse_pack(
                            {
                                "type": "message",
                                "role": payload.role,
                                "content": payload.content,
                            }
                        )
                    elif kind == "creative_preview":
                        yield _sse_pack({"type": "creative_preview", **payload})
                    elif kind == "done":
                        yield _sse_pack({"type": "done", **payload})
            except Exception as e:
                code, msg = _map_chat_exception(e)
                yield _sse_pack({"type": "error", "code": code, "message": msg})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.post("/api/session/{session_id}/novel")
def api_switch_novel(session_id: str, body: NovelSelectBody) -> dict:
    sess = _sessions.get(session_id)
    if sess is None:
        raise_session_not_found()

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="作品名称不能为空")
    try:
        if body.mode == "new":
            ctx = create_novel(name)
        else:
            ctx = select_novel_existing(name)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"作品不存在：{name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=400, detail=str(e))

    sess.apply_novel_context(ctx)
    return {
        "ok": True,
        "novel_name": ctx.novel_name,
        "welcome_hint": WELCOME_HINT,
        "has_memory": bool(sess.state.dialogue_memory),
    }


@app.get("/")
def serve_index() -> FileResponse:
    index = WEB_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=500, detail=f"缺少前端文件：{index}")
    return FileResponse(index)


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _schedule_open_browser(host: str, port: int, delay_sec: float = 1.25) -> None:
    import threading
    import webbrowser

    if os.environ.get("NOVERL_NO_BROWSER", "").strip() in ("1", "true", "yes"):
        return

    url = f"http://{host}:{port}/"

    def _open() -> None:
        webbrowser.open(url)

    threading.Timer(delay_sec, _open).start()


def _find_bindable_port(host: str, start_port: int, attempts: int = _PORT_SCAN_ATTEMPTS) -> int:
    """
    从 start_port 起尝试绑定，避免 WinError 10048（端口已被占用）。
    """
    for p in range(start_port, start_port + attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, p))
        except OSError:
            continue
        return p
    raise RuntimeError(
        f"在 {host} 上无法绑定端口 {start_port}～{start_port + attempts - 1}，"
        "请关闭占用端口的进程或设置环境变量 NOVERL_PORT 指定其它端口。"
    )


def resolve_listen_port(host: str) -> int:
    """
    决定实际监听端口：优先环境变量 NOVERL_PORT；否则从 DEFAULT_PORT 起自动避让占用。
    """
    raw = os.environ.get("NOVERL_PORT", "").strip()
    if raw:
        p = int(raw)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, p))
        except OSError as e:
            raise RuntimeError(
                f"环境变量 NOVERL_PORT={p} 无法绑定到 {host}：{e}。"
                "请换端口或结束占用该端口的程序。"
            ) from e
        return p
    return _find_bindable_port(host, DEFAULT_PORT)


def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: int | None = None,
    open_browser: bool = True,
    reload: bool = False,
) -> None:
    """启动 uvicorn；默认自动延时打开浏览器（reload=True 时建议关闭 open_browser）。"""
    import uvicorn

    listen_port = port if port is not None else resolve_listen_port(host)
    if listen_port != DEFAULT_PORT and not os.environ.get("NOVERL_PORT"):
        print(
            f"[Noverl] 端口 {DEFAULT_PORT} 已被占用，已自动改用 {listen_port}。"
            f" 请在浏览器访问 http://{host}:{listen_port}/",
            file=sys.stderr,
        )

    if open_browser and not reload:
        _schedule_open_browser(host, listen_port)

    if reload:
        uvicorn.run(
            "main_web:app",
            host=host,
            port=listen_port,
            reload=True,
        )
    else:
        uvicorn.run(app, host=host, port=listen_port, reload=False)


if __name__ == "__main__":
    run_server(open_browser=True, reload=False)
