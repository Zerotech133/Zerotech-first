# -*- coding: utf-8 -*-
"""trust_chain_kpi：M1 信任链 KPI 只读工具的离线单测（不依赖生产服务）。

覆盖：
- 每项检查的 pass / fail / unknown 三态；
- 超时/不可达 fail-soft（HTTP 探针异常 → unknown，不阻塞其它检查）；
- 未知检查异常 → unknown（不炸整体）；
- 总体判定 fail > unknown > pass 的优先级与退出码；
- --json / --markdown 渲染与 JSON 可序列化；
- M4 只读计数（不代签）与 patchset evidence HMAC 校验。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import importlib.util

TOOL = Path(__file__).resolve().parents[1] / "trust_chain_kpi.py"
AUTH_CHECKER = Path(__file__).resolve().parents[1] / "check_auth_surfaces.py"
FIXED_NOW = dt.datetime(2026, 10, 31, 12, 0, tzinfo=dt.timezone.utc)


def _load():
    name = "trust_chain_kpi_under_test"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass + PEP 563 需要模块已注册
    spec.loader.exec_module(mod)
    return mod


def _ctx(mod, tmp_path, **overrides):
    data = {
        "repo": tmp_path,
        "timeout": 0.5,
        "lde_base": "http://127.0.0.1:8103",
        "hermes_base": "http://127.0.0.1:8787",
        "release_root": tmp_path / "release",
        "evidence_dir": tmp_path / "evidence",
        "cap_key_path": tmp_path / "cap.key",
        "approvals_path": tmp_path / "approvals.jsonl",
        "dispatch_path": tmp_path / "dispatch.jsonl",
        "gate_log_dir": tmp_path / "gate",
        "m4_since": mod.DEFAULT_M4_SINCE,
        "evidence_require_prod": False,
        "evidence_task": (),
        "evidence_run_prefix": (),
        "now": FIXED_NOW,
    }
    data.update(overrides)
    return mod.Ctx(**data)


def _probe(status=None, payload=None, reachable=True, error=""):
    return {"reachable": reachable, "status": status,
            "json": payload, "snippet": json.dumps(payload, ensure_ascii=False) if payload else "",
            "error": error}


UNREACHABLE = _probe(reachable=False, error="ConnectionRefusedError: [WinError 10061] refused")


def _fake_http(monkeypatch, mod, responses):
    calls = []

    def fake(base, method, path, payload=None, timeout=None):
        calls.append({"base": base, "method": method, "path": path, "payload": payload})
        value = responses.get(path)
        if callable(value):
            value = value()
        return value if value is not None else UNREACHABLE

    monkeypatch.setattr(mod, "_http", fake)
    return calls


# ---------------------------------------------------------------------------
# 1) 凭证入口
# ---------------------------------------------------------------------------
def _surface(index):
    return {"id": "s%d" % index, "kind": "session", "name": "surface %d" % index,
            "owner": "owner%d" % index, "entry": "/entry/%d" % index,
            "purpose": "p%d" % index, "source": "src/%d.py" % index}


def _fake_repo_surfaces(tmp_path, count=5, max_surfaces=5):
    checker = tmp_path / "tools" / "check_auth_surfaces.py"
    checker.parent.mkdir(parents=True, exist_ok=True)
    checker.write_text(AUTH_CHECKER.read_text(encoding="utf-8"), encoding="utf-8")
    data = {"max_surfaces": max_surfaces, "decision": "sso_trusted_header",
            "sso": "active_2026-10-31",
            "surfaces": [_surface(i) for i in range(1, count + 1)]}
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "auth_surfaces.json").write_text(
        json.dumps(data), encoding="utf-8")
    return data


def test_auth_surfaces_pass(tmp_path, monkeypatch):
    mod = _load()
    _fake_repo_surfaces(tmp_path)
    result = mod.check_auth_surfaces(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["count"] == 5
    assert result["evidence"]["surfaces"] == ["s1", "s2", "s3", "s4", "s5"]


def test_auth_surfaces_sixth_surface_fails(tmp_path):
    mod = _load()
    _fake_repo_surfaces(tmp_path, count=6, max_surfaces=6)
    result = mod.check_auth_surfaces(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["count"] == 6


def test_auth_surfaces_checker_missing_is_unknown(tmp_path):
    mod = _load()
    result = mod.check_auth_surfaces(_ctx(mod, tmp_path))
    assert result["status"] == "unknown"
    assert "检查器不可用" in result["detail"]


# ---------------------------------------------------------------------------
# 2~6) HTTP 检查
# ---------------------------------------------------------------------------
def test_ide_health_pass(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/health": _probe(200, {"ok": True, "pid": 1,
                                                              "rw_enabled": True})})
    result = mod.check_ide_health_8103(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["status"] == 200
    assert result["evidence"]["pid"] == 1


def test_ide_health_unreachable_is_unknown(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/health": UNREACHABLE})
    result = mod.check_ide_health_8103(_ctx(mod, tmp_path))
    assert result["status"] == "unknown"
    assert "不可达" in result["detail"]


def test_ide_health_500_fails(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/health": _probe(500, {"ok": False})})
    assert mod.check_ide_health_8103(_ctx(mod, tmp_path))["status"] == "fail"


def test_ide_rw_anon_401_pass_and_200_fail(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/rw": _probe(401, {"ok": False,
                                                       "error": "local_token_required"})})
    first = mod.check_ide_rw_anon(_ctx(mod, tmp_path))
    assert first["status"] == "pass"
    assert first["evidence"]["error"] == "local_token_required"
    _fake_http(monkeypatch, mod, {"/rw": _probe(200, {"ok": True})})
    assert mod.check_ide_rw_anon(_ctx(mod, tmp_path))["status"] == "fail"


def test_ide_patchset_no_cap_anonymous_fail_closed(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/ide/patchset/preview":
                                  _probe(403, {"ok": False, "error": "no_session"})})
    result = mod.check_ide_patchset_no_cap(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["fail_closed"] is True
    assert result["evidence"]["cap_required_observed"] is False


def test_ide_patchset_no_cap_records_cap_required(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/ide/patchset/preview":
                                  _probe(403, {"ok": False, "error": "cap_required"})})
    result = mod.check_ide_patchset_no_cap(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["cap_required_observed"] is True


def test_ide_patchset_no_cap_fail_open_is_fail(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/ide/patchset/preview": _probe(200, {"ok": True})})
    assert mod.check_ide_patchset_no_cap(_ctx(mod, tmp_path))["status"] == "fail"


def test_hermes_sessions_401_pass_and_200_fail(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/sessions": _probe(401, {"error": "Authentication required"})})
    assert mod.check_hermes_sessions_8787(_ctx(mod, tmp_path))["status"] == "pass"
    _fake_http(monkeypatch, mod, {"/api/sessions": _probe(200, {"sessions": []})})
    result = mod.check_hermes_sessions_8787(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert "SSO 未生效" in result["detail"]


def test_ide_version_all_match_and_mismatch(tmp_path, monkeypatch):
    mod = _load()
    _fake_http(monkeypatch, mod, {"/api/ide/version":
                                  _probe(200, {"ok": True, "version": "v1", "all_match": True})})
    assert mod.check_ide_version(_ctx(mod, tmp_path))["status"] == "pass"
    _fake_http(monkeypatch, mod, {"/api/ide/version":
                                  _probe(200, {"ok": True, "version": "v1", "all_match": False})})
    assert mod.check_ide_version(_ctx(mod, tmp_path))["status"] == "fail"


def _serve_once(handler_fn):
    """起一个仅监听 127.0.0.1 的临时 HTTP 服务（测试 HTTP 探针实现本身）。"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server 约定
            status, payload = handler_fn(self.path)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # noqa: A003 - 静音
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_http_probe_returns_exact_status_and_closes(tmp_path):
    mod = _load()

    def handler(path):
        if path == "/api/health":
            return 200, {"ok": True}
        if path == "/rw":
            return 401, {"ok": False, "error": "local_token_required"}
        return 404, {"ok": False}

    server, thread = _serve_once(handler)
    try:
        base = "http://127.0.0.1:%d" % server.server_address[1]
        health = mod._http(base, "GET", "/api/health", timeout=2)
        assert health["reachable"] is True
        assert health["status"] == 200
        assert health["json"] == {"ok": True}
        rw = mod._http(base, "GET", "/rw", timeout=2)
        assert rw["status"] == 401
        assert rw["json"]["error"] == "local_token_required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_probe_unreachable_is_fail_soft(tmp_path):
    mod = _load()
    probe = mod._http("http://127.0.0.1:1", "GET", "/api/health", timeout=0.2)
    assert probe["reachable"] is False
    assert probe["status"] is None
    assert probe["error"]


# ---------------------------------------------------------------------------
# 7) release artifact drift
# ---------------------------------------------------------------------------
class _FakeRelease:
    def __init__(self, status):
        self._status = status

    def status(self, _root):
        return self._status


def _release_status(pointer="v1", junction_version="v1", drift=False,
                    exists=True, target_ok=True):
    return {"pointer_version": pointer,
            "junction": {"path": "root/current", "exists": exists,
                         "target": "root/" + junction_version,
                         "target_exists": target_ok, "version": junction_version},
            "drift": drift}


def test_release_drift_ok(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_load_release_module",
                        lambda ctx: (_FakeRelease(_release_status()), ""))
    result = mod.check_release_artifact_drift(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["drift"] is False


def test_release_drift_detected(tmp_path, monkeypatch):
    mod = _load()
    st = _release_status(pointer="v2", junction_version="v1", drift=True)
    monkeypatch.setattr(mod, "_load_release_module", lambda ctx: (_FakeRelease(st), ""))
    result = mod.check_release_artifact_drift(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["drift"] is True


def test_release_module_missing_is_unknown(tmp_path, monkeypatch):
    mod = _load()
    monkeypatch.setattr(mod, "_load_release_module", lambda ctx: (None, "import boom"))
    result = mod.check_release_artifact_drift(_ctx(mod, tmp_path))
    assert result["status"] == "unknown"
    assert "import boom" in result["detail"]


# ---------------------------------------------------------------------------
# 8) patchset evidence + HMAC
# ---------------------------------------------------------------------------
def _sign_record(record, key):
    body = json.dumps({k: v for k, v in record.items() if k != "signature"},
                      ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def _evidence_record(task="T-BOOT", run_id="patchset-T-BOOT-abcd1234", agent="LDE"):
    return {"schema": "legion-evidence/v1", "run_id": run_id,
            "kind": "patchset", "task": task, "agent": agent, "mode": "patchset",
            "ok": True, "created_at": "2026-10-31T10:00:00+08:00",
            "finalized_at": "2026-10-31T10:00:00+08:00",
            "events": [{"at": "2026-10-31T10:00:00+08:00",
                        "event": "patchset_confirm", "payload": {"files": []}}]}


def _write_evidence(tmp_path, key, signed=True, mutate=False,
                    name="patchset-T-BOOT-abcd1234.json",
                    task="T-BOOT", run_id=None, agent="LDE"):
    directory = tmp_path / "evidence"
    directory.mkdir(exist_ok=True)
    record = _evidence_record(task=task, run_id=run_id or Path(name).stem, agent=agent)
    if signed:
        record["signature"] = _sign_record(record, key)
    if mutate:
        record["ok"] = False  # 签名后篡改
    path = directory / name
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def test_patchset_evidence_signed_pass(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    _write_evidence(tmp_path, key)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["signature_verified"] == "true"


def test_patchset_evidence_missing_signature_fails(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    _write_evidence(tmp_path, key, signed=False)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert "signature" in result["detail"]


def test_patchset_evidence_tampered_fails(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    _write_evidence(tmp_path, key, mutate=True)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["signature_verified"] == "false"


def test_patchset_evidence_no_key_still_passes_with_unknown_verify(tmp_path):
    mod = _load()
    key = b"k" * 32
    _write_evidence(tmp_path, key)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["signature_verified"] == "unknown"


def test_patchset_evidence_missing_dir_is_unknown(tmp_path):
    mod = _load()
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "unknown"


def test_patchset_evidence_empty_dir_fails(tmp_path):
    mod = _load()
    (tmp_path / "evidence").mkdir()
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["count"] == 0


def test_patchset_evidence_picks_latest(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    older = _write_evidence(tmp_path, key, name="patchset-old.json")
    newer = _write_evidence(tmp_path, key, name="patchset-new.json")
    os.utime(older, (FIXED_NOW.timestamp() - 7200, FIXED_NOW.timestamp() - 7200))
    os.utime(newer, (FIXED_NOW.timestamp() - 60, FIXED_NOW.timestamp() - 60))
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert Path(result["evidence"]["file"]).name == "patchset-new.json"


# ---------------------------------------------------------------------------
# 9) M4 签字（只读）
# ---------------------------------------------------------------------------
def _m4_dispatch():
    return [
        {"task": "作战屏 v1 M4 质量门验收", "to": "质量工程部/QA1", "status": "accepted"},
        {"task": "作战屏 v1 M4 代码审计", "to": "研究院/GUANYU", "status": "done"},
        {"task": "作战屏 v1 M4 独立复验", "to": "质量工程部/VERIFY1", "status": "accepted"},
        {"task": "M5 无关任务", "to": "质量工程部/QA1", "status": "accepted"},
    ]


def _write_m4(tmp_path, approvals, dispatch):
    (tmp_path / "approvals.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in approvals) + "\n",
        encoding="utf-8")
    (tmp_path / "dispatch.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in dispatch) + "\n",
        encoding="utf-8")


def test_m4_missing_all_fails(tmp_path):
    mod = _load()
    _write_m4(tmp_path, [], [])
    result = mod.check_m4_signoff(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["missing"] == ["QA1", "GUANYU", "VERIFY1", "A0/老田"]
    assert result["evidence"]["approvals_count"] == 0


def test_m4_full_signoff_passes(tmp_path):
    mod = _load()
    approvals = [{"ts": "2026-10-31T08:30:00", "action": "approve",
                  "task": "M4/v1.1 正式验收签字", "operator": "ou_laotian"}]
    _write_m4(tmp_path, approvals, _m4_dispatch())
    result = mod.check_m4_signoff(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["evidence"]["missing"] == []
    assert result["evidence"]["approvals_count"] == 1


def test_m4_old_approval_not_counted(tmp_path):
    mod = _load()
    approvals = [{"ts": "2026-10-30T00:48:38", "action": "approve",
                  "task": "M4/v1.1 验收签字（试点）", "operator": "ou_laotian"}]
    _write_m4(tmp_path, approvals, _m4_dispatch())
    result = mod.check_m4_signoff(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["missing"] == ["A0/老田"]


def test_m4_ledgers_missing_is_unknown(tmp_path):
    mod = _load()
    result = mod.check_m4_signoff(_ctx(mod, tmp_path))
    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# 10) fast gate（advisory）
# ---------------------------------------------------------------------------
def test_fast_gate_pass(tmp_path):
    mod = _load()
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    log = gate_dir / "test_gate_fast_20261031_1200.log"
    log.write_text("suite=fast\nGATE PASS\n", encoding="utf-8")
    os.utime(log, (FIXED_NOW.timestamp() - 600, FIXED_NOW.timestamp() - 600))
    result = mod.check_fast_gate(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    assert result["required"] is False
    assert result["evidence"]["stale"] is False


def test_fast_gate_fail_and_stale(tmp_path):
    mod = _load()
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    log = gate_dir / "gate_after_x.log"
    log.write_text("GATE FAIL\n", encoding="utf-8")
    os.utime(log, (FIXED_NOW.timestamp() - 48 * 3600, FIXED_NOW.timestamp() - 48 * 3600))
    result = mod.check_fast_gate(_ctx(mod, tmp_path))
    assert result["status"] == "fail"
    assert result["evidence"]["stale"] is True


def test_fast_gate_no_logs_is_unknown(tmp_path):
    mod = _load()
    result = mod.check_fast_gate(_ctx(mod, tmp_path))
    assert result["status"] == "unknown"
    assert result["required"] is False


# ---------------------------------------------------------------------------
# 采集聚合 / 判定 / 渲染 / CLI
# ---------------------------------------------------------------------------
def _collect(mod, tmp_path, checks):
    return mod.collect(repo=tmp_path, now=FIXED_NOW, checks=checks,
                       evidence_dir=tmp_path / "evidence",
                       release_root=tmp_path / "release",
                       gate_log_dir=tmp_path / "gate")


def test_check_registry_resolves():
    mod = _load()
    ids = [cid for cid, _fn in mod._CHECKS]
    assert ids == ["auth_surfaces", "ide_health_8103", "ide_rw_anon_8103",
                   "ide_patchset_no_cap_8103", "hermes_sessions_8787",
                   "ide_version_8103", "release_artifact_drift",
                   "patchset_evidence", "m4_signoff", "fast_gate"]
    for _cid, fn_name in mod._CHECKS:
        assert callable(mod._resolve_check(fn_name))


def test_collect_pass(tmp_path):
    mod = _load()
    report = _collect(mod, tmp_path, [
        ("a", lambda ctx: mod._result("a", "A", "pass", "ok")),
        ("b", lambda ctx: mod._result("b", "B", "pass", "ok")),
    ])
    assert report["verdict"] == "pass"
    assert report["passed"] is True
    assert report["counts"] == {"pass": 2, "fail": 0, "unknown": 0,
                                "total": 2, "checks_total": 2}


def test_collect_unknown_only(tmp_path):
    mod = _load()
    report = _collect(mod, tmp_path, [
        ("a", lambda ctx: mod._result("a", "A", "pass", "ok")),
        ("b", lambda ctx: mod._result("b", "B", "unknown", "超时")),
    ])
    assert report["verdict"] == "unknown"
    assert report["unknown"] == ["b"]


def test_collect_fail_beats_unknown(tmp_path):
    mod = _load()
    report = _collect(mod, tmp_path, [
        ("a", lambda ctx: mod._result("a", "A", "fail", "bad")),
        ("b", lambda ctx: mod._result("b", "B", "unknown", "超时")),
    ])
    assert report["verdict"] == "fail"
    assert report["failing"] == ["a"]


def test_collect_advisory_does_not_affect_verdict(tmp_path):
    mod = _load()
    report = _collect(mod, tmp_path, [
        ("a", lambda ctx: mod._result("a", "A", "pass", "ok")),
        ("g", lambda ctx: mod._result("g", "G", "fail", "gate", required=False,
                                      category=mod.CATEGORY_ADVISORY)),
    ])
    assert report["verdict"] == "pass"


def test_collect_fail_soft_on_check_exception(tmp_path):
    mod = _load()

    def boom(_ctx):
        raise RuntimeError("模拟未捕获异常")

    report = _collect(mod, tmp_path, [
        ("ok", lambda ctx: mod._result("ok", "OK", "pass", "ok")),
        ("boom", boom),
    ])
    assert report["verdict"] == "unknown"
    boom_check = [c for c in report["checks"] if c["id"] == "boom"][0]
    assert boom_check["status"] == "unknown"
    assert "fail-soft" in boom_check["detail"]
    assert "模拟未捕获异常" in boom_check["detail"]


def test_report_json_serializable(tmp_path):
    mod = _load()
    report = _collect(mod, tmp_path, [
        ("a", lambda ctx: mod._result("a", "A", "pass", "ok", {"x": 1})),
    ])
    json.dumps(report, ensure_ascii=False)


def test_render_markdown_contains_table_and_escapes(tmp_path):
    mod = _load()
    report = _collect(mod, tmp_path, [
        ("a", lambda ctx: mod._result("a", "A|B", "pass", "x|y")),
        ("b", lambda ctx: mod._result("b", "B", "unknown", "不可达")),
    ])
    text = mod.render_markdown(report)
    assert "# M1 信任链 KPI · 只读快照" in text
    assert "| 1 |" in text
    assert "A\\|B" in text
    assert "x\\|y" in text
    assert "UNKNOWN" in text
    assert "数据源" in text


def test_main_json_output_and_exit_codes(tmp_path, monkeypatch, capsys):
    mod = _load()
    fixed = {"schema": mod.SCHEMA, "verdict": "pass", "counts": {},
             "checks": [], "generated_at": "2026-10-31T12:00:00+08:00"}
    monkeypatch.setattr(mod, "collect", lambda **kwargs: fixed)
    assert mod.main(["--json", "--repo", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "pass"
    fixed["verdict"] = "fail"
    assert mod.main(["--json"]) == 1
    fixed["verdict"] = "unknown"
    assert mod.main(["--json"]) == 2
    capsys.readouterr()


def test_main_markdown_default(tmp_path, monkeypatch, capsys):
    mod = _load()
    fixed = {"schema": mod.SCHEMA, "verdict": "unknown", "counts": {},
             "checks": [], "generated_at": "2026-10-31T12:00:00+08:00"}
    monkeypatch.setattr(mod, "collect", lambda **kwargs: fixed)
    assert mod.main([]) == 2
    captured = capsys.readouterr().out
    assert "# M1 信任链 KPI · 只读快照" in captured
    assert "UNKNOWN" in captured


def test_main_out_writes_only_report_file(tmp_path, monkeypatch, capsys):
    mod = _load()
    fixed = {"schema": mod.SCHEMA, "verdict": "pass", "counts": {},
             "checks": [], "generated_at": "2026-10-31T12:00:00+08:00"}
    monkeypatch.setattr(mod, "collect", lambda **kwargs: fixed)
    out_file = tmp_path / "kpi.json"
    assert mod.main(["--json", "--out", str(out_file)]) == 0
    assert json.loads(out_file.read_text(encoding="utf-8"))["verdict"] == "pass"
    capsys.readouterr()


def test_no_stdlib_network_import_regression():
    """工具必须只依赖标准库（不引 requests/httpx 等，避免生产机缺包）。"""
    mod = _load()
    assert sys.modules.get("requests") is None or "requests" not in TOOL.read_text(encoding="utf-8")
    source = TOOL.read_text(encoding="utf-8")
    for banned in ("import requests", "import httpx", "from requests", "from httpx"):
        assert banned not in source
    assert callable(mod.collect)


# ---------------------------------------------------------------------------
# 证据来源口径：生产样本 / 测试样本 / 混合 / 无符合项（task-9）
# ---------------------------------------------------------------------------
def _mixed_evidence_dir(tmp_path):
    """prod 样本较旧、test 样本较新（模拟并行测试污染生产 evidence 目录）。"""
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    prod = _write_evidence(tmp_path, key, name="patchset-WO-PROD-aaaa1111.json",
                           task="WO-PROD-20261031", run_id="patchset-WO-PROD-aaaa1111",
                           agent="LDE")
    test = _write_evidence(tmp_path, key, name="patchset-T-BOOT-bbbb2222.json",
                           task="T-BOOT", run_id="patchset-T-BOOT-bbbb2222")
    os.utime(prod, (FIXED_NOW.timestamp() - 120, FIXED_NOW.timestamp() - 120))
    os.utime(test, (FIXED_NOW.timestamp() - 10, FIXED_NOW.timestamp() - 10))
    return key, prod, test


def test_evidence_is_prod_heuristic():
    mod = _load()
    assert mod._evidence_is_prod({"task": "WO-PROD-20261031"}) is True
    assert mod._evidence_is_prod({"task": "refactor-IDE"}) is True
    assert mod._evidence_is_prod({"task": "T-BOOT"}) is False
    assert mod._evidence_is_prod({"task": "T-REV"}) is False
    assert mod._evidence_is_prod({"task": "P0-4-LIVE-SMOKE"}) is False
    assert mod._evidence_is_prod({"task": "SOME-E2E"}) is False
    assert mod._evidence_is_prod({"task": "__m1_kpi_readonly_probe__"}) is False
    assert mod._evidence_is_prod({"task": ""}) is False


def test_filter_values_normalization():
    mod = _load()
    assert mod._filter_values(None) == ()
    assert mod._filter_values("") == ()
    assert mod._filter_values("WO-A, WO-B") == ("WO-A", "WO-B")
    assert mod._filter_values(["x", " y "]) == ("x", "y")


def test_patchset_evidence_require_prod_prefers_prod_over_newer_test(tmp_path):
    mod = _load()
    _key, prod, _test = _mixed_evidence_dir(tmp_path)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path, evidence_require_prod=True))
    assert result["status"] == "pass"
    sel = result["evidence"]["selected_evidence"]
    assert sel["task"] == "WO-PROD-20261031"
    assert sel["agent"] == "LDE"
    assert sel["run_id"] == "patchset-WO-PROD-aaaa1111"
    assert sel["is_prod"] is True
    assert Path(sel["file"]).name == prod.name
    # 最新样本（T-BOOT）被记录但未被选中
    assert result["evidence"]["latest_sample"]["task"] == "T-BOOT"
    assert result["evidence"]["matched"] == 1


def test_patchset_evidence_require_prod_only_test_fails(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    _write_evidence(tmp_path, key, name="patchset-T-BOOT-bbbb2222.json",
                    task="T-BOOT", run_id="patchset-T-BOOT-bbbb2222")
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path, evidence_require_prod=True))
    assert result["status"] == "fail"
    assert "无符合筛选条件" in result["detail"]
    assert "要求生产证据但只找到测试/演练样本" in result["detail"]
    assert result["evidence"]["selected_evidence"] is None
    assert result["evidence"]["matched"] == 0


def test_patchset_evidence_require_prod_only_smoke_fails(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    _write_evidence(tmp_path, key, name="patchset-P0-4-LIVE-SMOKE-cccc3333.json",
                    task="P0-4-LIVE-SMOKE", run_id="patchset-P0-4-LIVE-SMOKE-cccc3333")
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path, evidence_require_prod=True))
    assert result["status"] == "fail"


def test_patchset_evidence_default_behavior_compat(tmp_path):
    """不加新参数 = 原有行为：仍取最新，即使它是测试样本。"""
    mod = _load()
    _mixed_evidence_dir(tmp_path)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path))
    assert result["status"] == "pass"
    sel = result["evidence"]["selected_evidence"]
    assert sel["task"] == "T-BOOT"
    assert sel["is_prod"] is False


def test_patchset_evidence_task_filter_selects_older_prod(tmp_path):
    mod = _load()
    _mixed_evidence_dir(tmp_path)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path,
                                              evidence_task=("WO-PROD",)))
    assert result["status"] == "pass"
    assert result["evidence"]["selected_evidence"]["task"] == "WO-PROD-20261031"


def test_patchset_evidence_run_prefix_filter(tmp_path):
    mod = _load()
    _mixed_evidence_dir(tmp_path)
    result = mod.check_patchset_evidence(_ctx(
        mod, tmp_path, evidence_run_prefix=("patchset-WO-PROD",)))
    assert result["status"] == "pass"
    assert result["evidence"]["selected_evidence"]["run_id"] == "patchset-WO-PROD-aaaa1111"


def test_patchset_evidence_filter_no_match_fails(tmp_path):
    mod = _load()
    _mixed_evidence_dir(tmp_path)
    result = mod.check_patchset_evidence(_ctx(mod, tmp_path,
                                              evidence_task=("NOT-EXIST",)))
    assert result["status"] == "fail"
    assert "无符合筛选条件" in result["detail"]
    assert result["evidence"]["latest_sample"]["task"] == "T-BOOT"
    assert result["evidence"]["selected_evidence"] is None


def test_patchset_evidence_mixed_filters_combined_fails(tmp_path):
    mod = _load()
    _mixed_evidence_dir(tmp_path)
    result = mod.check_patchset_evidence(_ctx(
        mod, tmp_path, evidence_require_prod=True,
        evidence_run_prefix=("patchset-T-BOOT",)))
    assert result["status"] == "fail"
    assert result["evidence"]["matched"] == 0


def test_collect_exposes_selected_evidence(tmp_path):
    mod = _load()
    key, prod, _test = _mixed_evidence_dir(tmp_path)
    report = mod.collect(
        repo=tmp_path, now=FIXED_NOW, evidence_dir=tmp_path / "evidence",
        evidence_require_prod=True, cap_key_path=tmp_path / "cap.key",
        checks=[("patchset_evidence", mod.check_patchset_evidence)])
    assert report["verdict"] == "pass"
    assert report["selected_evidence"]["task"] == "WO-PROD-20261031"
    assert report["selected_evidence"]["run_id"] == "patchset-WO-PROD-aaaa1111"
    check = report["checks"][0]
    assert check["evidence"]["filters"]["require_prod"] is True


def test_collect_selected_evidence_none_on_fail(tmp_path):
    mod = _load()
    key = b"k" * 32
    (tmp_path / "cap.key").write_bytes(key)
    _write_evidence(tmp_path, key, name="patchset-T-BOOT-bbbb2222.json")
    report = mod.collect(
        repo=tmp_path, now=FIXED_NOW, evidence_dir=tmp_path / "evidence",
        evidence_require_prod=True, cap_key_path=tmp_path / "cap.key",
        checks=[("patchset_evidence", mod.check_patchset_evidence)])
    assert report["verdict"] == "fail"
    assert report["selected_evidence"] is None


def test_render_markdown_shows_selected_evidence(tmp_path):
    mod = _load()
    _mixed_evidence_dir(tmp_path)
    report = mod.collect(
        repo=tmp_path, now=FIXED_NOW, evidence_dir=tmp_path / "evidence",
        evidence_require_prod=True, cap_key_path=tmp_path / "cap.key",
        checks=[("patchset_evidence", mod.check_patchset_evidence)])
    text = mod.render_markdown(report)
    assert "## patchset 证据（selected_evidence）" in text
    assert "WO-PROD-20261031" in text
    assert "patchset-WO-PROD-aaaa1111" in text


def test_main_passes_evidence_filters(tmp_path, monkeypatch, capsys):
    mod = _load()
    captured = {}

    def fake_collect(**kwargs):
        captured.update(kwargs)
        return {"schema": mod.SCHEMA, "verdict": "pass", "counts": {},
                "checks": [], "selected_evidence": None,
                "generated_at": "2026-10-31T12:00:00+08:00"}

    monkeypatch.setattr(mod, "collect", fake_collect)
    rc = mod.main(["--json", "--evidence-require-prod",
                   "--evidence-task", "WO-A,WO-B",
                   "--evidence-run-prefix", "patchset-WO-"])
    assert rc == 0
    assert captured["evidence_require_prod"] is True
    assert mod._filter_values(captured["evidence_task"]) == ("WO-A", "WO-B")
    assert mod._filter_values(captured["evidence_run_prefix"]) == ("patchset-WO-",)
    capsys.readouterr()


def test_main_evidence_flags_default_off(tmp_path, monkeypatch, capsys):
    mod = _load()
    captured = {}

    def fake_collect(**kwargs):
        captured.update(kwargs)
        return {"schema": mod.SCHEMA, "verdict": "pass", "counts": {},
                "checks": [], "selected_evidence": None,
                "generated_at": "2026-10-31T12:00:00+08:00"}

    monkeypatch.setattr(mod, "collect", fake_collect)
    assert mod.main(["--json"]) == 0
    assert captured["evidence_require_prod"] is False
    assert captured["evidence_task"] is None
    assert captured["evidence_run_prefix"] is None
    capsys.readouterr()
