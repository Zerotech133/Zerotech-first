/* 军团 IDE · Hermes 扩展 panel（方式 A 原型） */
(function () {
  if (window.__legionIdeExtensionLoaded) return;
  window.__legionIdeExtensionLoaded = true;

  const OH_BASE = window.LEGION_OH_BASE || "http://127.0.0.1:8796";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  async function api(path, opts) {
    const r = await fetch(OH_BASE + path, Object.assign({
      headers: { "Content-Type": "application/json" },
    }, opts || {}));
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) {
      throw new Error(d.detail || d.error || ("HTTP " + r.status));
    }
    return d;
  }

  function buildLauncher() {
    if ($("#legion-ide-launch")) return;
    const btn = el("button", "legion-ide-launch", "⚔ 军团 IDE");
    btn.id = "legion-ide-launch";
    btn.title = "军团 IDE · OpenHands 工单执行";
    document.body.appendChild(btn);
    btn.addEventListener("click", openPanel);
    addNavButtons();
  }

  let state = {
    cid: "",
    timer: null,
    task: "",
    baseSteps: [],
    events: [],
    pending: false,
    pollBusy: false,
  };

  function panelHtml() {
    return `
      <div class="legion-ide-overlay" id="legion-ide-overlay">
        <section class="legion-ide-panel" role="dialog" aria-label="军团 IDE">
          <header class="legion-ide-head">
            <div>
              <b>⚔ 军团 IDE</b>
              <span class="legion-ide-sub">OpenHands · Docker sandbox</span>
            </div>
            <button class="legion-ide-close" id="legion-ide-close" aria-label="关闭">×</button>
          </header>
          <div class="legion-ide-body">
            <div class="legion-ide-agents-head">
              <span>Agent</span>
              <span id="legion-ide-agent-count" class="legion-ide-status"></span>
            </div>
            <div id="legion-ide-agents" class="legion-ide-agents">加载中…</div>
            <details id="legion-ide-agent-detail" class="legion-ide-agent-detail" open>
              <summary>Agent 详情</summary>
              <div id="legion-ide-agent-detail-body">选择 Agent</div>
            </details>
            <div class="legion-ide-session-head">
              <span>历史会话</span>
              <button id="legion-ide-new-session">新会话</button>
            </div>
            <div id="legion-ide-sessions" class="legion-ide-sessions">—</div>
            <div class="legion-ide-row">
              <label>工单</label>
              <select id="legion-ide-task"><option value="">（加载中…）</option></select>
            </div>
            <div class="legion-ide-row">
              <label>模型</label>
              <select id="legion-ide-model">
                <option value="deepseek-flash">deepseek-flash</option>
                <option value="glm/glm-5.3">glm/glm-5.3</option>
              </select>
            </div>
            <textarea id="legion-ide-message" placeholder="描述要改的内容，例如：修复 xxx 并跑测试"></textarea>
            <div class="legion-ide-actions">
              <button id="legion-ide-context">取上下文</button>
              <span id="legion-ide-context-status" class="legion-ide-status"></span>
            </div>
            <pre id="legion-ide-context-preview" style="display:none"></pre>
            <div class="legion-ide-actions">
              <button id="legion-ide-old-pick">旧 IDE 选中</button>
              <span id="legion-ide-snippet-status" class="legion-ide-status"></span>
            </div>
            <details id="legion-ide-snippet" class="legion-ide-snippet">
              <summary>引用代码片段</summary>
              <div class="legion-ide-row">
                <input id="legion-ide-snippet-path" placeholder="文件路径，例如 agent_platform/foo.py">
                <button id="legion-ide-snippet-load">读取</button>
                <button id="legion-ide-snippet-browse">浏览</button>
              </div>
              <div id="legion-ide-snippet-cwd" class="legion-ide-snippet-cwd">.</div>
              <div id="legion-ide-snippet-files" class="legion-ide-snippet-files"></div>
              <textarea id="legion-ide-snippet-text" readonly placeholder="读取文件后，用鼠标选中要引用的代码"></textarea>
              <div class="legion-ide-actions">
                <button id="legion-ide-snippet-insert">插入选中</button>
                <span id="legion-ide-snippet-info" class="legion-ide-status"></span>
              </div>
            </details>
            <div class="legion-ide-actions">
              <button id="legion-ide-start" class="primary">开始执行</button>
              <button id="legion-ide-stop" disabled>停止</button>
              <span id="legion-ide-status" class="legion-ide-status">就绪</span>
            </div>
            <div class="legion-ide-section">
              <div class="legion-ide-label">过程</div>
              <div id="legion-ide-events" class="legion-ide-events">—</div>
            </div>
            <div class="legion-ide-section" id="legion-ide-confirm-wrap" style="display:none">
              <div class="legion-ide-label" id="legion-ide-pending">等待确认</div>
              <button id="legion-ide-approve" class="primary">批准执行</button>
              <button id="legion-ide-reject">拒绝</button>
            </div>
            <div class="legion-ide-section" id="legion-ide-reply-wrap" style="display:none">
              <div class="legion-ide-label">最终答复</div>
              <pre id="legion-ide-reply"></pre>
            </div>
            <div class="legion-ide-section" id="legion-ide-diff-wrap" style="display:none">
              <div class="legion-ide-label">工单变更</div>
              <pre id="legion-ide-diff"></pre>
              <button id="legion-ide-commit" class="primary">确认提交到工单</button>
              <span id="legion-ide-commit-msg" class="legion-ide-status"></span>
            </div>
          </div>
        </section>
      </div>`;
  }

  const MAIN_CLASSES = ['settings', 'skills', 'memory', 'tasks', 'workspaces',
    'profiles', 'insights', 'legionide'];

  function ensureNativePanel() {
    const main = document.querySelector('main.main');
    if (!main) return false;
    if ($("#mainLegionIde")) return true;
    const host = document.createElement('section');
    host.id = 'mainLegionIde';
    host.className = 'main-view legion-ide-main';
    const wrap = el('div', 'legion-ide-native-host');
    const tmp = document.createElement('div');
    tmp.innerHTML = panelHtml();
    const panel = tmp.querySelector('.legion-ide-panel');
    if (!panel) return false;
    wrap.appendChild(panel);
    host.appendChild(wrap);
    main.appendChild(host);
    bindPanel();
    loadTasks();
    loadAgents();
    return true;
  }

  function showNativePanel() {
    if (!ensureNativePanel()) return false;
    const main = document.querySelector('main.main');
    MAIN_CLASSES.forEach((c) => main.classList.remove('showing-' + c));
    main.classList.add('showing-legionide');
    document.querySelectorAll('[data-panel]').forEach((t) => {
      t.classList.toggle('active', t.dataset.panel === 'legionide');
    });
    return true;
  }

  function addNavButton(container, cls, label) {
    if (!container || container.querySelector('[data-panel="legionide"]')) return;
    const b = el('button', cls);
    b.dataset.panel = 'legionide';
    b.title = label;
    b.setAttribute('aria-label', label);
    b.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14.5 3.5 20.5 9.5 12 18l-6 2 2-6 6.5-6.5z"/><path d="M3 21h18"/></svg>';
    if (cls.indexOf('nav-tab') >= 0 && cls.indexOf('rail-btn') < 0) {
      b.dataset.label = label;
    }
    b.addEventListener('click', (e) => { e.preventDefault(); showNativePanel(); });
    const anchor = container.querySelector('.rail-spacer') ||
      container.querySelector('[data-panel="settings"]');
    if (anchor) container.insertBefore(b, anchor);
    else container.appendChild(b);
  }

  function addNavButtons() {
    addNavButton(document.querySelector('.rail'), 'rail-btn nav-tab', '军团 IDE');
    addNavButton(document.querySelector('.sidebar-nav'), 'nav-tab', '军团 IDE');
    // 点回 Hermes 内置导航时，退出军团 IDE 主视图
    document.addEventListener('click', (e) => {
      const t = e.target && e.target.closest ? e.target.closest('[data-panel]') : null;
      if (!t || t.dataset.panel === 'legionide') return;
      const main = document.querySelector('main.main');
      if (main) main.classList.remove('showing-legionide');
    }, true);
  }

  function openPanel() {
    if (showNativePanel()) return;
    if ($("#legion-ide-overlay")) return;
    const wrap = el("div");
    wrap.innerHTML = panelHtml();
    document.body.appendChild(wrap.firstElementChild);
    bindPanel();
    loadTasks();
    loadAgents();
  }

  function closePanel() {
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
    const main = document.querySelector('main.main');
    if (main && main.classList.contains('showing-legionide')) {
      main.classList.remove('showing-legionide');
      if (typeof switchPanel === 'function') switchPanel('chat');
    }
    const ov = $("#legion-ide-overlay");
    if (ov) ov.remove();
  }

  function status(txt, kind) {
    const n = $("#legion-ide-status");
    if (!n) return;
    n.textContent = txt;
    n.className = "legion-ide-status " + (kind || "");
  }

  function renderEvents() {
    const box = $("#legion-ide-events");
    if (!box) return;
    if (!state.events.length) { box.textContent = state.baseSteps.join("\\n") || "—"; return; }
    box.innerHTML = state.events.slice(-16).map((e) =>
      `<div class="legion-ide-ev"><span>${esc(e.kind || "event")}</span>${esc(e.summary || "")}</div>`
    ).join("");
  }

  function renderReply(text) {
    const wrap = $("#legion-ide-reply-wrap"), pre = $("#legion-ide-reply");
    if (!wrap || !pre) return;
    wrap.style.display = "";
    pre.textContent = text || "(空响应)";
  }

  async function loadTasks() {
    const sel = $("#legion-ide-task");
    if (!sel) return;
    try {
      const d = await api("/api/ide/openhands/tasks");
      const tasks = d.tasks || [];
      sel.innerHTML = '<option value="">（不绑定工单）</option>' + tasks.map((t) =>
        `<option value="${esc(t.task)}">${esc(t.task)} · ${esc(t.status)} · ${esc(t.title || "")}</option>`
      ).join("");
    } catch (e) {
      sel.innerHTML = '<option value="">工单列表不可用</option>';
      status("工单列表失败：" + e.message, "bad");
    }
  }

  let AGENTS = [];
let SELECTED_AGENT = "";
const SESSIONS_KEY = "legion-ide-sessions-v1";

function readSessions() {
  try {
    const v = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "[]");
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}

function writeSessions(list) {
  try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(list.slice(0, 20))); } catch (e) {}
}

function upsertSession(patch) {
  if (!patch || !patch.cid) return;
  const list = readSessions();
  const i = list.findIndex((s) => s.cid === patch.cid);
  const row = Object.assign({ updated: Date.now() }, i >= 0 ? list[i] : {}, patch);
  if (i >= 0) list[i] = row; else list.unshift(row);
  writeSessions(list);
  renderSessions();
}

function removeSession(cid) {
  writeSessions(readSessions().filter((s) => s.cid !== cid));
  if (state.cid === cid) newSession();
  renderSessions();
}

function renderSessions() {
  const box = $("#legion-ide-sessions");
  if (!box) return;
  const list = readSessions();
  if (!list.length) { box.textContent = "—"; return; }
  box.innerHTML = list.map((s) => {
    const title = s.title || s.task || s.cid.slice(0, 8);
    const st = s.status || "?";
    return `<button class="legion-ide-session" data-cid="${esc(s.cid)}" title="${esc(title)}">`
      + `<b>${esc(title)}</b><span>${esc(s.agent || "A0")} · ${esc(st)}</span>`
      + `<i data-del="${esc(s.cid)}" title="删除">×</i></button>`;
  }).join("");
  box.querySelectorAll(".legion-ide-session").forEach((n) => {
    n.addEventListener("click", (e) => {
      if (e.target && e.target.dataset && e.target.dataset.del) {
        e.stopPropagation();
        removeSession(e.target.dataset.del);
        return;
      }
      const s = readSessions().find((x) => x.cid === n.dataset.cid);
      if (s) loadSession(s);
    });
  });
}

function newSession() {
  stopStream();
  if (state.timer) { clearTimeout(state.timer); state.timer = null; }
  state.cid = ""; state.events = []; state.pending = false; state.task = "";
  const ev = $("#legion-ide-events"); if (ev) ev.textContent = "—";
  const rp = $("#legion-ide-reply-wrap"); if (rp) rp.style.display = "none";
  const df = $("#legion-ide-diff-wrap"); if (df) df.style.display = "none";
  const cf = $("#legion-ide-confirm-wrap"); if (cf) cf.style.display = "none";
  const st = $("#legion-ide-status"); if (st) { st.textContent = "就绪"; st.className = "legion-ide-status"; }
  const start = $("#legion-ide-start"); if (start) start.disabled = false;
  const stop = $("#legion-ide-stop"); if (stop) stop.disabled = true;
}

function loadSession(s) {
  if (!s || !s.cid) return;
  newSession();
  state.cid = s.cid;
  state.task = s.task || "";
  const taskSel = $("#legion-ide-task");
  if (taskSel && s.task) taskSel.value = s.task;
  if (s.agent) selectAgent(s.agent);
  if (s.model) {
    const ms = $("#legion-ide-model");
    if (ms) {
      if (![...ms.options].some((o) => o.value === s.model)) {
        const o = document.createElement("option");
        o.value = s.model; o.textContent = s.model;
        ms.appendChild(o);
      }
      ms.value = s.model;
    }
  }
  const msg = $("#legion-ide-message");
  if (msg && s.title) msg.value = s.title;
  status("恢复会话…", "run");
  const stop = $("#legion-ide-stop"); if (stop) stop.disabled = false;
  startStream(state.cid);
}

function renderAgentDetail(a) {
  const box = $("#legion-ide-agent-detail-body");
  if (!box) return;
  if (!a) { box.textContent = "选择 Agent"; return; }
  const rows = [];
  rows.push(`<div class="legion-ide-detail-name"><b>${esc(a.cn_name || a.name || a.id)}</b> <span>${esc(a.id)} · ${esc(a.grade || "")}</span></div>`);
  if (a.charter) rows.push(`<div class="legion-ide-detail-sec"><em>职责</em>${esc(a.charter)}</div>`);
  if (a.role_profile) rows.push(`<div class="legion-ide-detail-sec"><em>角色</em>${esc(a.role_profile)}</div>`);
  if (a.llm) rows.push(`<div class="legion-ide-detail-sec"><em>模型</em>${esc(a.llm)}</div>`);
  if (Array.isArray(a.tools) && a.tools.length) rows.push(`<div class="legion-ide-detail-sec"><em>工具</em>${a.tools.map(esc).join(" · ")}</div>`);
  if (Array.isArray(a.skills) && a.skills.length) rows.push(`<div class="legion-ide-detail-sec"><em>技能</em>${a.skills.map(esc).join(" · ")}</div>`);
  if (a.experience) rows.push(`<div class="legion-ide-detail-sec"><em>经验</em>${esc(a.experience)}</div>`);
  if (a.prompt) {
    rows.push(`<details class="legion-ide-prompt"><summary>prompt (${a.prompt.length} 字)</summary><pre>${esc(a.prompt)}</pre></details>`);
  }
  box.innerHTML = rows.join("");
}

function selectAgent(id) {
  const a = AGENTS.find((x) => x.id === id);
  if (!a) return;
  SELECTED_AGENT = a.id;
  localStorage.setItem("legion-ide-agent", a.id);
  const box = $("#legion-ide-agents");
  if (box) {
    box.querySelectorAll("[data-agent]").forEach((n) => {
      n.classList.toggle("on", n.dataset.agent === a.id);
    });
  }
  renderAgentDetail(a);
  const modelSel = $("#legion-ide-model");
  if (modelSel && a.llm) {
    const val = String(a.llm);
    if (![...modelSel.options].some((o) => o.value === val)) {
      const o = document.createElement("option");
      o.value = val; o.textContent = val;
      modelSel.appendChild(o);
    }
    modelSel.value = val;
  }
}

async function loadAgents() {
  const box = $("#legion-ide-agents");
  const cnt = $("#legion-ide-agent-count");
  if (!box) return;
  try {
    let d;
    try {
      const r = await fetch("/agent_ide_bundle.json", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      d = await r.json();
    } catch (e1) {
      // 8787 直连页拿不到 8734 的 bundle；走 8796 同源 API
      const ar = await api("/api/ide/agents");
      d = { agents: Object.fromEntries((ar.agents || []).map((a) => [a.id, a])) };
    }
    AGENTS = Array.isArray(d.agents)
      ? d.agents.filter((a) => a && a.id)
      : Object.values(d.agents || {}).filter((a) => a && a.id);
    AGENTS.sort((a, b) => String(a.id).localeCompare(String(b.id)));
    if (!AGENTS.length) throw new Error("empty agents");
    const models = [...new Set(AGENTS.map((a) => a.llm).filter(Boolean))];
    const modelSel = $("#legion-ide-model");
    if (modelSel && models.length) {
      modelSel.innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
    }
    box.innerHTML = AGENTS.map((a) => {
      const role = a.role_cn || a.role_title || a.role_profile || a.dept || "";
      return `<button class="legion-ide-agent" data-agent="${esc(a.id)}" title="${esc(role)}">`
        + `<b>${esc(a.cn_name || a.name || a.id)}</b>`
        + `<span>${esc(a.id)} · ${esc(a.llm || "")}</span></button>`;
    }).join("");
    box.querySelectorAll("[data-agent]").forEach((n) => {
      n.addEventListener("click", () => selectAgent(n.dataset.agent));
    });
    if (cnt) cnt.textContent = AGENTS.length + " agents";
    const saved = localStorage.getItem("legion-ide-agent");
    selectAgent(saved && AGENTS.some((a) => a.id === saved) ? saved : (AGENTS.find((a) => a.id === "A0") || AGENTS[0]).id);
  } catch (e) {
    box.textContent = "Agent 列表不可用：" + e.message;
    if (cnt) cnt.textContent = "offline";
  }
}

async function loadContext() {
  const q = ($("#legion-ide-message")?.value || "").trim();
  const st = $("#legion-ide-context-status");
  const pre = $("#legion-ide-context-preview");
  if (!q) { if (st) st.textContent = "先输入任务"; return; }
  if (st) st.textContent = "检索中…";
  try {
    const d = await api("/api/ide/context?q=" + encodeURIComponent(q) + "&budget=6000");
    if (pre) {
      pre.style.display = "";
      const syms = (d.symbols || []).map((s) => "- " + s.kind + " " + s.path + ":" + s.line + " " + s.name);
      pre.textContent = "context " + (d.chars || 0) + " chars · symbols " + ((d.symbols || []).length)
        + "\n" + syms.join("\n") + "\n\n" + (d.context || "");
    }
    if (st) st.textContent = "已取上下文";
  } catch (e) {
    if (st) st.textContent = "上下文失败：" + e.message;
  }
}

function appendSelection(text, label) {
  const t = String(text || "").replace(/\r\n/g, "\n").trim();
  if (!t) return;
  const msg = $("#legion-ide-message");
  if (!msg) return;
  const fence = t.includes("```") ? "````" : "```";
  msg.value = (msg.value ? msg.value.trimEnd() + "\n\n" : "")
    + "[" + label + "]\n" + fence + "\n" + t + "\n" + fence + "\n";
  msg.focus();
  const st = $("#legion-ide-snippet-status");
  if (st) st.textContent = "已插入 " + t.split("\n").length + " 行";
}

async function loadSnippet() {
  const pathEl = $("#legion-ide-snippet-path");
  const ta = $("#legion-ide-snippet-text");
  const info = $("#legion-ide-snippet-info");
  const path = (pathEl && pathEl.value || "").trim();
  if (!path) { if (info) info.textContent = "先填文件路径"; return; }
  if (info) info.textContent = "读取中…";
  try {
    const d = await api("/api/ide/fs/read?path=" + encodeURIComponent(path));
    const text = d.content != null ? d.content : (d.text != null ? d.text : "");
    if (ta) ta.value = String(text);
    if (info) info.textContent = "已读取 " + String(text).split("\n").length + " 行";
  } catch (e) {
    if (info) info.textContent = "读取失败：" + e.message;
  }
}

async function browseFiles(path) {
  const cwdEl = $("#legion-ide-snippet-cwd");
  const listEl = $("#legion-ide-snippet-files");
  const info = $("#legion-ide-snippet-info");
  if (!listEl) return;
  const p = String(path || ".").trim() || ".";
  if (info) info.textContent = "读取目录…";
  try {
    const d = await api("/api/ide/fs/list?path=" + encodeURIComponent(p));
    const items = (d.items || []).slice().sort((a, b) => {
      if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
      return String(a.name).localeCompare(String(b.name));
    });
    if (cwdEl) cwdEl.textContent = p;
    listEl.innerHTML = "";
    if (p !== ".") {
      const up = el("button", "legion-ide-file dir", "..");
      up.addEventListener("click", () => browseFiles(p.split("/").slice(0, -1).join("/") || "."));
      listEl.appendChild(up);
    }
    items.forEach((it) => {
      const b = el("button", "legion-ide-file " + (it.type === "dir" ? "dir" : "file"),
        (it.type === "dir" ? "📁 " : "📄 ") + it.name);
      b.title = it.path;
      b.addEventListener("click", () => {
        if (it.type === "dir") { browseFiles(it.path); return; }
        const pe = $("#legion-ide-snippet-path");
        if (pe) pe.value = it.path;
        loadSnippet();
      });
      listEl.appendChild(b);
    });
    if (info) info.textContent = items.length + " 项";
  } catch (e) {
    if (info) info.textContent = "目录失败：" + e.message;
  }
}

function insertSnippet() {
  const ta = $("#legion-ide-snippet-text");
  const info = $("#legion-ide-snippet-info");
  if (!ta) return;
  const s = ta.selectionStart, e = ta.selectionEnd;
  if (e <= s) { if (info) info.textContent = "先用鼠标选中代码"; return; }
  const text = ta.value.slice(s, e);
  const startLine = ta.value.slice(0, s).split("\n").length;
  const endLine = startLine + text.split("\n").length - 1;
  appendSelection(text, "代码片段 " + ($("#legion-ide-snippet-path").value || "") + ":" + startLine + "-" + endLine);
  if (info) info.textContent = "已插入选中代码";
}

function openOldPicker() {
  try {
    const w = window.open("http://127.0.0.1:8734/ide?standalone=1&legion_pick=1",
      "legion-ide-old-picker", "width=1280,height=900");
    if (!w) {
      const st = $("#legion-ide-snippet-status");
      if (st) st.textContent = "弹窗被拦截";
    }
  } catch (e) {}
}

window.addEventListener("message", (e) => {
  if (!/^http:\/\/127\.0\.0\.1:8734$/.test(String(e.origin || ""))) return;
  const d = e.data || {};
  if (d.type === "legion-ide-selection" && d.text) {
    appendSelection(d.text, "旧 IDE 选中");
  }
});

async function loadDiff(task) {
    if (!task) return;
    const wrap = $("#legion-ide-diff-wrap");
    const pre = $("#legion-ide-diff");
    if (!wrap || !pre) return;
    try {
      const d = await api("/api/ide/openhands/diff", {
        method: "POST", body: JSON.stringify({ task }),
      });
      if (!d.has_changes) return;
      wrap.style.display = "";
      pre.textContent = (d.files || []).map((f) => "• " + f).join("\\n") +
        "\\n\\n" + (d.diff || "（无 diff）").slice(0, 12000);
      $("#legion-ide-commit").dataset.task = task;
    } catch (e) {
      status("diff 失败：" + e.message, "bad");
    }
  }

  function schedulePoll() {
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(poll, 1200);
  }

  let OH_STREAM = null;

  function stopStream() {
    if (OH_STREAM) {
      try { OH_STREAM.close(); } catch (e) {}
      OH_STREAM = null;
    }
  }

  function applyStatus(d, usePoll) {
    state.events = d.events || [];
    renderEvents();
    upsertSession({ cid: state.cid, status: d.status || "running" });
    if (d.status === "waiting_for_confirmation") {
      state.pending = true;
      $("#legion-ide-confirm-wrap").style.display = "";
      $("#legion-ide-pending").textContent = "⚠ 等待确认：" + (d.pending_action || "");
      status("等待人工确认", "warn");
    } else {
      state.pending = false;
      $("#legion-ide-confirm-wrap").style.display = "none";
      status("运行中… " + state.events.length + " 事件", "run");
    }
    if (d.terminal) {
      const st = d.status;
      status(st === "finished" ? "✅ 完成" : ("⏹/❌ " + st), st === "finished" ? "ok" : "warn");
      if (d.reply) renderReply(d.reply);
      upsertSession({ cid: state.cid, status: st, reply: d.reply || "" });
      $("#legion-ide-stop").disabled = true;
      $("#legion-ide-start").disabled = false;
      if (st === "finished") loadDiff($("#legion-ide-task").value);
      state.cid = "";
      stopStream();
      return;
    }
    if (usePoll) schedulePoll();
  }

  async function poll() {
    if (!state.cid || state.pollBusy) return;
    state.pollBusy = true;
    try {
      const d = await api("/api/ide/openhands/status?id=" + encodeURIComponent(state.cid));
      applyStatus(d, true);
    } catch (e) {
      status("状态失败：" + e.message, "bad");
      schedulePoll();
    } finally {
      state.pollBusy = false;
    }
  }

  function startStream(cid) {
    stopStream();
    if (!cid || typeof EventSource === "undefined") { schedulePoll(); return; }
    try {
      const es = new EventSource(OH_BASE + "/api/ide/openhands/stream?id=" + encodeURIComponent(cid));
      OH_STREAM = es;
      es.addEventListener("status", (ev) => {
        try {
          const d = JSON.parse(ev.data);
          applyStatus(d, false);
          if (d.terminal || d.ok === false) stopStream();
        } catch (e) { /* ignore malformed frame */ }
      });
      es.onerror = () => {
        stopStream();
        if (state.cid) schedulePoll();
      };
    } catch (e) {
      schedulePoll();
    }
  }

  function ohModelName(m) {
    // 平台别名 glm/glm-5.3 → 网关实际模型名 glm-5.3；
    // 老 8796 进程不认带 provider 前缀的模型名。
    const s = String(m || "").trim();
    return s.includes("/") ? s.split("/").pop() : s;
  }

  async function start() {
    const task = $("#legion-ide-task").value;
    const message = $("#legion-ide-message").value.trim();
    if (!message) { status("先输入任务", "warn"); return; }
    const model = $("#legion-ide-model").value;
    $("#legion-ide-start").disabled = true;
    $("#legion-ide-stop").disabled = false;
    $("#legion-ide-reply-wrap").style.display = "none";
    $("#legion-ide-diff-wrap").style.display = "none";
    $("#legion-ide-confirm-wrap").style.display = "none";
    $("#legion-ide-events").textContent = "启动中…";
    try {
      const d = await api("/api/ide/openhands/start", {
        method: "POST",
        body: JSON.stringify({ message, task, model: ohModelName(model), agent: SELECTED_AGENT }),
      });
      state.cid = d.conversation_id;
      state.task = task;
      upsertSession({
        cid: state.cid, task, agent: SELECTED_AGENT, model,
        title: message.slice(0, 80), status: "running",
      });
      state.baseSteps = [
        "worktree：" + (d.worktree || "default"),
        "模型：" + (d.model || model),
      ];
      renderEvents();
      status("运行中…", "run");
      startStream(state.cid);
    } catch (e) {
      status("启动失败：" + e.message, "bad");
      $("#legion-ide-start").disabled = false;
      $("#legion-ide-stop").disabled = true;
    }
  }

  async function stop() {
    if (!state.cid) return;
    try {
      await api("/api/ide/openhands/interrupt", {
        method: "POST", body: JSON.stringify({ conversation_id: state.cid }),
      });
      status("停止中…", "warn");
      startStream(state.cid);
    } catch (e) { status("停止失败：" + e.message, "bad"); }
  }

  async function respond(accept) {
    if (!state.cid) return;
    try {
      await api("/api/ide/openhands/respond", {
        method: "POST",
        body: JSON.stringify({ conversation_id: state.cid, accept: !!accept }),
      });
      state.pending = false;
      $("#legion-ide-confirm-wrap").style.display = "none";
      status(accept ? "已批准，继续…" : "已拒绝", "run");
      startStream(state.cid);
    } catch (e) { status("审批失败：" + e.message, "bad"); }
  }

  async function commit() {
    const task = $("#legion-ide-commit").dataset.task;
    if (!task) return;
    try {
      $("#legion-ide-commit-msg").textContent = "提交中…";
      const d = await api("/api/ide/openhands/commit", {
        method: "POST",
        body: JSON.stringify({ task, message: "Hermes IDE · OpenHands 执行结果" }),
      });
      $("#legion-ide-commit-msg").textContent = "✅ 已提交 " + (d.commit || "");
    } catch (e) {
      $("#legion-ide-commit-msg").textContent = "❌ " + e.message;
    }
  }

  function bindPanel() {
    $("#legion-ide-close").onclick = closePanel;
    $("#legion-ide-start").onclick = start;
    $("#legion-ide-stop").onclick = stop;
    $("#legion-ide-approve").onclick = () => respond(true);
    $("#legion-ide-reject").onclick = () => respond(false);
    $("#legion-ide-commit").onclick = commit;
    $("#legion-ide-context").onclick = loadContext;
    $("#legion-ide-new-session").onclick = newSession;
    $("#legion-ide-snippet-load").onclick = loadSnippet;
    $("#legion-ide-snippet-browse").onclick = () => browseFiles($("#legion-ide-snippet-path").value || ".");
    $("#legion-ide-snippet-insert").onclick = insertSnippet;
    $("#legion-ide-old-pick").onclick = openOldPicker;
    renderSessions();
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && $("#legion-ide-overlay")) closePanel();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", buildLauncher);
  } else {
    buildLauncher();
  }
})();

