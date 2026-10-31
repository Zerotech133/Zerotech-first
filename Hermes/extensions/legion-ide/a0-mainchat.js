// ── A0 田丰 · Hermes 主聊天模式（2026-10-30）────────────────────────────
// 只做客户端路由：开启后主聊天发送走 /api/ide/orchestrate（Hermes 规划 + OH 执行），
// 关闭时完全走原 Hermes。会话不写 Hermes 历史，刷新后只保留当前消息流。
//
// WO-LEGION-AUTH-20261031：认证拓扑收口——8787 直连只读。
//   直连 http://127.0.0.1:8787 时页面不带门户 session，不允许调 8796 执行
//   （会被 oh_auth 拦成 403 且语义上是"绕过同源认证的旁路"）；本文件检测
//   location.port==8787 进入只读模式：不 fetch 8796、不发 orchestrate，
//   只回显"直连只读 / 请走 8734"。经 8734 同源反代（/openhands/ → 8796）
//   时行为不变，仍可执行。
(function () {
  const KEY = "legion-a0-mainchat";
  // 直连只读提示（与 docs/audit/2026-10-31_IDE认证拓扑收口_v1.md 同口径）
  const READONLY_MSG = "直连只读：8787 直连不触发 8796 执行。请从 8734 /hermes/ " +
    "登录后使用 A0 执行（同源链路会自动带上门户身份），或为本次执行提供 run:execute cap。";
  let on = false;
  try { on = localStorage.getItem(KEY) === "1"; } catch (_) {}
  let busy = false;
  let taskSel = null;
  let ctrlCid = "";
  let ctrlTask = "";
  let ctrlTimer = null;
  let roHint = null;

  // 直连判定：只有 8787 是 Hermes 原生端口；8734 为门户同源反代入口。
  function isDirectMode() {
    try { return String(location.port || "") === "8787"; } catch (_) { return false; }
  }

  function base() {
    return isDirectMode() ? "http://127.0.0.1:8796" : "/openhands";
  }

  function endpoint() {
    return base() + "/api/ide/orchestrate";
  }

  async function loadTasks() {
    if (!taskSel) return;
    if (isDirectMode()) {
      // 直连只读：不加载工单，也不触碰 8796（避免误以为可执行）。
      taskSel.innerHTML = "";
      const ro = document.createElement("option");
      ro.value = "";
      ro.textContent = "直连只读 · 不加载工单";
      taskSel.appendChild(ro);
      taskSel.disabled = true;
      taskSel.title = "直连只读：请走 8734 /hermes/ 后再执行";
      return;
    }
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
    // 直连只读：不调 8796、不发 execute，只诚实回显引导（WO-LEGION-AUTH-20261031）。
    if (isDirectMode()) {
      pushMsg("user", text);
      pushMsg("assistant", READONLY_MSG);
      return;
    }
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
      if (!d.ok) {
        if (d.error === "oh_auth_required" || r.status === 403) {
          out = "执行未授权：当前请求未带门户 session 或缺少 run:execute cap。\n" +
            "请从 8734 /hermes/ 登录后使用（同源链路会自动带上门户身份）。";
        } else {
          out = "A0 编排失败：" + (d.error || d.detail || ("HTTP " + r.status));
        }
      }
      else if (d.mode === "answer") out = d.plan || "（A0 未返回计划）";
      else if (d.mode === "executed") {
        out = "A0 已规划并启动 OH 执行：\n" + (d.plan || "") +
          "\n\n工单：" + (d.task || task || "—") +
          "\n会话：" + (d.conversation_id || "—") +
          "\n\n可用右下角「A0 控制面板」审批 / 停止 / 查看 diff。";
        if (d.conversation_id) openControl(d.conversation_id, d.task || task || "");
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

  // ── P0-3：A0 控制面板（审批 / 停止 / diff / 打开 IDE）──
  function ctrlApi(path, opts) {
    const o = Object.assign({ credentials: "include" }, opts || {});
    return fetch(base() + path, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || d.ok === false) {
          throw new Error(d.error || d.detail || ("HTTP " + r.status));
        }
        return d;
      });
    });
  }

  function ctrlEl(id) { return document.getElementById(id); }

  function ctrlStatus(text) {
    const el = ctrlEl("a0-ctrl-status");
    if (el) el.textContent = text;
  }

  function ctrlOut(text) {
    const el = ctrlEl("a0-ctrl-out");
    if (!el) return;
    el.style.display = "block";
    el.textContent = String(text || "").slice(0, 12000);
  }

  function ctrlStopPolling() {
    if (ctrlTimer) { clearInterval(ctrlTimer); ctrlTimer = null; }
  }

  async function ctrlRefresh() {
    if (!ctrlCid) return;
    try {
      const d = await ctrlApi("/api/ide/openhands/status?id=" + encodeURIComponent(ctrlCid));
      const ev = (d.events || []).length;
      ctrlStatus((d.status || "running") + " · 事件 " + ev + (d.terminal ? " · 已结束" : ""));
      const wrap = ctrlEl("a0-ctrl-pending");
      if (wrap) {
        if (d.status === "waiting_for_confirmation") {
          wrap.style.display = "block";
          const txt = ctrlEl("a0-ctrl-pending-text");
          if (txt) txt.textContent = "⚠ 等待确认：" + (d.pending_action || "");
        } else {
          wrap.style.display = "none";
        }
      }
      if (d.reply) ctrlOut(d.reply);
      if (d.terminal) { ctrlStopPolling(); return; }
    } catch (e) {
      ctrlStatus("状态失败：" + (e && e.message ? e.message : e));
    }
  }

  async function ctrlAct(act) {
    // 直连只读：控制面板（审批/停止/diff/状态）全部依赖 8796 执行面，一律不调。
    if (isDirectMode()) {
      ctrlStatus("直连只读：控制面板不可用，请走 8734 /hermes/ 后再操作");
      return;
    }
    if (!ctrlCid) return;
    try {
      if (act === "status") {
        await ctrlRefresh();
      } else if (act === "stop") {
        await ctrlApi("/api/ide/openhands/interrupt", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: ctrlCid }),
        });
        ctrlStatus("已发送停止…");
        setTimeout(ctrlRefresh, 800);
      } else if (act === "diff") {
        if (!ctrlTask) { ctrlOut("未绑定工单，无法取 diff"); return; }
        const d = await ctrlApi("/api/ide/openhands/diff", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ task: ctrlTask }),
        });
        if (!d.has_changes) { ctrlOut("无未提交改动"); return; }
        ctrlOut((d.files || []).map(function (f) { return "• " + f; }).join("\n") +
          "\n\n" + (d.diff || "（无 diff）"));
      } else if (act === "approve" || act === "reject") {
        await ctrlApi("/api/ide/openhands/respond", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: ctrlCid, accept: act === "approve" }),
        });
        ctrlStatus(act === "approve" ? "已批准，继续…" : "已拒绝");
        const wrap = ctrlEl("a0-ctrl-pending");
        if (wrap) wrap.style.display = "none";
        setTimeout(ctrlRefresh, 800);
      } else if (act === "ide") {
        // v6：不再直连 8103（无认证旁路）；走同源 /lde 或 8734 登录入口
        const ideUrl = (location.port === "8787")
          ? "http://127.0.0.1:8734/lde/rw"
          : location.origin + "/lde/rw";
        window.open(ideUrl, "_blank");
      } else if (act === "close") {
        ctrlStopPolling();
        const card = ctrlEl("a0-ctrl");
        if (card) card.remove();
        ctrlCid = ""; ctrlTask = "";
      }
    } catch (e) {
      ctrlStatus("操作失败：" + (e && e.message ? e.message : e));
    }
  }

  function openControl(cid, task) {
    ctrlCid = String(cid || "");
    ctrlTask = String(task || "");
    ctrlStopPolling();
    let card = ctrlEl("a0-ctrl");
    if (!card) {
      card = document.createElement("div");
      card.id = "a0-ctrl";
      card.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9998;width:360px;" +
        "background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px;" +
        "box-shadow:0 8px 24px rgba(0,0,0,.45);color:#c9d1d9;font-size:12px";
      card.innerHTML =
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
        '<b>A0 控制面板</b><button id="a0-ctrl-close" data-act="close" style="background:none;border:0;color:#8b949e;font-size:16px;cursor:pointer">×</button></div>' +
        '<div id="a0-ctrl-status" style="color:#8b949e;margin-bottom:6px">运行中…</div>' +
        '<div id="a0-ctrl-pending" style="display:none;background:#2d1b00;border:1px solid #9e6a00;border-radius:6px;padding:6px;margin-bottom:6px">' +
        '<div id="a0-ctrl-pending-text" style="margin-bottom:6px"></div>' +
        '<button data-act="approve">批准</button> <button data-act="reject">拒绝</button></div>' +
        '<div style="display:flex;flex-wrap:wrap;gap:6px">' +
        '<button data-act="status">状态</button><button data-act="stop">停止</button>' +
        '<button data-act="diff">Diff</button><button data-act="ide">打开 IDE</button></div>' +
        '<pre id="a0-ctrl-out" style="display:none;max-height:220px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:8px 0 0"></pre>';
      card.addEventListener("click", function (ev) {
        const b = ev.target.closest ? ev.target.closest("button[data-act]") : null;
        if (b) ctrlAct(b.getAttribute("data-act"));
      });
      document.body.appendChild(card);
    }
    card.style.display = "block";
    ctrlStatus("运行中…（" + ctrlTask + "）");
    ctrlRefresh();
    ctrlTimer = setInterval(ctrlRefresh, 2500);
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
    const direct = isDirectMode();
    btn.textContent = on ? (direct ? "A0 田丰 · 只读" : "A0 田丰 · 开") : "A0 田丰";
    btn.title = on
      ? (direct
        ? "直连只读：8787 直连不触发执行；请走 8734 /hermes/"
        : "主聊天已切到 A0 田丰（Hermes 规划 + OH 执行）；点击关闭")
      : "主聊天切到 A0 田丰（不改 Hermes 默认）";
    btn.classList.toggle("a0-readonly", direct);
    btn.style.cssText = "margin-right:6px;padding:4px 8px;border-radius:999px;border:1px solid " +
      (on ? "#c6a15b" : "#555") + ";background:" + (on ? "#3b2f18" : "transparent") +
      ";color:" + (on ? "#ffd98a" : "inherit") + ";font-size:12px;cursor:pointer";
  }

  function ensureRoHint() {
    if (!isDirectMode()) return null;
    if (roHint && roHint.isConnected) return roHint;
    roHint = document.createElement("span");
    roHint.id = "a0-ro-hint";
    roHint.textContent = "直连只读 · 请走 8734 /hermes/";
    roHint.title = "8787 直连不触发 8796 执行；门户同源入口 8734 /hermes/ 才可执行";
    return roHint;
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
    const hint = ensureRoHint();
    if (hint) footer.appendChild(hint);
    loadTasks();
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else build();

  // 测试钩子（WO-LEGION-AUTH-20261031）：浏览器无 module → 零副作用；
  // node 侧供 portal/tests 断言"直连只读不 fetch 8796 / 同源仍可执行"。
  try {
    if (typeof module === "object" && module.exports) {
      module.exports = {
        isDirectMode: isDirectMode,
        readOnlyMessage: READONLY_MSG,
        base: base,
        runA0: runA0,
      };
    }
  } catch (_) {}
})();
