# Noverl Agent（小说创作智能体）Web 对话 Demo

基于大语言模型的小说创作辅助工具：在浏览器中与「主编」及子智能体对话，管理作品、章节大纲与正文，并在中间区域预览与编辑文档。

---

## 环境要求

- Python 3.10+（推荐 3.12）
- 可访问的 **OpenAI 兼容** Chat Completions API（需自行准备 Key 与网关地址）

---

## 安装依赖

在项目根目录执行：

```bash
pip install -r requirements.txt
```

若仅需最小 Web 依赖，可使用 `requirements-web.txt`。

---

## 配置 API Key 与 Base URL

大模型通过 `langchain_openai.ChatOpenAI` 调用，配置在 `src/算法/config_llm.py` 中统一解析。

### 1. API Key（必填，二选一）

| 变量名 | 说明 |
|--------|------|
| `NOVEL_AGENT_API_KEY` | 本项目推荐名称 |
| `OPENAI_API_KEY` | 与常见 OpenAI 兼容客户端一致 |

未配置时，服务端调用模型会失败（例如出现未授权相关错误）。

### 2. Base URL（可选）

| 变量名 | 说明 |
|--------|------|
| `NOVEL_AGENT_BASE_URL` | 覆盖默认 API 根地址 |
| `OPENAI_BASE_URL` | 同上，任选其一 |

未设置时，使用代码中的默认网关（见 `config_llm.py` 内 `_default_base_url()`）。

### 3. 模型名称（可选）

| 变量名 | 说明 |
|--------|------|
| `NOVEL_AGENT_MODEL` | 覆盖默认模型名；不设则使用 `config_llm.py` 中的默认模型 |

### 4. 使用 `.env`（推荐）

1. 复制仓库中的 `ENV.example`，在项目**根目录**保存为 `.env`（文件名即为 `.env`，勿提交到 Git）。
2. 按示例填入 `NOVEL_AGENT_API_KEY`（或 `OPENAI_API_KEY`），按需填写 `NOVEL_AGENT_BASE_URL` / `OPENAI_BASE_URL`、`NOVEL_AGENT_MODEL`。

程序在导入 `config_llm` 时会读取根目录 `.env`，**不会覆盖**你已在操作系统中设置过的同名环境变量。

### 5. 使用终端环境变量（示例）

**Linux / macOS：**

```bash
export NOVEL_AGENT_API_KEY="你的密钥"
export NOVEL_AGENT_BASE_URL="https://你的网关/v1"   # 可选
```

**Windows PowerShell：**

```powershell
$env:NOVEL_AGENT_API_KEY = "你的密钥"
$env:NOVEL_AGENT_BASE_URL = "https://你的网关/v1"   # 可选
```

---

## 启动「对话模式」（Web 产品）

本仓库的**主产品形态**为本地 Web：左侧导航、中间预览与编辑、右侧对话流式输出（SSE）。

任选一种方式启动（均在配置好 API 之后）：

1. **Windows**：双击项目根目录下的 `run_web.bat`
2. **跨平台**：在项目根目录执行 `python launch_web.py`
3. **直接运行入口**：进入 `src` 目录后执行 `python main_web.py`

启动成功后，一般会自动打开系统默认浏览器；若未打开，请查看终端输出的本地地址（默认主机 `127.0.0.1`，端口优先 `8765`，若被占用会递增尝试）。

**可选环境变量：**

| 变量 | 作用 |
|------|------|
| `NOVERL_NO_BROWSER=1` | 启动时不自动打开浏览器 |
| `NOVERL_PORT=端口号` | 固定端口；若端口被占用则启动失败 |

**开发模式（热重载、不自动开浏览器）：**

```bash
cd src
uvicorn main_web:app --reload --host 127.0.0.1 --port 8765
```

---

## 对话模式：功能说明与使用方式

### 整体布局

- **左侧**：作品与资源导航（章节、大纲、剧情纲要、简介等模块）。
- **中间**：**预览 Tab**（可在「章节正文 / 剧情大纲 / 剧情纲要 / 作品简介 / 对话创作」等视图间切换），支持查看与编辑、保存等与后端同步的操作（具体按钮以页面为准）。
- **右侧**：与智能体**流式对话**；界面中「主编」对应内部的导航/统筹角色，系统会根据你的意图在**大纲、正文撰写、正文优化**等子能力间路由。

### 推荐使用流程

1. 在页面中**选择或新建作品**，确保会话有效。
2. 在右侧用**自然语言**说明需求（例如：写某章大纲、写正文、修改润色、查看某章内容等）。
3. 当对话中涉及「查看章节正文 / 查看剧情大纲」等意图时，后端会通过 SSE 触发**预览区跳转**，左侧对应模块会展开，便于你对照编辑。
4. 在中间 Tab 中可**手动切换**预览类型，配合左侧文件列表选择具体章节或大纲文件。

### 命令行对话（可选）

若需**终端版**交互，可在配置好 `PYTHONPATH` 与 API 环境变量后运行 `src/main_chat.py`（详见仓库内 `USAGE.md`）。与 Web 共用同一套 `config_llm` 配置逻辑。

---

## 其他说明

- 当前仓库为 **2.0 版本的 Demo**，能力与体验**尚未完善**，后续会持续迭代。
- **部分后端或算法能力未完全体现在前端界面**中；若需了解完整行为、路由与扩展点，请直接阅读源码（例如 `src/main_web.py`、`src/后端/chat_session.py`、`web/app.js` 等）。
- **切勿**将真实 API Key、`.env` 或含密钥的配置提交到 GitHub；`.env` 应保留在本地并已加入 `.gitignore`。

---

## 许可证

本项目未附带许可证文件；如需开源发布请自行补充 `LICENSE`。
