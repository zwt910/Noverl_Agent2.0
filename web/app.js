/**
 * Noverl Agent Web：三栏（导航 | 预览 Tab+编辑 | 对话）+ REST / SSE
 */

const STORAGE_KEY = "noverl_session_id";
const CHAT_TIMEOUT_MS = 180000;

const PREVIEW_TAB = {
  CHAPTERS: "chapters",
  OUTLINES: "outlines",
  MAIN: "main",
  INTRO: "intro",
  CHAT: "chat",
};

const previewState = {
  tab: PREVIEW_TAB.CHAPTERS,
  kind: null,
  filename: null,
  lastSavedContent: "",
  saveTimer: null,
  counts: { chapters: 0, outlines: 0, main: 0 },
};

const el = {
  messages: document.getElementById("messages"),
  composer: document.getElementById("composer"),
  input: document.getElementById("input"),
  status: document.getElementById("status"),
  btnSend: document.getElementById("btn-send"),
  sessionMeta: document.getElementById("session-meta"),
  novelPill: document.getElementById("novel-pill"),
  agentPill: document.getElementById("agent-pill"),
  btnSwitchNovel: document.getElementById("btn-switch-novel"),
  novelModal: document.getElementById("novel-modal"),
  novelSelect: document.getElementById("novel-select"),
  novelNewName: document.getElementById("novel-new-name"),
  btnOpenExisting: document.getElementById("btn-open-existing"),
  btnCreateNew: document.getElementById("btn-create-new"),
  modalError: document.getElementById("modal-error"),
  navSidebar: document.getElementById("nav-sidebar"),
  navHint: document.getElementById("nav-hint"),
  navChapters: document.getElementById("nav-chapters"),
  navOutlines: document.getElementById("nav-outlines"),
  navMainPlots: document.getElementById("nav-main-plots"),
  browseTitle: document.getElementById("browse-title"),
  browseMeta: document.getElementById("browse-meta"),
  browseEditor: document.getElementById("browse-editor"),
  browseFilenameRow: document.getElementById("browse-filename-row"),
  browseFilenameInput: document.getElementById("browse-filename-input"),
  previewTabs: document.getElementById("preview-tabs"),
  previewPanelFile: document.getElementById("preview-panel-file"),
  previewPanelChat: document.getElementById("preview-panel-chat"),
  previewScroll: document.getElementById("preview-scroll"),
  stagingCreative: document.getElementById("staging-creative"),
  stagingRoleLabel: document.getElementById("staging-role-label"),
  stagingMeta: document.getElementById("staging-meta"),
  stagingEditor: document.getElementById("staging-editor"),
  stagingEmpty: document.getElementById("staging-empty"),
  stagingSave: document.getElementById("staging-save"),
  stagingDiscard: document.getElementById("staging-discard"),
  btnPreviewDelete: document.getElementById("btn-preview-delete"),
  deleteConfirmModal: document.getElementById("delete-confirm-modal"),
  deleteConfirmMessage: document.getElementById("delete-confirm-message"),
  btnDeleteCancel: document.getElementById("btn-delete-cancel"),
  btnDeleteConfirm: document.getElementById("btn-delete-confirm"),
  btnRefreshNav: document.getElementById("btn-refresh-nav"),
  outlineWizard: document.getElementById("outline-wizard"),
  wizardChapter: document.getElementById("wizard-chapter"),
  wizardPrev: document.getElementById("wizard-prev"),
  wizardReq: document.getElementById("wizard-req"),
  btnOutlineWizard: document.getElementById("btn-outline-wizard"),
};

let sessionId = sessionStorage.getItem(STORAGE_KEY) || "";
let pendingDeleteAction = null;
/** @type {{ role: string, kind: string, chapter: number, filename: string } | null} */
let stagingState = null;

function stemFilename(name) {
  if (!name) return "";
  return name.toLowerCase().endsWith(".txt") ? name.slice(0, -4) : name;
}

function encFile(name) {
  return encodeURIComponent(name);
}

function roleClass(role) {
  const map = {
    用户: "msg-user",
    主编: "msg-主编",
    编剧: "msg-编剧",
    写手: "msg-写手",
    编辑: "msg-编辑",
    系统: "msg-系统",
  };
  return map[role] || "";
}

function clearStagingCreative() {
  stagingState = null;
  if (el.stagingCreative) el.stagingCreative.hidden = true;
  if (el.stagingEditor) el.stagingEditor.value = "";
  if (el.stagingEmpty) el.stagingEmpty.hidden = false;
}

/**
 * SSE creative_preview：跳转「对话创作」并展示全文供修改 / 保存 / 放弃。
 */
function applyCreativePreviewFromStream(payload) {
  if (!payload || !el.stagingCreative) return;
  stagingState = {
    role: String(payload.role || ""),
    kind: String(payload.kind || ""),
    chapter: Number(payload.chapter) || 0,
    filename: String(payload.filename || ""),
  };
  el.stagingRoleLabel.textContent = stagingState.role;
  el.stagingMeta.textContent = `第 ${stagingState.chapter} 章 · ${stagingState.filename}`;
  el.stagingEditor.value = payload.content ?? "";
  el.stagingCreative.hidden = false;
  if (el.stagingEmpty) el.stagingEmpty.hidden = true;
  setPreviewTab(PREVIEW_TAB.CHAT);
  if (el.previewScroll) {
    el.previewScroll.scrollTop = 0;
  }
}

function appendMessage(role, content) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${roleClass(role)}`;
  wrap.innerHTML = `<div class="msg-role">${escapeHtml(role)}</div><div class="msg-body">${escapeHtml(
    content
  )}</div>`;
  el.messages.appendChild(wrap);
  el.messages.scrollTop = el.messages.scrollHeight;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function setStatus(text) {
  el.status.textContent = text || "";
}

function showModal(show) {
  if (show) {
    el.novelModal.removeAttribute("hidden");
    el.modalError.hidden = true;
    el.modalError.textContent = "";
  } else {
    el.novelModal.setAttribute("hidden", "hidden");
  }
}

function showModalError(msg) {
  el.modalError.textContent = msg;
  el.modalError.hidden = false;
}

function showDeleteModal(show) {
  if (show) {
    el.deleteConfirmModal.removeAttribute("hidden");
  } else {
    el.deleteConfirmModal.setAttribute("hidden", "hidden");
    pendingDeleteAction = null;
  }
}

function updateSessionBar(data) {
  el.sessionMeta.hidden = false;
  el.novelPill.textContent = `作品：${data.novel_name || "—"}`;
  const agentMap = {
    navigator: "主编",
    outline: "编剧",
    writer: "写手",
    editor: "编辑",
  };
  el.agentPill.textContent = `当前模块：${agentMap[data.active_agent] || data.active_agent || "—"}`;
}

function setNavEnabled(on) {
  if (on) {
    el.navSidebar.classList.remove("nav-sidebar--disabled");
    el.navHint.textContent =
      "展开「章节」「剧情大纲」「当前主线」可管理文件，右侧 + 可新建；编剧/写手/编辑生成全文后会在「对话创作」中待确认，可改后保存或放弃；文件编辑区仍会自动保存。";
  } else {
    el.navSidebar.classList.add("nav-sidebar--disabled");
    el.navHint.textContent = "请先选择作品以浏览本书文件";
    el.navChapters.innerHTML = "";
    el.navOutlines.innerHTML = "";
    el.navMainPlots.innerHTML = "";
    el.navChapters.hidden = true;
    el.navOutlines.hidden = true;
    el.navMainPlots.hidden = true;
    document.querySelectorAll(".nav-module-head[data-toggle]").forEach((b) => {
      b.setAttribute("aria-expanded", "false");
    });
    updateNavAddVisibility();
  }
}

function clearNavActive() {
  el.navSidebar.querySelectorAll(".nav-sub button.is-active").forEach((b) => b.classList.remove("is-active"));
  el.navSidebar.querySelectorAll(".nav-module-head--leaf.is-active").forEach((b) =>
    b.classList.remove("is-active")
  );
}

function highlightNavFile(ul, filename) {
  if (!ul || !filename) return;
  clearNavActive();
  ul.querySelectorAll("button").forEach((b) => {
    if (b.dataset.filename === filename) b.classList.add("is-active");
  });
}

function updateNavAddVisibility() {
  document.querySelectorAll(".nav-module").forEach((mod) => {
    const head = mod.querySelector(".nav-module-head[data-toggle]");
    const addBtn = mod.querySelector(".nav-add-btn");
    if (!addBtn) return;
    const expanded = head && head.getAttribute("aria-expanded") === "true";
    addBtn.hidden = !expanded;
  });
}

/** 展开左侧某模块列表（主编「查看章节/大纲」跳转预览时用） */
function expandNavSublist(toggleKey) {
  const head = el.navSidebar.querySelector(`.nav-module-head[data-toggle="${toggleKey}"]`);
  const ul =
    toggleKey === "chapters"
      ? el.navChapters
      : toggleKey === "outlines"
        ? el.navOutlines
        : toggleKey === "main-plots"
          ? el.navMainPlots
          : null;
  if (!head || !ul) return;
  if (ul.hidden) {
    ul.hidden = false;
    head.setAttribute("aria-expanded", "true");
    updateNavAddVisibility();
  }
}

/** 主编识别「查看章节正文 / 查看剧情大纲」后由 SSE 触发 */
async function applyPreviewNavigate(payload) {
  if (!payload || !sessionId) return;
  const tab = payload.tab;
  const filename = payload.filename;
  if (tab === "chapters" && filename) {
    expandNavSublist("chapters");
    await refreshBrowseNav();
    await openChapterFile(filename);
  } else if (tab === "outlines" && filename) {
    expandNavSublist("outlines");
    await refreshBrowseNav();
    await openOutlineFile(filename);
  }
}

function setPreviewTab(tab) {
  previewState.tab = tab;
  el.previewTabs.querySelectorAll("[data-preview-tab]").forEach((btn) => {
    const sel = btn.getAttribute("data-preview-tab") === tab;
    btn.setAttribute("aria-selected", sel ? "true" : "false");
    btn.classList.toggle("is-active", sel);
  });
  const isChat = tab === PREVIEW_TAB.CHAT;
  el.previewPanelFile.hidden = isChat;
  el.previewPanelChat.hidden = !isChat;
}

function updateDeleteButtonVisibility() {
  const k = previewState.kind;
  let n = 0;
  if (k === "chapter") n = previewState.counts.chapters;
  else if (k === "outline") n = previewState.counts.outlines;
  else if (k === "main") n = previewState.counts.main;
  el.btnPreviewDelete.hidden = !(k && k !== "intro" && n > 1 && previewState.filename);
}

function showFilenameRow(show) {
  el.browseFilenameRow.hidden = !show;
}

function resetPreviewEditor(placeholder) {
  previewState.kind = null;
  previewState.filename = null;
  el.browseEditor.value = placeholder || "";
  previewState.lastSavedContent = el.browseEditor.value;
  showFilenameRow(false);
  el.btnPreviewDelete.hidden = true;
}

function scheduleSave() {
  clearTimeout(previewState.saveTimer);
  previewState.saveTimer = setTimeout(() => {
    flushSave().catch(() => {});
  }, 750);
}

async function flushSave() {
  if (!sessionId || !previewState.kind) return;
  const content = el.browseEditor.value;
  if (content === previewState.lastSavedContent) return;
  try {
    if (previewState.kind === "intro") {
      await api(`/api/session/${encodeURIComponent(sessionId)}/browse/intro`, {
        method: "PUT",
        body: JSON.stringify({ content }),
      });
    } else {
      const path =
        previewState.kind === "chapter"
          ? `/api/session/${encodeURIComponent(sessionId)}/browse/chapters/content`
          : previewState.kind === "outline"
            ? `/api/session/${encodeURIComponent(sessionId)}/browse/outlines/content`
            : `/api/session/${encodeURIComponent(sessionId)}/browse/main-plots/content`;
      await api(path, {
        method: "PUT",
        body: JSON.stringify({ filename: previewState.filename, content }),
      });
    }
    previewState.lastSavedContent = content;
    setStatus("已保存");
    setTimeout(() => setStatus(""), 2000);
  } catch (e) {
    setStatus(`保存失败：${e.message}`);
  }
}

async function openChapterFile(filename) {
  if (!sessionId) return;
  setPreviewTab(PREVIEW_TAB.CHAPTERS);
  setStatus("加载中…");
  try {
    const data = await api(
      `/api/session/${encodeURIComponent(sessionId)}/browse/chapters/content?filename=${encFile(filename)}`
    );
    previewState.kind = "chapter";
    previewState.filename = data.filename;
    el.browseEditor.value = data.content ?? "";
    previewState.lastSavedContent = el.browseEditor.value;
    el.browseTitle.textContent = data.title || stemFilename(filename);
    el.browseMeta.textContent = `作品文件 · chapter/${data.filename}`;
    showFilenameRow(true);
    el.browseFilenameInput.value = stemFilename(data.filename);
    updateDeleteButtonVisibility();
    highlightNavFile(el.navChapters, data.filename);
  } catch (e) {
    resetPreviewEditor("");
    el.browseTitle.textContent = "章节";
    el.browseMeta.textContent = e.message;
  } finally {
    setStatus("");
  }
}

async function openOutlineFile(filename) {
  if (!sessionId) return;
  setPreviewTab(PREVIEW_TAB.OUTLINES);
  setStatus("加载中…");
  try {
    const data = await api(
      `/api/session/${encodeURIComponent(sessionId)}/browse/outlines/content?filename=${encFile(filename)}`
    );
    previewState.kind = "outline";
    previewState.filename = data.filename;
    el.browseEditor.value = data.content ?? "";
    previewState.lastSavedContent = el.browseEditor.value;
    el.browseTitle.textContent = data.title || stemFilename(filename);
    el.browseMeta.textContent = `作品文件 · plot/${data.filename}`;
    showFilenameRow(true);
    el.browseFilenameInput.value = stemFilename(data.filename);
    updateDeleteButtonVisibility();
    highlightNavFile(el.navOutlines, data.filename);
  } catch (e) {
    resetPreviewEditor("");
    el.browseTitle.textContent = "剧情大纲";
    el.browseMeta.textContent = e.message;
  } finally {
    setStatus("");
  }
}

async function openMainPlotFile(filename) {
  if (!sessionId) return;
  setPreviewTab(PREVIEW_TAB.MAIN);
  setStatus("加载中…");
  try {
    const data = await api(
      `/api/session/${encodeURIComponent(sessionId)}/browse/main-plots/content?filename=${encFile(filename)}`
    );
    previewState.kind = "main";
    previewState.filename = data.filename;
    el.browseEditor.value = data.content ?? "";
    previewState.lastSavedContent = el.browseEditor.value;
    el.browseTitle.textContent = data.title || stemFilename(filename);
    el.browseMeta.textContent = `作品文件 · main_plot/${data.filename}`;
    showFilenameRow(true);
    el.browseFilenameInput.value = stemFilename(data.filename);
    updateDeleteButtonVisibility();
  } catch (e) {
    resetPreviewEditor("");
    el.browseTitle.textContent = "当前主线";
    el.browseMeta.textContent = e.message;
  } finally {
    setStatus("");
  }
}

async function openIntroPreview() {
  if (!sessionId) return;
  clearNavActive();
  document.querySelector('.nav-module-head[data-page="intro"]')?.classList.add("is-active");
  setPreviewTab(PREVIEW_TAB.INTRO);
  setStatus("加载中…");
  try {
    const data = await api(`/api/session/${encodeURIComponent(sessionId)}/browse/intro`);
    previewState.kind = "intro";
    previewState.filename = null;
    el.browseEditor.value = data.content ?? "";
    previewState.lastSavedContent = el.browseEditor.value;
    el.browseTitle.textContent = data.title || "小说简介";
    el.browseMeta.textContent = "作品根目录 · 小说简介.txt";
    showFilenameRow(false);
    el.btnPreviewDelete.hidden = true;
  } catch (e) {
    resetPreviewEditor("");
    el.browseMeta.textContent = e.message;
  } finally {
    setStatus("");
  }
}

function tabHint(tab) {
  const hints = {
    [PREVIEW_TAB.CHAPTERS]: "请从左侧展开「章节」并选择文件，或使用上方 Tab 切换。",
    [PREVIEW_TAB.OUTLINES]: "请从左侧展开「剧情大纲」并选择文件。",
    [PREVIEW_TAB.MAIN]: "请从左侧展开「当前主线」并选择文件。",
    [PREVIEW_TAB.INTRO]: "正在加载或请从左侧点击「小说简介」。",
  };
  return hints[tab] || "";
}

function onPreviewTabClick(tab) {
  if (tab === PREVIEW_TAB.CHAT) {
    setPreviewTab(PREVIEW_TAB.CHAT);
    return;
  }
  setPreviewTab(tab);
  const sameKind =
    (tab === PREVIEW_TAB.CHAPTERS && previewState.kind === "chapter") ||
    (tab === PREVIEW_TAB.OUTLINES && previewState.kind === "outline") ||
    (tab === PREVIEW_TAB.MAIN && previewState.kind === "main") ||
    (tab === PREVIEW_TAB.INTRO && previewState.kind === "intro");
  if (!sameKind) {
    if (tab === PREVIEW_TAB.INTRO) {
      openIntroPreview();
      return;
    }
    resetPreviewEditor(tabHint(tab));
    el.browseTitle.textContent =
      tab === PREVIEW_TAB.CHAPTERS ? "章节" : tab === PREVIEW_TAB.OUTLINES ? "剧情大纲" : "当前主线";
    el.browseMeta.textContent = "";
  }
}

async function handleAddFile(kind) {
  if (!sessionId) return;
  try {
    let path;
    if (kind === "chapters") path = `/api/session/${encodeURIComponent(sessionId)}/browse/chapters/create`;
    else if (kind === "outlines") path = `/api/session/${encodeURIComponent(sessionId)}/browse/outlines/create`;
    else if (kind === "main-plots") path = `/api/session/${encodeURIComponent(sessionId)}/browse/main-plots/create`;
    else return;
    const data = await api(path, { method: "POST", body: JSON.stringify({}) });
    await refreshBrowseNav();
    const fn = data.filename;
    if (kind === "chapters") await openChapterFile(fn);
    else if (kind === "outlines") await openOutlineFile(fn);
    else await openMainPlotFile(fn);
    const ul =
      kind === "chapters"
        ? el.navChapters
        : kind === "outlines"
          ? el.navOutlines
          : el.navMainPlots;
    ul.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.filename === fn);
    });
  } catch (e) {
    appendMessage("系统", `新建文件失败：${e.message}`);
  }
}

async function tryRenameFromInput() {
  if (!previewState.filename || previewState.kind === "intro") return;
  const stem = el.browseFilenameInput.value.trim();
  if (!stem) return;
  const newName = stem.toLowerCase().endsWith(".txt") ? stem : `${stem}.txt`;
  if (newName === previewState.filename) return;
  const old = previewState.filename;
  let renamePath;
  if (previewState.kind === "chapter") renamePath = `/api/session/${encodeURIComponent(sessionId)}/browse/chapters/rename`;
  else if (previewState.kind === "outline")
    renamePath = `/api/session/${encodeURIComponent(sessionId)}/browse/outlines/rename`;
  else if (previewState.kind === "main")
    renamePath = `/api/session/${encodeURIComponent(sessionId)}/browse/main-plots/rename`;
  else return;
  try {
    const { filename } = await api(renamePath, {
      method: "POST",
      body: JSON.stringify({ old_name: old, new_name: newName }),
    });
    previewState.filename = filename;
    el.browseMeta.textContent =
      previewState.kind === "chapter"
        ? `作品文件 · chapter/${filename}`
        : previewState.kind === "outline"
          ? `作品文件 · plot/${filename}`
          : `作品文件 · main_plot/${filename}`;
    await refreshBrowseNav();
    const ul =
      previewState.kind === "chapter"
        ? el.navChapters
        : previewState.kind === "outline"
          ? el.navOutlines
          : el.navMainPlots;
    ul.querySelectorAll("button").forEach((b) => {
      b.classList.toggle("is-active", b.dataset.filename === filename);
    });
  } catch (e) {
    appendMessage("系统", `重命名失败：${e.message}`);
    el.browseFilenameInput.value = stemFilename(previewState.filename);
  }
}

async function executeDelete() {
  if (!previewState.filename || previewState.kind === "intro") return;
  let delPath;
  if (previewState.kind === "chapter")
    delPath = `/api/session/${encodeURIComponent(sessionId)}/browse/chapters/file?filename=${encFile(previewState.filename)}`;
  else if (previewState.kind === "outline")
    delPath = `/api/session/${encodeURIComponent(sessionId)}/browse/outlines/file?filename=${encFile(previewState.filename)}`;
  else if (previewState.kind === "main")
    delPath = `/api/session/${encodeURIComponent(sessionId)}/browse/main-plots/file?filename=${encFile(previewState.filename)}`;
  else return;
  await api(delPath, { method: "DELETE" });
  await refreshBrowseNav();
  resetPreviewEditor("文件已删除。请在左侧选择其他文件。");
  el.browseTitle.textContent = "内容浏览";
  el.browseMeta.textContent = "";
}

function detailToMessage(detail) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    if (typeof detail.message === "string") return detail.message;
  }
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join("; ");
  return "";
}

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  const text = await res.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(text || res.statusText, res.status, undefined);
  }
  if (!res.ok) {
    const detail = json?.detail;
    const msg = detailToMessage(detail) || `HTTP ${res.status}`;
    const code =
      detail && typeof detail === "object" && !Array.isArray(detail) ? detail.code : undefined;
    throw new ApiError(msg, res.status, code);
  }
  return json;
}

async function streamSsePost(path, body, onEvent, signal) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {
      /* ignore */
    }
    const detail = json?.detail;
    const msg = detailToMessage(detail) || text || res.statusText;
    const code =
      detail && typeof detail === "object" && !Array.isArray(detail) ? detail.code : undefined;
    throw new ApiError(msg, res.status, code);
  }

  if (!res.body?.getReader) {
    throw new ApiError("浏览器不支持流式读取", res.status, undefined);
  }

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(line.slice(6)));
          } catch {
            /* ignore */
          }
        }
      }
    }
  }
}

function applyChatDone(data) {
  updateSessionBar({
    novel_name: data.novel_name,
    active_agent: data.active_agent,
  });

  if (data.needs_novel_picker) {
    setNavEnabled(false);
    clearStagingCreative();
    loadNovelList().then(() => showModal(true));
  }

  if (data.session_ended) {
    sessionStorage.removeItem(STORAGE_KEY);
    sessionId = "";
    el.sessionMeta.hidden = true;
    setNavEnabled(false);
    clearStagingCreative();
    appendMessage("系统", "会话已结束。请重新选择作品以继续。");
    loadNovelList().then(() => showModal(true));
  } else {
    refreshBrowseNav();
  }
}

async function refreshBrowseNav() {
  if (!sessionId) return;
  try {
    const [ch, ol, mp] = await Promise.all([
      api(`/api/session/${encodeURIComponent(sessionId)}/browse/chapters`),
      api(`/api/session/${encodeURIComponent(sessionId)}/browse/outlines`),
      api(`/api/session/${encodeURIComponent(sessionId)}/browse/main-plots`),
    ]);
    previewState.counts = {
      chapters: (ch.items || []).length,
      outlines: (ol.items || []).length,
      main: (mp.items || []).length,
    };
    updateDeleteButtonVisibility();

    el.navChapters.innerHTML = "";
    for (const it of ch.items || []) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = it.label;
      btn.dataset.filename = it.name;
      btn.addEventListener("click", () => {
        clearNavActive();
        btn.classList.add("is-active");
        openChapterFile(it.name);
      });
      li.appendChild(btn);
      el.navChapters.appendChild(li);
    }

    el.navOutlines.innerHTML = "";
    for (const it of ol.items || []) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = it.label;
      btn.dataset.filename = it.name;
      btn.addEventListener("click", () => {
        clearNavActive();
        btn.classList.add("is-active");
        openOutlineFile(it.name);
      });
      li.appendChild(btn);
      el.navOutlines.appendChild(li);
    }

    el.navMainPlots.innerHTML = "";
    for (const it of mp.items || []) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = it.label;
      btn.dataset.filename = it.name;
      btn.addEventListener("click", () => {
        clearNavActive();
        btn.classList.add("is-active");
        openMainPlotFile(it.name);
      });
      li.appendChild(btn);
      el.navMainPlots.appendChild(li);
    }
  } catch (e) {
    el.browseTitle.textContent = "目录加载失败";
    el.browseMeta.textContent = e.message;
  }
}

function wireNavSidebar() {
  el.navSidebar.querySelectorAll(".nav-module-head[data-toggle]").forEach((head) => {
    head.addEventListener("click", () => {
      const key = head.getAttribute("data-toggle");
      const ul =
        key === "chapters"
          ? el.navChapters
          : key === "outlines"
            ? el.navOutlines
            : el.navMainPlots;
      const open = ul.hidden;
      ul.hidden = !open;
      head.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        refreshBrowseNav();
      }
      updateNavAddVisibility();
    });
  });

  el.navSidebar.querySelectorAll(".nav-add-btn[data-add]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      handleAddFile(btn.getAttribute("data-add"));
    });
  });

  el.navSidebar.querySelectorAll(".nav-module-head[data-page]").forEach((head) => {
    head.addEventListener("click", () => {
      clearNavActive();
      head.classList.add("is-active");
      openIntroPreview();
    });
  });

  el.btnRefreshNav.addEventListener("click", () => {
    refreshBrowseNav();
  });
}

function wirePreviewChrome() {
  el.previewTabs.querySelectorAll("[data-preview-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      onPreviewTabClick(btn.getAttribute("data-preview-tab"));
    });
  });

  el.browseEditor.addEventListener("input", () => {
    scheduleSave();
  });

  el.browseFilenameInput.addEventListener("blur", () => {
    tryRenameFromInput();
  });

  el.browseFilenameInput.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      el.browseFilenameInput.blur();
    }
  });

  el.btnPreviewDelete.addEventListener("click", () => {
    if (!previewState.filename || previewState.kind === "intro") return;
    el.deleteConfirmMessage.textContent = `确定永久删除「${previewState.filename}」吗？此操作不可恢复。`;
    pendingDeleteAction = executeDelete;
    showDeleteModal(true);
  });

  el.btnDeleteCancel.addEventListener("click", () => showDeleteModal(false));

  el.btnDeleteConfirm.addEventListener("click", async () => {
    const fn = pendingDeleteAction;
    showDeleteModal(false);
    if (typeof fn === "function") {
      try {
        await fn();
      } catch (e) {
        appendMessage("系统", `删除失败：${e.message}`);
      }
    }
  });

  el.stagingSave.addEventListener("click", async () => {
    if (!stagingState || !sessionId) return;
    const content = el.stagingEditor.value;
    const fn = stagingState.filename;
    try {
      if (stagingState.kind === "outline") {
        await api(`/api/session/${encodeURIComponent(sessionId)}/browse/outlines/content`, {
          method: "PUT",
          body: JSON.stringify({ filename: fn, content }),
        });
      } else {
        await api(`/api/session/${encodeURIComponent(sessionId)}/browse/chapters/content`, {
          method: "PUT",
          body: JSON.stringify({ filename: fn, content }),
        });
      }
      appendMessage("系统", `已从预览保存到：${fn}`);
      clearStagingCreative();
      await refreshBrowseNav();
    } catch (e) {
      appendMessage("系统", `保存失败：${e.message}`);
    }
  });

  el.stagingDiscard.addEventListener("click", () => {
    clearStagingCreative();
  });
}

async function loadNovelList() {
  const data = await api("/api/novels");
  el.novelSelect.innerHTML = '<option value="">— 请选择 —</option>';
  for (const name of data.novels || []) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    el.novelSelect.appendChild(opt);
  }
}

async function startSession(mode, name) {
  const data = await api("/api/session", {
    method: "POST",
    body: JSON.stringify({ mode, name }),
  });
  sessionId = data.session_id;
  sessionStorage.setItem(STORAGE_KEY, sessionId);
  el.messages.innerHTML = "";
  clearStagingCreative();
  appendMessage("系统", `已连接作品「${data.novel_name}」。${data.welcome_hint || ""}`);
  if (data.has_memory) {
    appendMessage("系统", "已载入本书近期对话记录。");
  }
  updateSessionBar({ novel_name: data.novel_name, active_agent: "navigator" });
  setNavEnabled(true);
  setPreviewTab(PREVIEW_TAB.CHAPTERS);
  resetPreviewEditor("已连接作品。请从左侧选择章节、大纲或主线。");
  el.browseTitle.textContent = "内容浏览";
  el.browseMeta.textContent = "";
  await refreshBrowseNav();
  showModal(false);
  el.input.focus();
}

async function switchSessionNovel(mode, name) {
  const data = await api(`/api/session/${encodeURIComponent(sessionId)}/novel`, {
    method: "POST",
    body: JSON.stringify({ mode, name }),
  });
  clearStagingCreative();
  appendMessage("系统", `已切换到作品「${data.novel_name}」。`);
  updateSessionBar({ novel_name: data.novel_name, active_agent: "navigator" });
  clearNavActive();
  setPreviewTab(PREVIEW_TAB.CHAPTERS);
  resetPreviewEditor("已切换作品。请从左侧打开文件。");
  el.browseTitle.textContent = "内容浏览";
  el.browseMeta.textContent = "";
  await refreshBrowseNav();
  showModal(false);
  el.input.focus();
}

async function sendMessage(text) {
  if (!sessionId) {
    showModal(true);
    return;
  }
  el.btnSend.disabled = true;
  setStatus("连接中…");
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
  let doneMeta = null;
  let streamError = false;
  try {
    await streamSsePost(
      `/api/session/${encodeURIComponent(sessionId)}/message/stream`,
      { message: text },
      (ev) => {
        if (ev.type === "progress") setStatus(ev.text || "");
        if (ev.type === "message") appendMessage(ev.role, ev.content);
        if (ev.type === "creative_preview") applyCreativePreviewFromStream(ev);
        if (ev.type === "preview_navigate") void applyPreviewNavigate(ev);
        if (ev.type === "done") doneMeta = ev;
        if (ev.type === "error") {
          streamError = true;
          const hint =
            ev.code === "timeout"
              ? ev.message
              : ev.code === "auth"
                ? `${ev.message}`
                : ev.code === "rate_limit"
                  ? `${ev.message}`
                  : `${ev.message}`;
          appendMessage("系统", hint);
        }
      },
      controller.signal
    );
    if (doneMeta) applyChatDone(doneMeta);
  } catch (e) {
    if (e.name === "AbortError") {
      appendMessage(
        "系统",
        "请求超时（已等待较长时间）。若模型仍在生成可稍后查看服务端日志；也可缩短单次任务或检查网络后重试。"
      );
    } else if (e instanceof ApiError && e.code === "session_expired") {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionId = "";
      el.sessionMeta.hidden = true;
      setNavEnabled(false);
      clearStagingCreative();
      appendMessage("系统", `${e.message} 将打开作品选择。`);
      await loadNovelList();
      showModal(true);
    } else if (!streamError) {
      appendMessage("系统", `请求失败：${e.message}`);
    }
  } finally {
    clearTimeout(timeoutId);
    el.btnSend.disabled = false;
    setStatus("");
  }
}

el.composer.addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = el.input.value.trim();
  if (!text) return;
  appendMessage("用户", text);
  el.input.value = "";
  sendMessage(text);
});

el.btnOutlineWizard.addEventListener("click", async () => {
  if (!sessionId) {
    showModal(true);
    return;
  }
  const chapter = parseInt(el.wizardChapter.value, 10);
  const prevChapters = parseInt(el.wizardPrev.value, 10);
  const requirements = el.wizardReq.value.trim();
  if (Number.isNaN(chapter) || chapter < 1) {
    appendMessage("系统", "请输入有效的章节号（≥1）。");
    return;
  }
  if (Number.isNaN(prevChapters) || prevChapters < 1) {
    appendMessage("系统", "请输入有效的「读取前文章数」（1～80）。");
    return;
  }
  const summary =
    `[大纲向导] 第${chapter}章；前文 ${prevChapters} 章` +
    (requirements ? `；要求：${requirements}` : "；无特别要求");
  appendMessage("用户", summary);

  el.btnOutlineWizard.disabled = true;
  el.btnSend.disabled = true;
  setStatus("连接中…");
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CHAT_TIMEOUT_MS);
  let doneMeta = null;
  let streamError = false;
  try {
    await streamSsePost(
      `/api/session/${encodeURIComponent(sessionId)}/message/outline-wizard/stream`,
      { chapter, requirements, prev_chapters: prevChapters },
      (ev) => {
        if (ev.type === "progress") setStatus(ev.text || "");
        if (ev.type === "message") appendMessage(ev.role, ev.content);
        if (ev.type === "creative_preview") applyCreativePreviewFromStream(ev);
        if (ev.type === "done") doneMeta = ev;
        if (ev.type === "error") {
          streamError = true;
          appendMessage("系统", ev.message || "处理失败");
        }
      },
      controller.signal
    );
    if (doneMeta) applyChatDone(doneMeta);
  } catch (e) {
    if (e.name === "AbortError") {
      appendMessage("系统", "请求超时（已等待较长时间）。请检查网络与模型服务后重试。");
    } else if (e instanceof ApiError && e.code === "session_expired") {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionId = "";
      el.sessionMeta.hidden = true;
      setNavEnabled(false);
      clearStagingCreative();
      appendMessage("系统", `${e.message} 将打开作品选择。`);
      await loadNovelList();
      showModal(true);
    } else if (!streamError) {
      appendMessage("系统", `请求失败：${e.message}`);
    }
  } finally {
    clearTimeout(timeoutId);
    el.btnOutlineWizard.disabled = false;
    el.btnSend.disabled = false;
    setStatus("");
  }
});

el.input.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    el.composer.requestSubmit();
  }
});

el.btnSwitchNovel.addEventListener("click", async () => {
  await loadNovelList();
  showModal(true);
});

el.btnOpenExisting.addEventListener("click", async () => {
  const name = el.novelSelect.value.trim();
  if (!name) {
    showModalError("请选择一个已有作品。");
    return;
  }
  try {
    if (sessionId) {
      await switchSessionNovel("existing", name);
    } else {
      await startSession("existing", name);
    }
  } catch (e) {
    showModalError(e.message);
  }
});

el.btnCreateNew.addEventListener("click", async () => {
  const name = el.novelNewName.value.trim();
  if (!name) {
    showModalError("请填写新作品名称。");
    return;
  }
  try {
    if (sessionId) {
      await switchSessionNovel("new", name);
    } else {
      await startSession("new", name);
    }
    el.novelNewName.value = "";
  } catch (e) {
    showModalError(e.message);
  }
});

async function boot() {
  wireNavSidebar();
  wirePreviewChrome();
  setPreviewTab(PREVIEW_TAB.CHAPTERS);

  try {
    await api("/api/health");
  } catch {
    setStatus("无法连接后端，请确认已启动 uvicorn（见 main_web.py 顶部说明）。");
    showModal(false);
    setNavEnabled(false);
    return;
  }

  await loadNovelList();

  if (sessionId) {
    try {
      const info = await api(`/api/session/${encodeURIComponent(sessionId)}`);
      el.sessionMeta.hidden = false;
      updateSessionBar(info);
      setNavEnabled(true);
      await refreshBrowseNav();
      appendMessage("系统", "欢迎回来，继续输入即可。");
    } catch (e) {
      sessionStorage.removeItem(STORAGE_KEY);
      sessionId = "";
      setNavEnabled(false);
      showModal(true);
      if (e instanceof ApiError && e.code === "session_expired") {
        appendMessage("系统", "会话已失效，请重新选择作品。");
      }
    }
  } else {
    setNavEnabled(false);
    showModal(true);
  }
}

boot();
