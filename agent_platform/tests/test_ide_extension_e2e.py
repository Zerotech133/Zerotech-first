# -*- coding: utf-8 -*-
"""P0-3/P0-4 浏览器 e2e（静态桩 + headless Edge，不触生产）。

覆盖：
- A0 控制面板出现、状态轮询、pending 展示、批准/停止/diff 调用；
- task picker 标签 [复用已有]/[将新建 worktree]；
- 无 worktree 工单二次确认（取消不编排、确认才编排）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
A0 = ROOT / "Hermes" / "extensions" / "legion-ide" / "a0-mainchat.js"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>e2e</title></head>
<body>
<div id="composerBox"><div class="composer-footer"><div class="composer-left"></div></div></div>
<textarea id="msg"></textarea><button id="btnSend">Send</button>
<pre id="e2e">RUNNING</pre>
<script>
window.__calls = [];
window.__confirmMsgs = [];
window.__confirmReturn = true;
window.S = { messages: [] };
window.renderMessages = function () {};
window.updateSendBtn = function () {};
window.confirm = function (m) { window.__confirmMsgs.push(String(m)); return window.__confirmReturn !== false; };
window.fetch = function (url, opts) {
  window.__calls.push({ url: String(url), opts: opts || {} });
  function j(obj) { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(obj); } }); }
  var u = String(url);
  if (u.indexOf("/api/ide/tasks") >= 0) return j({ ok: true, tasks: [
    { task: "T-OK", status: "doing", desc: "existing", exists: true },
    { task: "T-NEW", status: "review", desc: "new", exists: false }
  ]});
  if (u.indexOf("/api/ide/orchestrate") >= 0) return j({ ok: true, mode: "executed",
    conversation_id: "cid-e2e", task: "T-OK", plan: "plan-e2e", run_id: "run-e2e" });
  if (u.indexOf("/api/ide/openhands/status") >= 0) return j({ ok: true,
    status: "waiting_for_confirmation", pending_action: "ls -la", events: [{ kind: "x" }], terminal: false });
  if (u.indexOf("/api/ide/openhands/diff") >= 0) return j({ ok: true, has_changes: true,
    files: ["a.py"], diff: "+hello-e2e" });
  if (u.indexOf("/api/ide/openhands/respond") >= 0 || u.indexOf("/api/ide/openhands/interrupt") >= 0)
    return j({ ok: true });
  return j({ ok: true });
};
</script>
<script>
__A0_SCRIPT__
</script>
<script>
function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
async function run() {
  var out = [];
  function ok(c, m) { out.push((c ? "PASS " : "FAIL ") + m); }
  try {
    await sleep(250);
    var btn = document.getElementById("a0-main-btn");
    ok(!!btn, "A0 button exists");
    btn.click();
    await sleep(250);
    var sel = document.getElementById("a0-main-task");
    ok(!!sel && sel.options.length === 3, "task selector loaded");
    var labels = Array.prototype.map.call(sel.options, function (o) { return o.textContent; }).join("|");
    ok(labels.indexOf("复用已有") >= 0 && labels.indexOf("将新建 worktree") >= 0, "picker labels");
    // 无 worktree 二次确认：取消
    sel.value = "T-NEW";
    document.getElementById("msg").value = "do it";
    window.__confirmReturn = false;
    document.getElementById("btnSend").click();
    await sleep(250);
    var orch = window.__calls.filter(function (c) { return c.url.indexOf("/api/ide/orchestrate") >= 0; }).length;
    ok(orch === 0 && window.__confirmMsgs.length >= 1, "non-existing worktree confirm(cancel)");
    // 确认 -> 编排 + 控制面板
    window.__confirmReturn = true;
    document.getElementById("msg").value = "do it";
    document.getElementById("btnSend").click();
    await sleep(700);
    orch = window.__calls.filter(function (c) { return c.url.indexOf("/api/ide/orchestrate") >= 0; }).length;
    ok(orch >= 1, "orchestrate after confirm");
    var card = document.getElementById("a0-ctrl");
    ok(!!card, "control panel appears");
    var st = document.getElementById("a0-ctrl-status");
    ok(!!st && /事件/.test(st.textContent), "status shows events");
    var pending = document.getElementById("a0-ctrl-pending");
    ok(!!pending && pending.style.display !== "none", "pending visible");
    document.querySelector('#a0-ctrl button[data-act="approve"]').click();
    await sleep(300);
    ok(window.__calls.some(function (c) { return c.url.indexOf("/respond") >= 0; }), "approve calls respond");
    document.querySelector('#a0-ctrl button[data-act="diff"]').click();
    await sleep(300);
    var outEl = document.getElementById("a0-ctrl-out");
    ok(!!outEl && outEl.textContent.indexOf("+hello-e2e") >= 0, "diff rendered");
    document.querySelector('#a0-ctrl button[data-act="stop"]').click();
    await sleep(250);
    ok(window.__calls.some(function (c) { return c.url.indexOf("/interrupt") >= 0; }), "stop calls interrupt");
  } catch (e) {
    out.push("FAIL exception " + e);
  }
  document.getElementById("e2e").textContent =
    "E2E RESULT: " + (out.some(function (x) { return x.indexOf("FAIL") === 0; }) ? "FAIL" : "PASS") +
    "\\n" + out.join("\\n");
}
run();
</script>
</body></html>
"""


def _edge():
    if os.environ.get("E2E_SKIP") == "1":
        return None
    return EDGE if EDGE.is_file() else None


def test_p03_p04_browser_e2e(tmp_path):
    edge = _edge()
    if not edge:
        pytest.skip("Edge/headless e2e disabled or missing")
    if not A0.is_file():
        pytest.skip("a0-mainchat.js missing")
    html = HARNESS.replace("__A0_SCRIPT__", A0.read_text(encoding="utf-8"))
    page = tmp_path / "harness.html"
    page.write_text(html, encoding="utf-8")
    profile = tmp_path / "edge-profile"
    cmd = [str(edge), "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check", "--user-data-dir=" + str(profile),
           "--virtual-time-budget=15000", "--dump-dom", page.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    dom = r.stdout or ""
    assert "E2E RESULT: PASS" in dom, dom[-3000:]
    for line in ("PASS A0 button exists", "PASS picker labels", "PASS orchestrate after confirm",
                 "PASS control panel appears", "PASS approve calls respond",
                 "PASS diff rendered", "PASS stop calls interrupt"):
        assert line in dom, "missing: %s\n%s" % (line, dom[-3000:])
