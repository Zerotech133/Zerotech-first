/* 军团 IDE · Hermes 扩展 panel（方式 A 原型） */
(function () {
  if (window.__legionIdeExtensionLoaded) return;
  window.__legionIdeExtensionLoaded = true;

  const OH_BASE = window.LEGION_OH_BASE || "http://127.0.0.1:8799";
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

  function openPanel() {
    if ($("#legion-ide-overlay")) return;
    const wrap = el("div");
    wrap.innerHTML = panelHtml();
    document.body.appendChild(wrap.firstElementChild);
    bindPanel();
    loadTasks();
  }

  function closePanel() {
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
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

  async function poll() {
    if (!state.cid || state.pollBusy) return;
    state.pollBusy = true;
    try {
      const d = await api("/api/ide/openhands/status?id=" + encodeURIComponent(state.cid));
      state.events = d.events || [];
      renderEvents();
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
        $("#legion-ide-stop").disabled = true;
        $("#legion-ide-start").disabled = false;
        state.cid = "";
        if (st === "finished") loadDiff($("#legion-ide-task").value);
        return;
      }
      schedulePoll();
    } catch (e) {
      status("状态失败：" + e.message, "bad");
      schedulePoll();
    } finally {
      state.pollBusy = false;
    }
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
        body: JSON.stringify({ message, task, model }),
      });
      state.cid = d.conversation_id;
      state.task = task;
      state.baseSteps = [
        "worktree：" + (d.worktree || "default"),
        "模型：" + (d.model || model),
      ];
      renderEvents();
      status("运行中…", "run");
      schedulePoll();
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
      schedulePoll();
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
      schedulePoll();
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
