# -*- coding: utf-8 -*-
"""元数据日报（P0-1）纯函数测试：summarize / render_line 异常判定与摘要格式。"""
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import metadata_daily as md  # noqa: E402


def _meta(issues=None):
    return {"code": 0, "catalog_version": 16, "tables": 253, "physical_count": 253,
            "issues": issues or [], "warnings": []}


def _fresh(code=0, issues=None, checks=10):
    return {"code": code, "as_of": "2026-10-30", "checks": [{}] * checks,
            "issues": issues or []}


def _backup(code=0, missing=None, gen="2026-10-29_0300"):
    return {"code": code, "latest_generation": gen, "age_hours": 10.5,
            "missing": missing or [], "receipt_present": True, "issues": []}


def test_summarize_ok():
    s = md.summarize(_meta(), _fresh(), _backup(), "abc123")
    assert s["meta_ok"] is True
    assert s["fresh_ok"] is True
    assert s["backup_ok"] is True
    assert s["anomalies"] == []
    assert s["catalog_version"] == 16
    line = md.render_line(s)
    assert "metadata_daily sha=abc123" in line
    assert "meta=ok" in line and "fresh=ok" in line and "backup=ok" in line
    assert "anomalies=0" in line


def test_summarize_detects_anomalies():
    s = md.summarize(_meta(["registry_sha_mismatch"]), _fresh(code=1, issues=["stale"]),
                     _backup(code=1, missing=["zhice_db"]), "def456")
    assert s["meta_ok"] is False
    assert s["fresh_ok"] is False
    assert s["backup_ok"] is False
    joined = " ".join(s["anomalies"])
    assert "meta:registry_sha_mismatch" in joined
    assert "fresh:stale" in joined
    assert "backup:missing=zhice_db" in joined
    line = md.render_line(s)
    assert "meta=FAIL" in line and "fresh=FAIL" in line and "backup=FAIL" in line


def test_render_line_missing_values():
    s = md.summarize({"issues": ["catalog_or_registry_missing"], "catalog_version": None,
                      "tables": None, "physical_count": None},
                     {"code": 1, "checks": [], "issues": []},
                     {"code": 1, "missing": [], "latest_generation": None, "age_hours": None},
                     "")
    line = md.render_line(s)
    assert "sha=?" in line
    assert "gen=None" in line
