# -*- coding: utf-8 -*-
"""A0 每日跑板：13 部门真实编排逻辑（模型调用注入 mock）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_daily_run as d  # noqa: E402


def _fake(lead, date, base, timeout):
    text = json.dumps({"status": "amber", "highlights": [lead["lead"] + " 要点"],
                       "blockers": ["需查证"], "decision_requests": ["请 A0 定"],
                       "research_topics": [{"title": lead["lead"] + " 研究题", "question": "怎么验证"}]},
                      ensure_ascii=False)
    return {"lead": lead, "text": text, "binding": "glm/glm-5.3", "latency_ms": 1}


def test_run_all_13_and_write(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_ask", _fake)
    r = d.run("2026-11-04", write=True, out_dir=tmp_path)
    assert r["ok"] and r["submitted"] == r["expected"] == 13
    assert (tmp_path / "rollup.json").is_file()
    assert (tmp_path / "a0_briefing.md").is_file()
    b = json.loads((tmp_path / "rollup.json").read_text(encoding="utf-8"))["briefing"]
    assert b["schema"] == "hq/a0_briefing/v1"
    assert len(b["research_topics"]) == 13
    assert "数据智能部" in (tmp_path / "a0_briefing.md").read_text(encoding="utf-8")


def test_partial_failure_is_visible(monkeypatch, tmp_path):
    def flaky(lead, date, base, timeout):
        if lead["lead"] == "QA1":
            raise RuntimeError("cloud timeout")
        return _fake(lead, date, base, timeout)
    monkeypatch.setattr(d, "_ask", flaky)
    r = d.run("2026-11-04", write=True, out_dir=tmp_path)
    assert r["ok"] is False
    assert r["submitted"] == 12 and len(r["failed"]) == 1
    assert r["failed"][0]["lead"] == "QA1"


def test_no_write(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_ask", _fake)
    r = d.run("2026-11-04", write=False, out_dir=tmp_path)
    assert r["ok"] and not (tmp_path / "rollup.json").exists()


def test_parse_malformed_output_does_not_fabricate():
    lead = {"lead": "DATA1", "dept": "data", "name": "数据智能部"}
    r = d._parse(lead, "这不是 JSON，模型胡说")
    assert r["status"] == "amber"
    assert r["highlights"] == [] and r["blockers"] == []
