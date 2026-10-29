# -*- coding: utf-8 -*-
import json
import threading
import time
from pathlib import Path

import sys

PLATFORM = Path(__file__).resolve().parents[1]
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))

from legion_ide import rw_guard as G  # noqa: E402
from legion_ide import writer as W  # noqa: E402
from legion_ide import serve as S  # noqa: E402
from legion_ide import exec_runner as X  # noqa: E402


def _wt(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    wt = repo / ".worktrees" / "K-TEST-1"
    wt.mkdir(parents=True)
    tasks = repo / ".worktrees" / "tasks.json"
    tasks.write_text(json.dumps({"K-TEST-1": {"status": "doing"}}), encoding="utf-8")
    return wt


def test_acquire_lock_is_exclusive(tmp_path):
    wt = _wt(tmp_path)
    barrier = threading.Barrier(8)
    results = []

    def worker(i):
        barrier.wait()
        results.append(G.acquire_lock(wt, "owner-%d" % i))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r.get("ok")]
    assert len(winners) == 1, results
    assert sum(1 for r in results if r.get("error") == "locked") == 7


def test_lock_reuse_and_stale(tmp_path):
    wt = _wt(tmp_path)
    first = G.acquire_lock(wt, "a")
    assert first["ok"]
    again = G.acquire_lock(wt, "a")
    assert again["ok"] and again.get("reused") is True
    # 伪造过期锁：同 owner 可重新抢
    p = wt / ".ide.lock"
    p.write_text(json.dumps({"owner": "old", "ts": time.time() - 9999}), encoding="utf-8")
    got = G.acquire_lock(wt, "b")
    assert got["ok"] and got["lock"]["owner"] == "b"


def test_writer_parent_failure_returns_error(tmp_path):
    wt = _wt(tmp_path)
    (wt / "agent_platform").write_text("not a dir", encoding="utf-8")
    res = W.write_file(wt, "agent_platform/x.py", "print(1)\n")
    assert res["ok"] is False and res["error"] == "write_failed"


def test_exec_results_are_bounded():
    S._EXEC_RESULTS.clear()
    for i in range(120):
        S._remember_exec("r%d" % i, {"ok": True, "ts": time.time()})
    assert len(S._EXEC_RESULTS) <= 100


def test_preview_confirm_and_stale_guard(tmp_path):
    wt = _wt(tmp_path)
    target = wt / "agent_platform" / "x.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old = 1\n", encoding="utf-8")
    preview = W.preview_file(wt, "agent_platform/x.py", "old = 2\n")
    assert preview["ok"] and "+old = 2" in preview["diff"]
    target.write_text("old = 9\n", encoding="utf-8")
    stale = W.write_file(wt, "agent_platform/x.py", "old = 2\n",
                         expected_sha256=preview["old_sha256"])
    assert stale["ok"] is False and stale["error"] == "stale_preview"


def test_exec_reports_failure_semantics(tmp_path):
    wt = _wt(tmp_path)
    bad = wt / "agent_platform" / "bad.py"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("def broken(:\n", encoding="utf-8")
    res = X.run(wt, "python -m py_compile agent_platform/bad.py")
    assert res["ok"] is True and res["exit_code"] != 0 and res["success"] is False
