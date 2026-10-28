# -*- coding: utf-8 -*-
"""LS-02 数据契约测试（M0 冻结前校验）：只读/字段/真源。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs" / "dev" / "legion_screen_contract.json"


def _c():
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_readonly_and_endpoint():
    c = _c()
    assert c["readonly"] is True
    assert c["method"] == "GET"
    assert c["endpoint"] == "/api/dev/legion"
    assert c["schema"] == "legion_screen_contract/v1"


def test_response_shape():
    r = _c()["response"]
    for k in ("generated_at", "department", "seats", "work_in_progress",
              "metrics", "redlines", "sources"):
        assert k in r
    dept = r["department"]
    assert dept["id"] == "dev" and dept["lead"] == "ARCH1"
    assert len(dept["groups"]) == 2
    assert {"id", "name", "lead", "members"} <= set(dept["groups"][0])
    assert len(r["seats"]) == 1  # 模板示意，实现时按 11 席填充
    assert {"id", "name", "role_title", "discipline", "group", "grade",
            "model", "org_role", "report_to"} <= set(r["seats"][0])


def test_metrics_and_sources():
    c = _c()
    m = c["response"]["metrics"]
    for k in ("window_days", "dev_tasks_recent", "dev_merged_recent",
              "owner_legal_rate_recent", "verification_rate", "e3_trigger",
              "dev_leadtime_p50_min"):
        assert k in m
    for src in c["response"]["sources"]:
        assert (ROOT / src).exists(), src


def test_degradation_declared():
    e = _c()["errors"]
    assert e["fallback"] == "empty"
    assert "不 500" in e["note"] or "500" in e["note"]
