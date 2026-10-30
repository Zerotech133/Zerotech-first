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
              <button id="legion-ide-clip">剪贴板引用</button>
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
    'profiles', 'insights', 'legionide', 'legionlde'];

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

  let LDE_OWNER = "hermes-lde";
  const LDE_BASE = (location.port === "8734") ? "/lde" : "http://127.0.0.1:8734/lde";
  let LDE_PREVIEW = null;

  function ldeStatus(text, kind) {
    const n = $("#lde-status");
    if (!n) return;
    n.textContent = text;
    n.className = "lde-status " + (kind || "");
  }

  async function ldeApi(path, opts) {
    const o = Object.assign({ credentials: "include", headers: {} }, opts || {});
    if (o.body !== undefined) {
      o.headers = Object.assign({ "Content-Type": "application/json" }, o.headers);
    }
    const r = await fetch(LDE_BASE + path, o);
    let d = {};
    try { d = await r.json(); } catch (e) { d = { ok: false, error: "bad_response" }; }
    if (!r.ok || d.ok === false) throw new Error(d.message || d.error || ("HTTP " + r.status));
    return d;
  }

  async function ldeScope() {
    const sel = $("#lde-task");
    if (!sel) return;
    ldeStatus("读取可写范围…");
    try {
      const d = await ldeApi("/api/ide/scope");
      const items = d.items || [];
      const keep = sel.value;
      sel.innerHTML = items.length
        ? items.map((it) => `<option value="${esc(it.task)}">${esc(it.task)} · ${esc(it.status)}</option>`).join("")
        : '<option value="">（无可写 worktree）</option>';
      if (keep && items.some((it) => it.task === keep)) sel.value = keep;
      ldeStatus("可写工单 " + items.length + " 个", items.length ? "ok" : "warn");
    } catch (e) {
      ldeStatus("范围不可用：" + e.message, "bad");
    }
  }

  async function ldeTree(path) {
    const box = $("#lde-tree");
    const cwd = $("#lde-tree-cwd");
    if (!box) return;
    const p = String(path || ".").trim() || ".";
    try {
      const sel = $("#lde-task");
      const q = new URLSearchParams({ task: sel ? sel.value : "", path: p });
      const d = await ldeApi("/api/ide/tree?" + q.toString());
      if (cwd) cwd.textContent = p;
      box.innerHTML = "";
      const items = d.items || [];
      items.forEach((it) => {
        const b = el("button", "lde-file " + (it.type === "dir" ? "dir" : "file"),
          (it.type === "dir" ? "📁 " : "📄 ") + it.name);
        b.title = it.path;
        b.addEventListener("click", () => {
          if (it.type === "dir") { ldeTree(it.path); return; }
          $("#lde-path").value = it.path;
          ldeRead();
        });
        box.appendChild(b);
      });
      if (!items.length) box.textContent = "（空目录）";
    } catch (e) {
      box.textContent = "文件树不可用：" + e.message;
    }
  }

  async function ldeGit(action) {
    const p = $("#lde-path");
    const out = $("#lde-diff");
    if (!p) return;
    const path = p.value.trim();
    if (!path) { ldeStatus("先选择/填写文件路径", "warn"); return; }
    let message = "";
    if (action === "commit") {
      message = window.prompt("提交说明：", "LDE: " + path);
      if (!message) return;
    }
    try {
      const d = await ldeApi("/api/ide/git", {
        method: "POST",
        body: JSON.stringify(ldeBody({ action, path, message })),
      });
      if (out) out.textContent = d.output || "（无输出）";
      ldeStatus("git " + action + " " + path + " · exit=" + d.exit_code, d.ok ? "ok" : "bad");
      if (action === "commit") ldeTree($("#lde-tree-cwd")?.textContent || ".");
    } catch (e) {
      ldeStatus("git " + action + " 失败：" + e.message, "bad");
    }
  }

  async function ldeLockInfo() {
    const chip = $("#lde-lockchip");
    const sel = $("#lde-task");
    if (!chip || !sel || !sel.value) return;
    try {
      const q = new URLSearchParams({ task: sel.value });
      const d = await ldeApi("/api/ide/lock?" + q.toString());
      if (d.locked) {
        chip.textContent = "锁 " + (d.owner || "?") + " · " + Math.round(d.age_s || 0) + "s";
        chip.title = d.stale ? "锁已过期，可重新获取" : "锁被占用，等待释放或 TTL 过期";
      } else if (d.stale) {
        chip.textContent = "锁 已过期";
        chip.title = "过期锁，可直接获取";
      } else {
        chip.textContent = "锁 —";
        chip.title = "当前无锁";
      }
    } catch (e) {
      chip.textContent = "锁 读取失败";
      chip.title = e.message;
    }
  }

  function ldeBody(extra) {
    const sel = $("#lde-task");
    return Object.assign({ task: sel ? sel.value : "", owner: LDE_OWNER }, extra || {});
  }

  async function ldeRead() {
    const p = $("#lde-path");
    const ta = $("#lde-content");
    if (!p || !ta) return;
    try {
      const d = await ldeApi("/api/ide/read", {
        method: "POST", body: JSON.stringify(ldeBody({ path: p.value.trim() })),
      });
      ta.value = d.content || "";
      LDE_PREVIEW = null;
      $("#lde-confirm").style.display = "none";
      $("#lde-cancel-preview").style.display = "none";
      $("#lde-preview").style.display = "";
      $("#lde-diff").textContent = "—";
      ldeStatus("已读取 " + d.path + " · " + d.bytes + "B", "ok");
    } catch (e) { ldeStatus("读取失败：" + e.message, "bad"); }
  }

  async function ldePreview() {
    const p = $("#lde-path");
    const ta = $("#lde-content");
    if (!p || !ta) return;
    try {
      const d = await ldeApi("/api/ide/write/preview", {
        method: "POST",
        body: JSON.stringify(ldeBody({ path: p.value.trim(), content: ta.value })),
      });
      LDE_PREVIEW = d;
      $("#lde-diff").textContent = d.diff || "（无差异）";
      $("#lde-preview").style.display = "none";
      $("#lde-confirm").style.display = "";
      $("#lde-cancel-preview").style.display = "";
      ldeStatus("预览就绪：" + d.path + (d.diff_truncated ? " · diff 已截断" : " · 请确认写入"), "warn");
    } catch (e) { ldeStatus("预览失败：" + e.message, "bad"); }
  }

  async function ldeConfirm() {
    if (!LDE_PREVIEW) { ldeStatus("没有待确认的预览", "warn"); return; }
    try {
      const d = await ldeApi("/api/ide/write/confirm", {
        method: "POST", body: JSON.stringify(ldeBody({ preview_id: LDE_PREVIEW.preview_id })),
      });
      ldeStatus("已写入 " + d.path + " · " + d.bytes + "B" + (d.created ? " · 新建" : ""), "ok");
      LDE_PREVIEW = null;
      $("#lde-confirm").style.display = "none";
      $("#lde-cancel-preview").style.display = "none";
      $("#lde-preview").style.display = "";
    } catch (e) { ldeStatus("确认失败：" + e.message, "bad"); }
  }

  async function ldeUndo() {
    const p = $("#lde-path");
    if (!p || !p.value.trim()) { ldeStatus("先选择/填写文件路径", "warn"); return; }
    try {
      const d = await ldeApi("/api/ide/undo", {
        method: "POST",
        body: JSON.stringify(ldeBody({ path: p.value.trim() })),
      });
      ldeStatus("已撤销写入：" + d.path, "ok");
      $("#lde-diff").textContent = "已撤销到写入前内容";
      await ldeRead();
    } catch (e) { ldeStatus("撤销失败：" + e.message, "warn"); }
  }

  async function ldeLock() {
    try {
      await ldeApi("/api/ide/lock", { method: "POST", body: JSON.stringify(ldeBody()) });
      await ldeLockInfo();
      ldeStatus("已获取工单锁（owner=" + LDE_OWNER + "）", "ok");
    } catch (e) {
      ldeStatus("取锁失败：" + e.message, "warn");
      ldeLockInfo();
    }
  }

  async function ldeUnlock() {
    try {
      const sel = $("#lde-task");
      const q = new URLSearchParams({ task: sel ? sel.value : "", owner: LDE_OWNER });
      await ldeApi("/api/ide/lock?" + q.toString(), { method: "DELETE" });
      await ldeLockInfo();
      ldeStatus("已释放锁", "ok");
    } catch (e) { ldeStatus("释放失败：" + e.message, "bad"); }
  }

  async function ldeExec() {
    const cmd = $("#lde-cmd");
    const out = $("#lde-output");
    if (!cmd || !out) return;
    try {
      const d = await ldeApi("/api/ide/exec", {
        method: "POST", body: JSON.stringify(ldeBody({ command: cmd.value.trim() })),
      });
      const success = (typeof d.success === "boolean") ? d.success : (d.exit_code === 0);
      ldeStatus((success ? "✅ success" : "❌ failed") + " · exit=" + d.exit_code + " · " + d.duration_ms + "ms", success ? "ok" : "bad");
      out.textContent = (d.output || "（无输出）") + (d.truncated ? "\n[输出已截断]" : "");
    } catch (e) {
      ldeStatus("执行被拒：" + e.message, "bad");
      out.textContent = "—";
    }
  }

  async function ldeAudit() {
    const out = $("#lde-audit-out");
    if (!out) return;
    try {
      const sel = $("#lde-task");
      const q = new URLSearchParams({ task: sel ? sel.value : "" });
      const d = await ldeApi("/api/ide/audit?" + q.toString());
      out.textContent = (d.events || []).length
        ? d.events.map((e) => e.ts + "  " + e.kind + "  " + e.detail).join("\n")
        : "（本单暂无 code_action / code_observation）";
    } catch (e) { ldeStatus("审计不可用：" + e.message, "bad"); }
  }

  function ldeHtml() {
    return `
      <section id="mainLegionLde" class="main-view legion-lde-view">
        <header class="lde-head">
          <div><b>LDE 施工台</b><span>文件写 / 锁 / 白名单执行 / 审计</span></div>
          <span id="lde-status" class="lde-status">初始化…</span>
        </header>
        <div class="lde-body">
          <div class="lde-left">
            <div class="lde-row">
              <label>工单</label>
              <select id="lde-task"></select>
              <button id="lde-refresh">刷新范围</button>
              <span id="lde-lockchip" class="lde-lockchip">锁 —</span>
              <button id="lde-lock">获取锁</button>
              <button id="lde-unlock">释放</button>
            </div>
            <div class="lde-row">
              <input id="lde-path" placeholder="文件相对路径，例如 agent_platform/foo.py">
              <button id="lde-read">读取</button>
            </div>
            <div class="lde-tree-bar">
              <span>文件树</span>
              <button id="lde-tree-root">根目录</button>
              <span id="lde-tree-cwd">.</span>
            </div>
            <div id="lde-tree" class="lde-tree"></div>
            <textarea id="lde-content" spellcheck="false" placeholder="文件内容（UTF-8）"></textarea>
            <div class="lde-actions">
              <button id="lde-preview" class="primary">预览变更</button>
              <button id="lde-confirm" class="primary" style="display:none">确认写入</button>
              <button id="lde-cancel-preview" style="display:none">取消预览</button>
              <button id="lde-undo" title="撤销最近一次确认写入（内存快照 10 分钟）">撤销上次写入</button>
            </div>
            <div class="lde-label">diff</div>
            <pre id="lde-diff">—</pre>
            <div class="lde-actions">
              <button id="lde-git-diff">文件 diff</button>
              <button id="lde-git-add">git add</button>
              <button id="lde-git-commit">commit</button>
            </div>
          </div>
          <div class="lde-right">
            <div class="lde-label">白名单执行</div>
            <div class="lde-row">
              <input id="lde-cmd" placeholder="git status -s / python -m pytest ...">
              <button id="lde-exec" class="primary">执行</button>
            </div>
            <pre id="lde-output">—</pre>
            <div class="lde-actions"><button id="lde-audit">本单审计</button></div>
            <div class="lde-label">审计</div>
            <pre id="lde-audit-out" class="lde-audit">—</pre>
          </div>
        </div>
      </section>`;
  }

  function ensureLdePanel() {
    const main = document.querySelector('main.main');
    if (!main) return false;
    if ($("#mainLegionLde")) return true;
    const host = document.createElement('section');
    host.className = 'main-view';
    host.innerHTML = ldeHtml();
    main.appendChild(host.firstElementChild);
    bindLde();
    ldeInit();
    return true;
  }

  async function ldeSession() {
    try {
      const r = await fetch(LDE_BASE + "/api/ide/session", { credentials: "include" });
      const d = await r.json();
      if (d && d.owner) LDE_OWNER = d.owner;
    } catch (e) { /* 保持降级：后续请求会返回 no_session */ }
  }

  async function ldeInit() {
    try {
      // 先访问 /rw 种 ide_session cookie，再取服务端绑定的 owner
      await fetch(LDE_BASE + "/rw", { credentials: "include" });
      await ldeSession();
      await ldeScope();
      await ldeTree(".");
      await ldeLockInfo();
      ldeStatus("就绪", "ok");
    } catch (e) {
      ldeStatus("LDE 不可用：" + e.message, "bad");
    }
  }

  function bindLde() {
    $("#lde-refresh").onclick = ldeScope;
    $("#lde-read").onclick = ldeRead;
    $("#lde-preview").onclick = ldePreview;
    $("#lde-confirm").onclick = ldeConfirm;
    $("#lde-cancel-preview").onclick = () => {
      LDE_PREVIEW = null;
      $("#lde-preview").style.display = "";
      $("#lde-confirm").style.display = "none";
      $("#lde-cancel-preview").style.display = "none";
      $("#lde-diff").textContent = "—";
    };
    $("#lde-lock").onclick = ldeLock;
    $("#lde-unlock").onclick = ldeUnlock;
    $("#lde-exec").onclick = ldeExec;
    $("#lde-audit").onclick = ldeAudit;
    $("#lde-tree-root").onclick = () => ldeTree(".");
    $("#lde-undo").onclick = ldeUndo;
    $("#lde-git-diff").onclick = () => ldeGit("diff");
    $("#lde-git-add").onclick = () => ldeGit("add");
    $("#lde-git-commit").onclick = () => ldeGit("commit");
    $("#lde-task").onchange = () => {
      LDE_PREVIEW = null;
      $("#lde-confirm").style.display = "none";
      $("#lde-cancel-preview").style.display = "none";
      $("#lde-preview").style.display = "";
      ldeTree(".");
      ldeLockInfo();
    };
  }

  function showLdePanel() {
    if (!ensureLdePanel()) return false;
    const main = document.querySelector('main.main');
    MAIN_CLASSES.forEach((c) => main.classList.remove('showing-' + c));
    main.classList.add('showing-legionlde');
    document.querySelectorAll('[data-panel]').forEach((t) => {
      t.classList.toggle('active', t.dataset.panel === 'legionlde');
    });
    return true;
  }

  function addLdeNavButton(container, cls, label) {
    if (!container || container.querySelector('[data-panel="legionlde"]')) return;
    const b = el('button', cls);
    b.dataset.panel = 'legionlde';
    b.title = label;
    b.setAttribute('aria-label', label);
    b.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>';
    if (cls.indexOf('nav-tab') >= 0 && cls.indexOf('rail-btn') < 0) b.dataset.label = label;
    b.addEventListener('click', (e) => { e.preventDefault(); showLdePanel(); });
    const anchor = container.querySelector('.rail-spacer') ||
      container.querySelector('[data-panel="settings"]');
    if (anchor) container.insertBefore(b, anchor);
    else container.appendChild(b);
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
    addLdeNavButton(document.querySelector('.rail'), 'rail-btn nav-tab', 'LDE 施工台');
    addLdeNavButton(document.querySelector('.sidebar-nav'), 'nav-tab', 'LDE 施工台');
    // 点回 Hermes 内置导航时，退出军团 IDE / LDE 主视图
    document.addEventListener('click', (e) => {
      const t = e.target && e.target.closest ? e.target.closest('[data-panel]') : null;
      if (!t) return;
      const main = document.querySelector('main.main');
      if (!main) return;
      if (t.dataset.panel !== 'legionide') main.classList.remove('showing-legionide');
      if (t.dataset.panel !== 'legionlde') main.classList.remove('showing-legionlde');
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
let SESSIONS = [];

function readSessions() {
  if (SESSIONS.length) return SESSIONS;
  try {
    const v = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "[]");
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}

async function loadServerSessions() {
  try {
    const d = await api("/api/ide/openhands/sessions?limit=30");
    const local = readSessions();
    const map = new Map();
    [...local, ...(d.sessions || [])].forEach((s) => {
      if (!s || !s.cid) return;
      const old = map.get(s.cid);
      map.set(s.cid, Object.assign({}, old || {}, s));
    });
    SESSIONS = [...map.values()]
      .sort((a, b) => Number(b.updated || 0) - Number(a.updated || 0))
      .slice(0, 20);
    writeSessions(SESSIONS);
    renderSessions();
  } catch (e) { /* 旧 8796 没有 sessions API，保留本地历史 */ }
}

function writeSessions(list) {
  SESSIONS = list.slice(0, 20);
  try { localStorage.setItem(SESSIONS_KEY, JSON.stringify(SESSIONS)); } catch (e) {}
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
  api("/api/ide/openhands/sessions/delete", {
    method: "POST", body: JSON.stringify({ cid }),
  }).catch(() => {});
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
  rows.push('<div class="legion-ide-detail-actions"><button id="legion-ide-edit-prompt">在 LDE 编辑 prompt</button>'
    + '<button id="legion-ide-copy-skills">复制技能</button></div>');
  box.innerHTML = rows.join("");
  const promptBtn = box.querySelector("#legion-ide-edit-prompt");
  if (promptBtn) {
    promptBtn.disabled = !(a.prompt_file || a.prompt);
    promptBtn.onclick = () => openPromptInLde(a.prompt_file || "", a);
  }
  const copyBtn = box.querySelector("#legion-ide-copy-skills");
  if (copyBtn) {
    copyBtn.onclick = () => copyText((a.skills || []).join("\n") || (a.prompt || ""), "技能已复制");
  }
}

async function openPromptInLde(path, agent) {
  showLdePanel();
  await new Promise((r) => setTimeout(r, 500));
  const taskSel = $("#lde-task");
  if (taskSel && !taskSel.value && taskSel.options.length) taskSel.value = taskSel.options[0].value;
  const p = $("#lde-path");
  if (!p) return;
  p.value = path || ("agent_platform/prompts/" + String(agent && agent.id || "a0").toLowerCase() + ".md");
  const ta = $("#lde-content");
  if (ta && agent && agent.prompt && !path) ta.value = agent.prompt;
  if (path) ldeRead();
  else ldeStatus("已填入 prompt 路径，可按读取/编辑", "warn");
}

async function copyText(text, okMsg) {
  try {
    await navigator.clipboard.writeText(String(text || ""));
    const st = $("#legion-ide-snippet-status");
    if (st) st.textContent = okMsg || "已复制";
  } catch (e) {
    const st = $("#legion-ide-snippet-status");
    if (st) st.textContent = "复制失败：" + e.message;
  }
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
  if (st) st.textContent = "已插入 " + t.split("\n").length + " 行，正在补上下文…";
  enrichSelectionContext(t, label).catch(() => {});
}

function _selectionPath(label) {
  const m = String(label || "").match(/([A-Za-z0-9_./-]+\.(?:py|js|html|css|md|json|ps1|bat|toml|yaml|yml))/);
  return m ? m[1] : "";
}

function _diffNearFile(diff, path, limit) {
  const text = String(diff || "");
  if (!path) return "";
  const needle = "diff --git a/" + path + " ";
  let i = text.indexOf(needle);
  if (i < 0) i = text.indexOf("diff --git a/" + path + "\n");
  if (i < 0) return "";
  let j = text.indexOf("\ndiff --git ", i + 1);
  const part = text.slice(i, j > 0 ? j : i + 2200);
  return part.length > (limit || 1800) ? part.slice(0, limit || 1800) + "\n…" : part;
}

async function enrichSelectionContext(text, label) {
  const msg = $("#legion-ide-message");
  const st = $("#legion-ide-snippet-status");
  if (!msg) return;
  const q = String(text || "").replace(/\s+/g, " ").slice(0, 600);
  if (!q) return;
  let block = "";
  try {
    const ctx = await api("/api/ide/context?q=" + encodeURIComponent(q) + "&budget=2500");
    const syms = (ctx.symbols || []).slice(0, 6);
    if (syms.length) {
      block += "\n[自动上下文 · AST]\n" + syms.map((s) =>
        "- " + s.kind + " " + s.path + ":" + s.line + " " + s.name).join("\n");
    }
  } catch (e) {}
  const task = $("#legion-ide-task") && $("#legion-ide-task").value;
  const path = _selectionPath(label);
  if (task && path) {
    try {
      const dd = await api("/api/ide/openhands/diff", {
        method: "POST", body: JSON.stringify({ task }),
      });
      let part = _diffNearFile(dd.diff, path, 1600);
      let diffLabel = path;
      if (!part && dd.has_changes && dd.diff) {
        // 选中文件不在本单 diff 里时，注入工单变更摘要作为邻近上下文。
        part = String(dd.diff).slice(0, 1200);
        diffLabel = "工单变更";
      }
      if (part) block += "\n[邻近 diff · " + diffLabel + "]\n```diff\n" + part + "\n```";
    } catch (e) {}
  }
  if (!block) {
    if (st) st.textContent = "已插入（无额外上下文命中）";
    return;
  }
  msg.value = msg.value.trimEnd() + "\n" + block + "\n";
  if (st) st.textContent = "已插入并自动补充上下文";
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

async function pasteClipboard() {
  const st = $("#legion-ide-snippet-status");
  try {
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      if (st) st.textContent = "浏览器不支持剪贴板读取";
      return;
    }
    const text = await navigator.clipboard.readText();
    if (!String(text || "").trim()) {
      if (st) st.textContent = "剪贴板为空";
      return;
    }
    appendSelection(text, "剪贴板选中");
    if (st) st.textContent = "已从剪贴板插入";
  } catch (e) {
    if (st) st.textContent = "剪贴板失败：" + e.message;
  }
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
    $("#legion-ide-events").textContent = "A0/Hermes 编排中…";
    try {
      // 方案 1：Hermes 先规划/决定是否执行，OH 只负责执行。
      const d = await api("/api/ide/orchestrate", {
        method: "POST",
        body: JSON.stringify({ message, task, model: ohModelName(model), agent: SELECTED_AGENT }),
      });
      if (!d.ok) throw new Error(d.error || d.detail || "orchestrate_failed");
      if (d.plan) {
        $("#legion-ide-reply").textContent = "A0/席位计划：\n" + d.plan;
        $("#legion-ide-reply-wrap").style.display = "";
      }
      if (d.mode === "answer" || !d.conversation_id) {
        state.cid = "";
        state.baseSteps = ["A0 判断：无需执行", "binding：" + (d.binding || "—")];
        renderEvents();
        status("A0 已答复，未执行", "ok");
        $("#legion-ide-start").disabled = false;
        $("#legion-ide-stop").disabled = true;
        return;
      }
      state.cid = d.conversation_id;
      state.task = task;
      upsertSession({
        cid: state.cid, task, agent: SELECTED_AGENT, model,
        title: message.slice(0, 80), status: "running",
      });
      state.baseSteps = [
        "A0 编排：" + (d.agent_name || d.agent || "—") + " · " + (d.binding || "—"),
        "worktree：" + (d.worktree || "default"),
        "模型：" + (d.model || model),
      ];
      renderEvents();
      status("A0 已计划，OH 执行中…", "run");
      startStream(state.cid);
    } catch (e) {
      status("编排/启动失败：" + e.message, "bad");
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
    $("#legion-ide-clip").onclick = pasteClipboard;
    renderSessions();
    loadServerSessions();
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




/* ===== 司马徽研究院 · Hermes 扩展入口（2026-10-30） ===== */
(function () {
  if (window.__acad1ExtLoaded) return;
  window.__acad1ExtLoaded = true;
  const BASE = "http://127.0.0.1:8102/api/v1";
  const PAGE = "http://127.0.0.1:5175/cn/replay?tab=selection";

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function openPanel() {
    let p = document.getElementById("acad1-panel");
    if (p) { p.style.display = "flex"; return; }
    p = el("div", "acad1-overlay"); p.id = "acad1-panel";
    const box = el("div", "acad1-box");
    const head = el("div", "acad1-head");
    const title = el("b", null, "司马徽研究院 · 选股中台");
    const close = el("button", "acad1-close", "×");
    close.onclick = () => { p.style.display = "none"; };
    head.append(title, close);
    const sum = el("div", "acad1-sum", "摘要加载中…");
    sum.id = "acad1-summary";
    const frame = el("iframe", "acad1-frame");
    frame.src = PAGE;  // L2-A：CSP 已放行 5175，直接嵌研究台原站
    box.append(head, sum, frame);
    p.append(box);
    document.body.appendChild(p);
    fetch(BASE + "/selection-public/summary")
      .then(r => r.json())
      .then(d => {
        const x = (d && d.data) || {};
        sum.textContent = "数据日 " + (x.data_date || "—") +
          " · 日报 " + (x.report_total || 0) +
          " · 候选池 " + (x.pool_total || 0) +
          " · 共振 " + (x.resonance || 0) +
          " · 反馈 用" + (x.used || 0) + "/不用" + (x.rejected || 0) +
          " · 督导 " + (x.supervision_total || 0);
      })
      .catch(() => { sum.textContent = "摘要不可用；页面内查看研究台"; });
  }

  function build() {
    if (document.getElementById("acad1-btn")) return;
    const btn = el("button", "acad1-btn", "司马徽");
    btn.id = "acad1-btn";
    btn.title = "司马徽研究院 · 选股中台";
    btn.onclick = openPanel;
    document.body.appendChild(btn);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();

// ── A0 田丰 · Hermes 主聊天模式（2026-10-30）────────────────────────────
// 只做客户端路由：开启后主聊天发送走 /api/ide/orchestrate（Hermes 规划 + OH 执行），
// 关闭时完全走原 Hermes。会话不写 Hermes 历史，刷新后只保留当前消息流。
(function () {
  const KEY = "legion-a0-mainchat";
  let on = false;
  try { on = localStorage.getItem(KEY) === "1"; } catch (_) {}
  let busy = false;

  function endpoint() {
    return (location.port === "8787" ? "http://127.0.0.1:8796" : "/openhands")
      + "/api/ide/orchestrate";
  }

  function pushMsg(role, content) {
    try {
      if (typeof S !== "undefined" && Array.isArray(S.messages)) {
        S.messages.push({ role: role, content: content });
        if (typeof renderMessages === "function") renderMessages();
      }
    } catch (_) {}
  }

  function setBusy(v) {
    busy = !!v;
    try { if (typeof S !== "undefined") S.busy = busy; } catch (_) {}
    try { if (typeof updateSendBtn === "function") updateSendBtn(); } catch (_) {}
  }

  async function runA0(text) {
    const ta = document.getElementById("msg");
    if (ta) {
      ta.value = "";
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    }
    pushMsg("user", text);
    const placeholder = { role: "assistant", content: "A0 田丰规划中…" };
    try {
      if (typeof S !== "undefined" && Array.isArray(S.messages)) {
        S.messages.push(placeholder);
        if (typeof renderMessages === "function") renderMessages();
      }
    } catch (_) {}
    setBusy(true);
    try {
      const r = await fetch(endpoint(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ message: text, task: "", agent: "A0" }),
      });
      const d = await r.json().catch(() => ({}));
      let out;
      if (!d.ok) out = "A0 编排失败：" + (d.error || d.detail || ("HTTP " + r.status));
      else if (d.mode === "answer") out = d.plan || "（A0 未返回计划）";
      else if (d.mode === "executed") {
        out = "A0 已规划并启动 OH 执行：\n" + (d.plan || "") +
          "\n\n会话：" + (d.conversation_id || "—");
      } else out = d.plan || d.error || "A0 已处理";
      placeholder.content = out;
      if (typeof renderMessages === "function") renderMessages();
    } catch (e) {
      placeholder.content = "A0 编排失败：" + (e && e.message ? e.message : e);
      try { if (typeof renderMessages === "function") renderMessages(); } catch (_) {}
    } finally {
      setBusy(false);
    }
  }

  function intercept(ev) {
    if (!on || busy) return;
    const ta = document.getElementById("msg");
    const text = ta && String(ta.value || "").trim();
    if (!text) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    runA0(text);
  }

  function onClick(ev) {
    const btn = ev.target && ev.target.closest ? ev.target.closest("#btnSend") : null;
    if (btn) intercept(ev);
  }

  function onKey(ev) {
    if (!ev || ev.key !== "Enter" || ev.shiftKey || ev.ctrlKey || ev.altKey) return;
    const ta = ev.target;
    if (!ta || ta.id !== "msg") return;
    intercept(ev);
  }

  function paint(btn) {
    btn.textContent = on ? "A0 田丰 · 开" : "A0 田丰";
    btn.title = on
      ? "主聊天已切到 A0 田丰（Hermes 规划 + OH 执行）；点击关闭"
      : "主聊天切到 A0 田丰（不改 Hermes 默认）";
    btn.style.cssText = "margin-right:6px;padding:4px 8px;border-radius:999px;border:1px solid " +
      (on ? "#c6a15b" : "#555") + ";background:" + (on ? "#3b2f18" : "transparent") +
      ";color:" + (on ? "#ffd98a" : "inherit") + ";font-size:12px;cursor:pointer";
  }

  function build() {
    if (document.getElementById("a0-main-btn")) return;
    const footer = document.querySelector("#composerBox .composer-footer .composer-left");
    if (!footer) return;
    const btn = document.createElement("button");
    btn.id = "a0-main-btn";
    btn.type = "button";
    btn.onclick = function () {
      on = !on;
      try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (_) {}
      paint(btn);
    };
    paint(btn);
    footer.prepend(btn);
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else build();
})();

/* 诸葛亮只读复核面板（P0，feature flag 由后端 /api/zhuge/review/status 控制） */
(function () {
  if (window.__zhugeReviewLoaded) return;
  window.__zhugeReviewLoaded = true;
  const $ = (s) => document.querySelector(s);
  let panel = null;
  let timer = null;
  let zrEnabled = false;

  function style() {
    if ($("#zr-style")) return;
    const s = document.createElement("style");
    s.id = "zr-style";
    s.textContent = `
      #zr-review-btn{margin-left:6px;padding:4px 8px;border-radius:999px;border:1px solid #6b5a2a;background:#2a2416;color:#ffd98a;font-size:12px;cursor:pointer}
      #zr-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:99998;display:none}
      #zr-panel{position:fixed;right:18px;top:60px;width:min(520px,92vw);max-height:82vh;overflow:auto;background:#151515;color:#eee;border:1px solid #444;border-radius:10px;z-index:99999;padding:14px;display:none;font-size:13px}
      #zr-panel h3{margin:0 0 8px;font-size:15px;color:#ffd98a}
      #zr-panel textarea{width:100%;height:120px;background:#0d0d0d;color:#ddd;border:1px solid #333;border-radius:6px;padding:6px;font:12px/1.4 monospace}
      #zr-panel button{margin-top:6px;padding:5px 10px;border-radius:6px;border:1px solid #555;background:#222;color:#eee;cursor:pointer}
      #zr-status{color:#aaa;margin:6px 0}
      .zr-finding{border-left:3px solid #c6a15b;padding:4px 8px;margin:6px 0;background:#1d1d1d}
      .zr-finding b{color:#ffd98a}
    `;
    document.head.appendChild(s);
  }

  function ensurePanel() {
    if (panel) return panel;
    style();
    const ov = document.createElement("div");
    ov.id = "zr-overlay";
    ov.onclick = () => { ov.style.display = "none"; panel.style.display = "none"; };
    const p = document.createElement("div");
    p.id = "zr-panel";
    p.innerHTML = `<h3>⚔ 诸葛亮 · 只读复核</h3>
      <div>任务：<span id="zr-task">—</span></div>
      <div id="zr-status">读取中…</div>
      <textarea id="zr-diff" placeholder="自动读取 LDE diff；为空时请先点「文件 diff」"></textarea>
      <button id="zr-load">读取 diff</button>
      <button id="zr-submit">提交复核</button>
      <div id="zr-result"></div>`;
    document.body.appendChild(ov);
    document.body.appendChild(p);
    panel = p;
    $("#zr-load").onclick = loadDiff;
    $("#zr-submit").onclick = submit;
    return p;
  }

  function taskId() {
    const sel = $("#lde-task");
    return sel ? String(sel.value || "").trim() : "";
  }

  function loadDiff() {
    if (!zrEnabled) return;
    const tx = $("#zr-diff");
    const cur = ($("#lde-diff") && $("#lde-diff").textContent || "").trim();
    if (!cur || cur === "—") {
      $("#zr-status").textContent = "没有读到 diff：先在 LDE 点「文件 diff」";
      return;
    }
    tx.value = cur.slice(0, 64000);
    $("#zr-status").textContent = "diff 已载入，长度 " + tx.value.length;
  }

  function parseFiles(diff) {
    const out = [];
    String(diff || "").split("\n").forEach((l) => {
      const m = l.match(/^\+\+\+ b\/(.+)$/);
      if (m && !out.includes(m[1])) out.push(m[1]);
    });
    return out.slice(0, 100);
  }

  async function open() {
    ensurePanel();
    $("#zr-overlay").style.display = "block";
    panel.style.display = "block";
    $("#zr-task").textContent = taskId() || "（未选工单）";
    $("#zr-result").textContent = "";
    try {
      const r = await fetch("/api/zhuge/review/status", { credentials: "include" });
      const d = await r.json();
      zrEnabled = Boolean(d.enabled);
      const sub = $("#zr-submit");
      const load = $("#zr-load");
      if (sub) sub.disabled = !zrEnabled;
      if (load) load.disabled = !zrEnabled;
      if (zrEnabled) {
        $("#zr-status").textContent =
          "可用 · " + (d.model || "unknown") + " · " + (d.carrier || "");
        loadDiff();
      } else {
        $("#zr-status").textContent = "复核未启用（后端 ZHUGE_IDE_REVIEW=off）";
      }
    } catch (e) {
      $("#zr-status").textContent = "状态读取失败：" + e.message;
    }
  }

  async function submit() {
    if (!zrEnabled) { $("#zr-status").textContent = "复核未启用"; return; }
    const task = taskId();
    const diff = String($("#zr-diff").value || "").trim();
    if (!task) { $("#zr-status").textContent = "先选工单"; return; }
    if (!diff) { $("#zr-status").textContent = "先读取或粘贴 diff"; return; }
    $("#zr-result").textContent = "提交中…";
    $("#zr-status").textContent = "复核中（DeepSeek 约 30–60s）…";
    try {
      const r = await fetch("/api/zhuge/review", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task_id: task, diff: diff, changed_files: parseFiles(diff),
          test_result: {}, context: { goal: task, acceptance: [] },
        }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
      poll(d.review_id);
    } catch (e) {
      $("#zr-status").textContent = "提交失败：" + e.message;
    }
  }

  function poll(rid) {
    if (timer) clearInterval(timer);
    timer = setInterval(async () => {
      try {
        const r = await fetch("/api/zhuge/review/" + encodeURIComponent(rid), { credentials: "include" });
        const d = await r.json();
        if (d.status === "pending") return;
        clearInterval(timer); timer = null;
        $("#zr-status").textContent = "完成 · " + (d.verdict || d.status) + " · " + (d.model || "");
        const box = $("#zr-result");
        box.innerHTML = "";
        (d.findings || []).forEach((f) => {
          const n = document.createElement("div");
          n.className = "zr-finding";
          n.innerHTML = `<b>${escapeHtml(f.severity || "P2")}</b> ${escapeHtml(f.file || "")}:${f.line || 0}<br>` +
            `${escapeHtml(f.claim || "")}<br><small>${escapeHtml(f.evidence || "")}</small>`;
          box.appendChild(n);
        });
        if (!(d.findings || []).length) box.textContent = "无 findings";
      } catch (e) {
        clearInterval(timer); timer = null;
        $("#zr-status").textContent = "查询失败：" + e.message;
      }
    }, 2000);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function mount() {
    if ($("#zr-review-btn")) return;
    const b = document.createElement("button");
    b.id = "zr-review-btn";
    b.type = "button";
    b.textContent = "⚔ 诸葛复核";
    b.onclick = open;
    // LDE 面板懒加载，不能依赖 #lde-lock 在扩展初始化时已存在。
    b.style.cssText = "position:fixed;left:18px;bottom:18px;z-index:99997;" +
      "padding:6px 10px;border-radius:999px;border:1px solid #6b5a2a;" +
      "background:#2a2416;color:#ffd98a;font-size:12px;cursor:pointer;" +
      "box-shadow:0 2px 8px rgba(0,0,0,.4)";
    document.body.appendChild(b);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else mount();
  setTimeout(mount, 1500);
})();
