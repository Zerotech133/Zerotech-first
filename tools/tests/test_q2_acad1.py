# -*- coding: utf-8 -*-
"""ACAD1-PROBE 测试（WO-ACAD1-PROBE-20261104）。

覆盖：
  1. `tools/q2_probe.py` 增 `acad1-legion`：4 个公开 200 端点 + 6 个预期 401
     （auth_required 语义，不判失败）；鉴权门缺失（2xx）/网络失败不假绿；
     不发送 Cookie/Authorization；JSON/Markdown 输出。
  2. `tools/q2_slo_alert.py` 增 acad1 评估：公开端点算样本（窗口不足/无 registry
     目标 → not_measured），6 个 401 端点显式 auth_required/not_measured，
     不伪造 200；鉴权门缺失/探针失败 → alert；数据 SLA 观测独立为 interface_alerts；
     CLI 无 token 泄漏。
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import q2_probe as P  # noqa: E402
import q2_slo_alert as A  # noqa: E402

ACAD1 = "acad1-legion"


# ── 构造器 ────────────────────────────────────────────────────────────
def _fake_get(auth_status=401, public_status=200, latency=10):
    """按 URL 返回：acad1-research/* → auth_status，其余公开端点 → public_status。"""
    def _get(url, timeout=5.0, expected_status=None):
        if "acad1-research" in url:
            status = auth_status
        else:
            status = public_status
        if expected_status is not None:
            ok = status == expected_status
            row = {"ok": ok, "status": status, "latency_ms": latency,
                   "evidence": "{}", "auth_required": True,
                   "expected_status": expected_status}
            if not ok:
                row["error"] = "unexpected_status:%s (expected %s auth_required)" % (
                    status, expected_status)
            return row
        return {"ok": 200 <= status < 300, "status": status,
                "latency_ms": latency, "evidence": "{}"}
    return _get


def _acad1_report(samples=1, public_status=200, auth_status=401, latency=10):
    """q2_probe 形态的 acad1-legion 报告（4 公开 + 6 预期 401）。"""
    checks = []
    for name in A.ACAD1_PUBLIC_TARGETS:
        statuses = [public_status] * samples
        checks.append({
            "name": name, "url": "http://127.0.0.1/x", "ok": public_status == 200,
            "status": statuses[0], "statuses": statuses, "samples": samples,
            "latencies_ms": [latency] * samples, "latency_ms": latency,
            "latency_ms_min": latency, "latency_ms_median": latency,
            "latency_ms_max": latency, "evidence": "{}",
            "auth_required": False, "expected_status": None,
        })
    for name in A.ACAD1_AUTH_TARGETS:
        statuses = [auth_status] * samples
        checks.append({
            "name": name, "url": "http://127.0.0.1/x", "ok": auth_status == 401,
            "status": statuses[0], "statuses": statuses, "samples": samples,
            "latencies_ms": [latency] * samples, "latency_ms": latency,
            "latency_ms_min": latency, "latency_ms_median": latency,
            "latency_ms_max": latency, "evidence": "{}",
            "auth_required": True, "expected_status": 401,
        })
    return {"schema": P.SCHEMA, "composition": ACAD1,
            "checked_at": "2026-11-04T06:00:00+08:00",
            "samples_per_target": samples,
            "public_targets": len(A.ACAD1_PUBLIC_TARGETS),
            "auth_required_targets": len(A.ACAD1_AUTH_TARGETS),
            "ok": public_status == 200 and auth_status == 401, "checks": checks}


# ══ 1. q2_probe：acad1-legion 4 公开 + 6 预期 401 ═══════════════════════
def test_probe_acad1_targets_structure():
    assert [n for n, _ in P.TARGETS[ACAD1]] == [
        "pool", "zhice-health", "shuijing-page", "hermes-acad1-comparison"]
    assert [n for n, _ in P.AUTH_TARGETS[ACAD1]] == A.ACAD1_AUTH_TARGETS
    urls = {n: u for n, u in P.TARGETS[ACAD1]}
    assert urls["pool"] == "http://127.0.0.1:8091/api/acad1/pool"
    assert urls["zhice-health"] == "http://127.0.0.1:8105/api/v1/health"
    assert urls["shuijing-page"] == "http://127.0.0.1:8734/academy/shuijing"
    assert urls["hermes-acad1-comparison"].endswith(
        "/quant-api/api/v1/hermes/acad1-comparison")
    for name, url in P.AUTH_TARGETS[ACAD1]:
        assert url.startswith("http://127.0.0.1:8734/quant-api/api/v1/acad1-research/")
        assert name.startswith("acad1-research-")


def test_probe_acad1_auth_required_is_expected_not_failure(monkeypatch):
    monkeypatch.setattr(P, "_get", _fake_get())
    report = P.probe(ACAD1)
    assert report["ok"] is True
    assert report["public_targets"] == 4
    assert report["auth_required_targets"] == 6
    public = [c for c in report["checks"] if not c["auth_required"]]
    auth = [c for c in report["checks"] if c["auth_required"]]
    assert all(c["status"] == 200 and c["ok"] is True for c in public)
    assert all(c["status"] == 401 and c["ok"] is True for c in auth)
    assert all(c["expected_status"] == 401 for c in auth)


def test_probe_acad1_auth_gate_missing_not_green(monkeypatch):
    """未登录端点若返回 2xx（鉴权门缺失/回退）必须 ok=False，不得当绿。"""
    monkeypatch.setattr(P, "_get", _fake_get(auth_status=200))
    report = P.probe(ACAD1)
    assert report["ok"] is False
    auth = [c for c in report["checks"] if c["auth_required"]]
    assert all(c["ok"] is False for c in auth)
    assert all(any("unexpected_status" in e for e in c["errors"]) for c in auth)


def test_probe_acad1_public_down_not_green(monkeypatch):
    monkeypatch.setattr(P, "_get", _fake_get(public_status=0))
    report = P.probe(ACAD1, samples=2)
    assert report["ok"] is False
    public = [c for c in report["checks"] if not c["auth_required"]]
    assert all(c["ok"] is False for c in public)
    assert all(c["samples"] == 2 for c in report["checks"])


def test_probe_get_no_auth_headers_sent(monkeypatch):
    """探测请求不得携带 Cookie/Authorization（无凭据、无 token 泄漏）。"""
    captured = {}

    class _Resp:
        status = 200

        def read(self, n=2048):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=5):
        captured["headers"] = dict(req.headers)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    row = P._get("http://127.0.0.1:8105/api/v1/health", expected_status=None)
    assert row["ok"] is True and row["status"] == 200
    lowered = {k.lower() for k in captured["headers"]}
    assert "authorization" not in lowered
    assert "cookie" not in lowered
    assert lowered == {"accept"}


def test_probe_get_http_error_401_is_evidence(monkeypatch):
    """HTTPError(401) 必须按状态码记录（预期 401=ok；非预期 401=not ok）。"""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=5):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"detail":"missing token"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    expected = P._get("http://127.0.0.1:8734/quant-api/api/v1/acad1-research/pool",
                      expected_status=401)
    assert expected["ok"] is True and expected["status"] == 401
    assert expected["auth_required"] is True
    unexpected = P._get("http://127.0.0.1:8091/api/acad1/pool", expected_status=None)
    assert unexpected["ok"] is False and unexpected["status"] == 401


def test_probe_acad1_cli_json_and_markdown(monkeypatch, capsys):
    monkeypatch.setattr(P, "_get", _fake_get())
    assert P.main(["--target", "acad1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    row = payload["results"][0]
    assert row["composition"] == ACAD1
    assert row["ok"] is True
    assert len(row["checks"]) == 10
    assert sum(1 for c in row["checks"] if c["auth_required"]) == 6

    assert P.main(["--target", "acad1", "--markdown"]) == 0
    md = capsys.readouterr().out
    assert "| 组合 | 目标 |" in md and "鉴权" in md
    assert "auth_required" in md and ACAD1 in md


# ══ 2. q2_slo_alert：acad1 公开样本 + auth_required not_measured ════════
def test_slo_acad1_public_samples_and_auth_required_not_measured():
    result = A.evaluate(ACAD1, probe_report=_acad1_report(), interface_payload=None)
    assert result["ok"] is True
    assert result["slo_status"] == "not_measured"
    assert result["auth_required"] == 6
    assert result["auth_required_targets"] == A.ACAD1_AUTH_TARGETS
    assert result["not_measured"] == 4 + 6
    assert result["alerts"] == []
    public = [c for c in result["checks"] if c["role"] == "public"]
    auth = [c for c in result["checks"] if c["role"] == "auth_required"]
    assert len(public) == 4 and len(auth) == 6
    assert all(c["status"] == "not_measured" for c in public)
    assert all(c["status"] == "auth_required" and c["ok"] is None for c in auth)
    # 不伪造 200：auth 端点没有把 401 当成 200/ok
    assert all("observed" not in c for c in auth)


def test_slo_acad1_auth_gate_missing_alerts():
    result = A.evaluate(ACAD1, probe_report=_acad1_report(auth_status=200),
                        interface_payload=None)
    assert result["ok"] is False
    assert result["slo_status"] == "alert"
    assert any(a.startswith("auth_gate_missing:") for a in result["alerts"])
    assert result["not_measured"] == 4  # 只剩 4 公开窗口不足；auth 未被算作 not_measured


def test_slo_acad1_unexpected_auth_status_alerts():
    result = A.evaluate(ACAD1, probe_report=_acad1_report(auth_status=500),
                        interface_payload=None)
    assert result["ok"] is False
    assert any(a.startswith("probe_unexpected_status:") for a in result["alerts"])


def test_slo_acad1_public_probe_failure_alerts():
    result = A.evaluate(ACAD1, probe_report=_acad1_report(public_status=503),
                        interface_payload=None)
    assert result["ok"] is False
    assert result["slo_status"] == "alert"
    assert any(a.startswith("probe_failed:") for a in result["alerts"])


def test_slo_acad1_missing_target_alerts():
    report = _acad1_report()
    report["checks"] = [c for c in report["checks"]
                        if c["name"] != "acad1-research-calendar"]
    result = A.evaluate(ACAD1, probe_report=report, interface_payload=None)
    assert result["ok"] is False
    assert "probe_missing:acad1-research-calendar" in result["alerts"]


def test_slo_acad1_pool_slo_not_measured_when_window_short():
    result = A.evaluate(ACAD1, probe_report=_acad1_report(samples=5, latency=10),
                        interface_payload=None)
    assert result["slo_status"] == "not_measured"
    pool = next(c for c in result["checks"] if c["name"] == "pool")
    assert pool["status"] == "not_measured"
    assert pool["observed"]["samples"] == 5
    assert pool["observed"]["p95_ms"] == 10


def test_slo_acad1_pool_slo_ok_with_enough_samples():
    result = A.evaluate(ACAD1,
                        probe_report=_acad1_report(samples=A.MIN_SLO_SAMPLES, latency=10),
                        interface_payload=None)
    pool = next(c for c in result["checks"] if c["name"] == "pool")
    assert pool["status"] == "ok" and pool["ok"] is True
    # 其余 3 个公开端点无 registry SLO 目标 → 仍显式 not_measured（不自行发明目标）
    other_public = [c for c in result["checks"]
                    if c["role"] == "public" and c["name"] != "pool"]
    assert all(c["status"] == "not_measured" for c in other_public)
    assert all("no registry SLO target" in c["reason"] for c in other_public)
    assert result["slo_status"] == "not_measured"


def test_slo_acad1_pool_slo_alert_on_p95_breach():
    result = A.evaluate(
        ACAD1,
        probe_report=_acad1_report(samples=A.MIN_SLO_SAMPLES, latency=2000),
        interface_payload=None)
    assert result["ok"] is False
    assert any("pool: p95_ms" in a for a in result["alerts"])


def test_slo_acad1_interface_checks_are_separate_observation():
    """数据 SLA 观测（stale/freshness）独立于 SLO 窗口判定，但显式 interface_alerts。"""
    payload = {"freshness_days": 5, "stale": True, "data_date": "2026-11-03",
               "summary": {"pool_total": 30}}
    result = A.evaluate(ACAD1, probe_report=_acad1_report(samples=1),
                        interface_payload=payload)
    assert result["ok"] is True and result["slo_status"] == "not_measured"
    assert result["interface_ok"] is False
    assert any("freshness_days" in a for a in result["interface_alerts"])
    assert any("stale=false" in a for a in result["interface_alerts"])
    assert result["alerts"] == []  # 数据 SLA 不混入 SLO 告警


def test_slo_acad1_cli_json_no_token_leak(monkeypatch, capsys):
    captured = {}

    def fake_probe(samples=None):
        captured["samples"] = samples
        return _acad1_report()

    monkeypatch.setattr(A, "_default_acad1_probe", fake_probe)
    monkeypatch.setattr(A, "_get", lambda url: {
        "freshness_days": 0, "stale": False, "data_date": "2026-11-03",
        "summary": {"pool_total": 30}})
    assert A.main(["--composition", ACAD1, "--json", "--samples", "3"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    result = payload["results"][0]
    assert captured["samples"] == 3
    assert result["slo_status"] == "not_measured"
    assert result["auth_required"] == 6
    assert payload["summary"]["auth_required"] == 6
    assert payload["summary"]["not_measured"] == 4 + 6
    assert payload["summary"]["interface_alerts"] == 0
    for forbidden in ("Bearer ", "zb_session", "Cookie:", "Authorization:"):
        assert forbidden not in out


def test_slo_acad1_cli_requires_login_not_green_without_probe(monkeypatch, capsys):
    """探针目标缺失（例如服务未起/未接线）→ alert，不伪造 not_measured/绿。"""
    monkeypatch.setattr(A, "_default_acad1_probe",
                        lambda samples=None: {"checks": [], "samples_per_target": 0})
    monkeypatch.setattr(A, "_get", lambda url: {"freshness_days": 0, "stale": False,
                                                "data_date": "2026-11-03",
                                                "summary": {"pool_total": 30}})
    assert A.main(["--composition", ACAD1, "--json"]) == 1
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert result["ok"] is False
    assert any(a.startswith("probe_missing:") for a in result["alerts"])
