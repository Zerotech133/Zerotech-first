# -*- coding: utf-8 -*-
"""tests for tools/w1_acceptance.py（W1 验收一条命令）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "w1_acceptance.py"
spec = importlib.util.spec_from_file_location("w1_acceptance", MOD)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _fake_http_ok(url, method="GET", headers=None, timeout=5):
    if url.endswith("/api/ide/version"):
        return {"ok": True, "status": 200, "body": json.dumps({"version": "v-test"})}
    if url.endswith("/api/sessions"):
        return {"ok": False, "status": 401, "body": ""}
    if url.endswith("/health"):
        return {"ok": True, "status": 200, "body": "ok"}
    # 未授权矩阵：受保护 POST 返回 403
    return {"ok": False, "status": 403, "body": ""}


def test_run_all_green(monkeypatch, tmp_path):
    rel = tmp_path / "current.json"
    rel.write_text(json.dumps({"version": "v-test"}), encoding="utf-8")
    monkeypatch.setattr(m, "_http", _fake_http_ok)
    monkeypatch.setattr(m, "check_p1_regression",
                        lambda: {"id": "p1_regression", "status": "pass", "detail": []})
    rep = m.run(release_path=rel)
    assert rep["ok"] is True
    ids = {c["id"]: c["status"] for c in rep["checks"]}
    assert ids["release_version"] == "pass"
    assert ids["hermes_8787_auth"] == "pass"
    assert ids["unauth_matrix"] == "pass"


def test_run_fails_on_version_mismatch(monkeypatch, tmp_path):
    rel = tmp_path / "current.json"
    rel.write_text(json.dumps({"version": "v-file"}), encoding="utf-8")
    monkeypatch.setattr(m, "_http", _fake_http_ok)
    monkeypatch.setattr(m, "check_p1_regression",
                        lambda: {"id": "p1_regression", "status": "pass", "detail": []})
    rep = m.run(release_path=rel)
    assert rep["ok"] is False
    assert any(c["id"] == "release_version" and c["status"] == "fail" for c in rep["checks"])


# ── 2026-11-01 WO-T1T2-METRICS-20261031：T1/T2 指标接入 ──
def test_run_t1t2_unavailable_carries_reason(monkeypatch, tmp_path):
    """无 trace（worktree 常态）：t1_t2_metrics 保持 unavailable 且带原因，不造数。"""
    rel = tmp_path / "current.json"
    rel.write_text(json.dumps({"version": "v-test"}), encoding="utf-8")
    monkeypatch.setattr(m, "_http", _fake_http_ok)
    monkeypatch.setattr(m, "check_p1_regression",
                        lambda: {"id": "p1_regression", "status": "pass", "detail": []})
    rep = m.run(release_path=rel, data_dir=tmp_path / "no-team-exec")
    row = [c for c in rep["checks"] if c["id"] == "t1_t2_metrics"][0]
    assert row["status"] == "unavailable"
    assert "不造数" in row["detail"]
    assert row.get("unavailable_reasons")
    assert rep["ok"] is True  # unavailable 不判 FAIL（WARN 级语义）


def test_check_t1t2_metrics_available_passes(monkeypatch):
    """真实可用时接 pass（工具返回桩模拟“可用”，指标口径由 test_t1t2_metrics 覆盖）。"""
    import types
    fake = types.SimpleNamespace(aggregate=lambda data_dir=None: {
        "available": True, "gate": {"passed": True, "checks": []}, "arms": {}})
    monkeypatch.setattr(m, "_load_t1t2_module", lambda: fake)
    row = m.check_t1_t2_metrics()
    assert row["status"] == "pass"
    assert row["available"] is True

