// ── A0 田丰 · Hermes 主聊天模式（2026-10-30）────────────────────────────
// 只做客户端路由：开启后主聊天发送走 /api/ide/orchestrate（Hermes 规划 + OH 执行），
// 关闭时完全走原 Hermes。会话不写 Hermes 历史，刷新后只保留当前消息流。
(function () {
  const KEY = "legion-a0-mainchat";
  let on = false;
  try { on = localStorage.getItem(KEY) === "1"; } catch (_) {}
  let busy = false;
  let taskSel = null;

  function base() {
    return location.port === "8787" ? "http://127.0.0.1:8796" : "/openhands";
  }

  function endpoint() {
    return base() + "/api/ide/orchestrate";
  }

  async function loadTasks() {
    if (!taskSel) return;
    try {
      const r = await fetch(base() + "/api/ide/tasks", { credentials: "include" });
      const d = await r.json();
      const rows = (d && d.tasks) || [];
      taskSel.innerHTML = "";
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "不指定工单";
      taskSel.appendChild(empty);
      rows.slice().sort(function (a, b) { return Number(b.exists) - Number(a.exists); }).forEach(function (t) {
        const o = document.createElement("option");
        o.value = t.task;
        o.dataset.exists = t.exists ? "1" : "0";
        o.textContent = t.task + " · " + (t.status || "-") + " · [" +
          (t.exists ? "复用已有" : "将新建 worktree") + "] · " + (t.desc || "");
        taskSel.appendChild(o);
      });
    } catch (_) {}
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
    const task = taskSel ? String(taskSel.value || "") : "";
    if (task && taskSel && taskSel.selectedOptions && taskSel.selectedOptions[0] &&
        taskSel.selectedOptions[0].dataset.exists === "0") {
      if (!window.confirm("工单 " + task + " 当前没有 worktree，将新建 task/" + task + "，确认继续？")) return;
    }
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
        body: JSON.stringify({ message: text, task: task, agent: "A0" }),
      });
      const d = await r.json().catch(() => ({}));
      let out;
      if (!d.ok) out = "A0 编排失败：" + (d.error || d.detail || ("HTTP " + r.status));
      else if (d.mode === "answer") out = d.plan || "（A0 未返回计划）";
      else if (d.mode === "executed") {
        out = "A0 已规划并启动 OH 执行：\n" + (d.plan || "") +
          "\n\n工单：" + (d.task || task || "—") +
          "\n会话：" + (d.conversation_id || "—");
      } else out = d.plan || d.error || "A0 已处理";
      if (d.run_id) out += "\n\nrun：" + d.run_id;
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
    taskSel = document.createElement("select");
    taskSel.id = "a0-main-task";
    taskSel.title = "A0 开发工单（OH 使用对应 worktree）";
    taskSel.style.cssText = "margin-right:6px;max-width:230px;padding:3px 6px;border-radius:6px;" +
      "border:1px solid #555;background:#1b1b1b;color:inherit;font-size:12px;";
    footer.prepend(taskSel);
    footer.prepend(btn);
    loadTasks();
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else build();
})();
