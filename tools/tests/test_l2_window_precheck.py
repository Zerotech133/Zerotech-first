# -*- coding: utf-8 -*-
"""l2_window_precheck 单测（task-14 / WO-L2-PRECHECK-20261104）。

覆盖：A1–A7 各 ready/blocked/unknown 分支、网络 mock、tasks fixture、JSON schema、
rc 语义（全部 ready=0；任一 blocked/unknown=2）、--skip-network、--json/--md/--out、
不泄漏凭据、只读断言（fixture 文件 hash 不变）、helpers。
全部用 tmp_path fixture，不访问真实网络/真实计划任务/真实业务库。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import l2_window_precheck as pre  # noqa: E402

NOW = "2026-11-05 05:30:00"
DEPLOYED_HTML = ("<html><head><title>数据仓驾驶舱 · 零界</title></head>"
                 "<body>deployed-page</body></html>")
CANDIDATE_HTML = ("<html><head><title>数据仓统一入口 · 零界</title></head>"
                  "<body>candidate-page</body></html>")
NGINX_READY = """
server {
    location = /dw { try_files /dw.html =404; }
    location = /bt-jev {
        auth_request /_auth_guard;
        try_files /bt-jev.html =404;
    }
    location ~ ^/api/(?:intel-graph|jev-stack)/ {
        auth_request /_auth_guard;
        proxy_pass http://host.docker.internal:8091;
    }
    # token=SHOULD_NEVER_LEAK token-abc
    location = /_auth_guard { internal; }
}
"""
NGINX_NO_AUTH = """
server {
    location = /bt-jev { try_files /bt-jev.html =404; }
    location ~ ^/api/(?:intel-graph)/ { proxy_pass http://host.docker.internal:8091; }
}
"""
KG_SCRIPT_READY = """
try {
    foreach ($step in $steps) { ... }
    # registry_align chain tail
    $alignScript = 'C:\\项目\\老田的AI助手\\tools\\datawarehouse\\registry_align.py'
    $alignRc = 0
    ('registry_align exit=' + $alignRc + ' ' + (Get-Date -Format s)) | Out-File $log -Append
    ('kg_sync OK ' + (Get-Date -Format s)) | Out-File $log -Append
    exit 0
} catch { exit 1 }
"""
KG_SCRIPT_NO_STEP = """
try {
    foreach ($step in $steps) { ... }
    ('kg_sync OK ' + (Get-Date -Format s)) | Out-File $log -Append
    exit 0
} catch { exit 1 }
"""


def _sha(text_or_bytes) -> str:
    data = (text_or_bytes.encode("utf-8")
            if isinstance(text_or_bytes, str) else text_or_bytes)
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _make_tdx_db(path: Path, latest: str = "2026-11-04") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for table, col in pre.TDX_TABLES:
            conn.execute('CREATE TABLE "%s" (id INTEGER, "%s" TEXT)' % (table, col))
        rows = (("stock_news", "pub_date", 3), ("stock_notice", "notice_date", 2),
                ("stock_report", "pub_date", 1))
        for table, col, n in rows:
            for i in range(n):
                conn.execute('INSERT INTO "%s" (id, "%s") VALUES (?,?)'
                             % (table, col), (i, latest))
        conn.commit()
    finally:
        conn.close()


def _make_repo(tmp_path: Path, *, nginx: str = NGINX_READY,
               kg_script: str = KG_SCRIPT_READY,
               tdx_latest: str = "2026-11-04",
               with_register_script: bool = True,
               with_jev_stack_handler: bool = True) -> Path:
    repo = tmp_path / "aios"
    _write(repo / "portal" / "nginx.conf", nginx)
    _write(repo / "portal" / "portal_api.py",
           'elif path == "/intel-dossier":\n    pass\n')
    route = ('_GET_EXACT_1 = {\n'
             '    "/api/health": "_api_health",\n'
             '    "/api/jev-stack": "_api_jev_stack",\n'
             '}\n')
    _write(repo / "portal" / "route_table.py", route)
    handler = ("def _api_jev_stack(self):\n    pass\n"
               if with_jev_stack_handler else "# moved elsewhere\n")
    _write(repo / "portal" / "api_health_portal.py", handler)
    _write(repo / "portal" / "dw.html", DEPLOYED_HTML)
    _write(repo / "portal" / "static" / "dw.html", CANDIDATE_HTML)
    _write(repo / "tools" / "datawarehouse" / "registry_align.py", "# tool\n")
    _write(repo / "tools" / "jev_queue_refresh.py", "# tool\n")
    _write(repo / "tools" / "jev_calib_runner.py", "# tool\n")
    if with_register_script:
        _write(repo / "ops" / "register_jev_tasks_20261105.ps1", "# preset\n")
    _write(repo / "docs" / "ops" / "2026-11-03_JEV复核与校准L2申请单_v1.md",
           "# L2 packet\n")
    (repo / "news_collector").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(repo / "news_collector" / "news.db")
    conn.close()
    (repo / "agent_platform" / "data" / "jev_audit").mkdir(
        parents=True, exist_ok=True)
    conn = sqlite3.connect(
        repo / "agent_platform" / "data" / "jev_audit" / "jev_audit.db")
    conn.close()
    _write(repo / "quant_backtest" / "start_kg_sync.ps1", kg_script)
    _make_tdx_db(repo / "quant_backtest" / "data" / "tdx" / "tdx_info.db",
                 latest=tdx_latest)
    return repo


def _base_argv(repo: Path, *extra) -> list[str]:
    return ["--repo-root", str(repo), "--now", NOW,
            "--tdx-max-stale-days", "1",
            "--kg-log", str(repo / "no_kg_log.log")] + list(extra)


def _fake_http(monkeypatch, *, health_8091=None, dw=None, health_8787=None):
    default = {"status": 200, "ok": True, "healthy": None, "body_len": 10,
               "body_sha256": _sha("x"), "error": ""}

    def fake(url, timeout=5.0):
        result = dict(default)
        result["url"] = url
        if "8091" in url and health_8091 is not None:
            result.update(health_8091)
        elif "8787" in url and health_8787 is not None:
            result.update(health_8787)
        elif "/dw.html" in url and dw is not None:
            result.update(dw)
        return result

    monkeypatch.setattr(pre, "http_get", fake)


def _fake_process(monkeypatch, repo: Path, *, pid: int = 4321,
                  created_offset: float = 60.0, created_ts=None):
    code_ts = (repo / "portal" / "portal_api.py").stat().st_mtime
    ts = code_ts + created_offset if created_ts is None else created_ts
    monkeypatch.setattr(pre, "listener_pid",
                        lambda port, timeout=10.0: {"port": port, "pid": pid,
                                                    "listening": True,
                                                    "error": ""})
    monkeypatch.setattr(pre, "process_info",
                        lambda p, timeout=10.0: {
                            "pid": p, "available": True, "name": "python",
                            "created": "2026-11-04 17:10:00",
                            "created_ts": ts, "stderr": ""})


def _tasks_fixture(tmp_path: Path, registered: bool = False) -> Path:
    path = tmp_path / "tasks_fixture.json"
    path.write_text(json.dumps({
        "\\JevQueueRefresh": {"state": "registered" if registered else "not_registered",
                              "next_run": "2026-11-05 08:10:00" if registered else "",
                              "status": "Ready" if registered else ""},
        "\\JevCalibDaily": {"state": "registered" if registered else "not_registered",
                            "next_run": "2026-11-05 07:50:00" if registered else "",
                            "status": "Ready" if registered else ""},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _run_json(capsys, argv):
    rc = pre.main(argv)
    out = capsys.readouterr()
    return rc, out


def _status(rep: dict, ident: str) -> str:
    return next(c["status"] for c in rep["checks"] if c["id"] == ident)


def _env_ready(tmp_path, monkeypatch, *, nginx=NGINX_READY,
               kg_script=KG_SCRIPT_READY, tdx_latest="2026-11-04",
               with_register_script=True, with_handler=True,
               registered=False):
    repo = _make_repo(tmp_path, nginx=nginx, kg_script=kg_script,
                      tdx_latest=tdx_latest,
                      with_register_script=with_register_script,
                      with_jev_stack_handler=with_handler)
    _fake_http(monkeypatch, dw={"status": 200, "ok": True,
                                "body_len": len(DEPLOYED_HTML),
                                "body_sha256": _sha(DEPLOYED_HTML)})
    _fake_process(monkeypatch, repo)
    fixture = _tasks_fixture(tmp_path, registered=registered)
    return repo, _base_argv(repo, "--tasks-fixture", str(fixture))


# --------------------------------------------------------------------------- #
# 1) 全 ready / JSON schema / rc=0
# --------------------------------------------------------------------------- #
def test_all_ready_rc0(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 0, out.err
    assert rep["overall"] == "ready"
    assert rep["rc"] == 0
    assert rep["read_only"] is True
    assert rep["summary"] == {"ready": 7, "blocked": 0, "unknown": 0,
                              "total": 7}
    assert [c["id"] for c in rep["checks"]] == ["A1", "A2", "A3", "A4",
                                                "A5", "A6", "A7"]
    for check in rep["checks"]:
        assert set(check) >= {"id", "title", "status", "reason", "evidence",
                              "notes"}
        assert check["status"] in ("ready", "blocked", "unknown")
    assert _status(rep, "A1") == "ready"
    assert _status(rep, "A2") == "ready"
    assert _status(rep, "A3") == "ready"
    assert _status(rep, "A4") == "ready"
    assert _status(rep, "A5") == "ready"
    assert _status(rep, "A6") == "ready"
    assert _status(rep, "A7") == "ready"
    assert rep["config"]["repo_root"] == str(repo)


# --------------------------------------------------------------------------- #
# 2) A1 分支
# --------------------------------------------------------------------------- #
def test_a1_blocked_old_process(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    _fake_http(monkeypatch)
    _fake_process(monkeypatch, repo, created_offset=-600.0)
    fixture = _tasks_fixture(tmp_path)
    rc, out = _run_json(capsys, _base_argv(
        repo, "--tasks-fixture", str(fixture), "--json"))
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A1") == "blocked"
    assert rep["checks"][0]["evidence"]["code_fresh"] is False


def test_a1_http_fail_blocked(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    _fake_http(monkeypatch, health_8091={"status": 500, "ok": False,
                                         "healthy": None, "error": "HTTP 500"})
    _fake_process(monkeypatch, repo)
    fixture = _tasks_fixture(tmp_path)
    rc, out = _run_json(capsys, _base_argv(
        repo, "--tasks-fixture", str(fixture), "--json"))
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A1") == "blocked"
    assert "500" in rep["checks"][0]["reason"]


# --------------------------------------------------------------------------- #
# 3) A2 分支
# --------------------------------------------------------------------------- #
def test_a2_already_swapped_ready(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    _fake_http(monkeypatch, dw={"status": 200, "ok": True,
                                "body_len": len(CANDIDATE_HTML),
                                "body_sha256": _sha(CANDIDATE_HTML)})
    _fake_process(monkeypatch, repo)
    fixture = _tasks_fixture(tmp_path)
    rc, out = _run_json(capsys, _base_argv(
        repo, "--tasks-fixture", str(fixture), "--json"))
    rep = json.loads(out.out)
    assert rc == 0
    assert _status(rep, "A2") == "ready"
    assert "no-op" in rep["checks"][1]["reason"]


def test_a2_served_drift_blocked(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    _fake_http(monkeypatch, dw={"status": 200, "ok": True, "body_len": 5,
                                "body_sha256": _sha("drift")})
    _fake_process(monkeypatch, repo)
    fixture = _tasks_fixture(tmp_path)
    rc, out = _run_json(capsys, _base_argv(
        repo, "--tasks-fixture", str(fixture), "--json"))
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A2") == "blocked"
    assert "bind mount" in rep["checks"][1]["reason"]


def test_a2_candidate_missing_blocked(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    (repo / "portal" / "static" / "dw.html").unlink()
    _fake_http(monkeypatch)
    _fake_process(monkeypatch, repo)
    fixture = _tasks_fixture(tmp_path)
    rc, out = _run_json(capsys, _base_argv(
        repo, "--tasks-fixture", str(fixture), "--json"))
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A2") == "blocked"
    assert "候选新页" in rep["checks"][1]["reason"]


# --------------------------------------------------------------------------- #
# 4) A3 分支
# --------------------------------------------------------------------------- #
def test_a3_auth_unknown(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    _fake_http(monkeypatch,
               dw={"status": 200, "ok": True, "body_len": len(DEPLOYED_HTML),
                   "body_sha256": _sha(DEPLOYED_HTML)},
               health_8787={"status": 401, "ok": False, "healthy": None,
                            "error": "HTTP 401"})
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert rep["overall"] == "unknown"
    assert _status(rep, "A3") == "unknown"


# --------------------------------------------------------------------------- #
# 5) A4 分支
# --------------------------------------------------------------------------- #
def test_a4_stale_blocked(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch, tdx_latest="2026-10-30")
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A4") == "blocked"
    a4 = next(c for c in rep["checks"] if c["id"] == "A4")
    assert a4["evidence"]["stale_days"] == 6
    assert "stale 6 天" in a4["reason"]


def test_a4_missing_db_blocked(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    (repo / "quant_backtest" / "data" / "tdx" / "tdx_info.db").unlink()
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A4") == "blocked"
    assert "TDX 库不存在" in rep["checks"][3]["reason"]


# --------------------------------------------------------------------------- #
# 6) A5 分支
# --------------------------------------------------------------------------- #
def test_a5_missing_prereq_blocked(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch, with_register_script=False)
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A5") == "blocked"
    assert "注册前置缺失" in rep["checks"][4]["reason"]


def test_a5_already_registered_ready(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch, registered=True)
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 0
    assert _status(rep, "A5") == "ready"
    assert "已在册" in rep["checks"][4]["reason"]


def test_a5_query_unknown(tmp_path, monkeypatch, capsys):
    repo = _make_repo(tmp_path)
    _fake_http(monkeypatch)
    _fake_process(monkeypatch, repo)
    monkeypatch.setattr(pre, "query_task",
                        lambda name, timeout=10.0: {
                            "name": name, "state": "unknown", "rc": -1,
                            "error": "schtasks boom", "next_run": "",
                            "task_status": ""})
    rc, out = _run_json(capsys, _base_argv(repo, "--json"))
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A5") == "unknown"


def test_tasks_fixture_invalid_unknown(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    bad = tmp_path / "bad_fixture.json"
    bad.write_text("not-json", encoding="utf-8")
    rc, out = _run_json(capsys, _base_argv(
        repo, "--tasks-fixture", str(bad), "--json"))
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A5") == "unknown"


# --------------------------------------------------------------------------- #
# 7) A6 / A7 分支
# --------------------------------------------------------------------------- #
def test_a6_blocked_missing_auth(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch, nginx=NGINX_NO_AUTH)
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A6") == "blocked"
    a6 = next(c for c in rep["checks"] if c["id"] == "A6")
    assert a6["evidence"]["bt_jev"]["auth_request"] is False
    assert a6["evidence"]["intel_graph"]["auth_request"] is False
    assert a6["evidence"]["handlers"]["intel_dossier"]["found"] is True


def test_a6_handler_missing_blocked(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch, with_handler=False)
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A6") == "blocked"
    assert "jev-stack" in rep["checks"][5]["reason"]


def test_a7_blocked_missing_step(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch, kg_script=KG_SCRIPT_NO_STEP)
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A7") == "blocked"
    assert "registry_align" in rep["checks"][6]["reason"]


def test_a7_blocked_missing_tool(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    (repo / "tools" / "datawarehouse" / "registry_align.py").unlink()
    rc, out = _run_json(capsys, argv + ["--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert _status(rep, "A7") == "blocked"
    assert "工具缺失" in rep["checks"][6]["reason"]


# --------------------------------------------------------------------------- #
# 8) --skip-network / 输出模式 / 不泄漏凭据 / 只读
# --------------------------------------------------------------------------- #
def test_skip_network_unknown_rc2(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    rc, out = _run_json(capsys, argv + ["--skip-network", "--json"])
    rep = json.loads(out.out)
    assert rc == 2
    assert rep["network_checked"] is False
    for ident in ("A1", "A2", "A3"):
        assert _status(rep, ident) == "unknown"
    for ident in ("A4", "A5", "A6", "A7"):
        assert _status(rep, ident) == "ready"


def test_md_and_out_file(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    out_file = tmp_path / "precheck.md"
    rc = pre.main(argv + ["--md", "--out", str(out_file)])
    out = capsys.readouterr().out
    assert rc == 0
    text = out_file.read_text(encoding="utf-8")
    assert text == out
    assert "# 11-05 L2 窗口只读预检" in text
    assert "## 一、逐项结果" in text
    assert "| A1 |" in text and "| A7 |" in text
    assert not list(tmp_path.glob("*.tmp"))


def test_json_md_conflict(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    rc = pre.main(argv + ["--json", "--md"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "二选一" in err


def test_no_secret_leak(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    # nginx fixture 里写了 token=SHOULD_NEVER_LEAK；工具只记录指令/行号，不得回显
    rc, out = _run_json(capsys, argv + ["--json"])
    assert rc == 0
    assert "SHOULD_NEVER_LEAK" not in out.out
    rc2 = pre.main(argv)
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "SHOULD_NEVER_LEAK" not in out2


def test_read_only_fixture_hash_unchanged(tmp_path, monkeypatch, capsys):
    repo, argv = _env_ready(tmp_path, monkeypatch)
    watched = [repo / "portal" / "nginx.conf",
               repo / "portal" / "dw.html",
               repo / "portal" / "static" / "dw.html",
               repo / "quant_backtest" / "data" / "tdx" / "tdx_info.db",
               repo / "quant_backtest" / "start_kg_sync.ps1"]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    rc, _out = _run_json(capsys, argv + ["--json"])
    assert rc == 0
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in watched}
    assert before == after


# --------------------------------------------------------------------------- #
# 9) helpers
# --------------------------------------------------------------------------- #
def test_helpers_block_parse():
    text = "server {\n location = /bt-jev {\n  auth_request /_auth_guard;\n }\n}\n"
    block = pre.find_location_block(text, "/bt-jev")
    assert block["found"] is True
    assert "auth_request" in block["block"]
    assert block["line"] == 2
    assert pre.find_location_block(text, "/nope")["found"] is False


def test_helpers_parse_day_and_meta(tmp_path):
    assert pre.parse_day("2026-11-04") is not None
    assert pre.parse_day("20261104") is not None
    assert pre.parse_day("bad") is None
    html = _write(tmp_path / "a.html", "<html><title>T0</title></html>")
    meta = pre.file_meta(html, with_title=True)
    assert meta["exists"] is True
    assert meta["title"] == "T0"
    assert meta["sha256"] == _sha("<html><title>T0</title></html>")
    missing = pre.file_meta(tmp_path / "nope.html")
    assert missing["exists"] is False
