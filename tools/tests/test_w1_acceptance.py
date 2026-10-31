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
