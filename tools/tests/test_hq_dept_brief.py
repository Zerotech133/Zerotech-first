# -*- coding: utf-8 -*-
"""08:00 部门早会：早报模板/采集/会议载荷。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_dept_brief as q  # noqa: E402


def test_template_has_all_leads():
    d = q.template("2026-11-03")
    assert d["schema"] == q.SCHEMA
    assert d["deadline"] == "07:50"
    leads = {x["lead"] for x in d["items"]}
    assert "A0" not in leads and "D1" in leads and "DATA1" in leads and "QA1" in leads
    assert len(leads) == len(d["items"]) == 13


def test_collect_valid_and_live_cap(tmp_path):
    items = q.template("2026-11-03")["items"]
    for x in items:
        x["status"] = "amber" if x["lead"] == "DATA1" else "green"
        x["highlights"] = [x["lead"] + " 今日要点"]
        x["decision_requests"] = ["请 A0 决定 " + x["lead"]] if x["lead"] in ("DATA1", "QA1") else []
    p = tmp_path / "brief.json"
    p.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    d = q.collect(p)
    assert d["submitted"] == len(items)
    assert len(d["live_attendees"]) == 6
    assert d["live_attendees"][0] == "A0"
    assert len(d["decision_requests"]) == 2
    assert d["meeting_payload"]["type"] == "dept_morning"
    assert d["meeting_payload"]["attendees"] == d["live_attendees"]


def test_collect_directory_and_unknown(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"lead": "D1", "status": "green",
        "highlights": ["量化"], "decision_requests": []}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "bad.json").write_text(json.dumps({"lead": "UNKNOWN", "status": "red"}), encoding="utf-8")
    d = q.collect(tmp_path)
    assert d["submitted"] == 1
    assert any(x["error"] == "unknown_lead" for x in d["errors"])


def test_collect_bad_status(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"items": [{"lead": "D1", "status": "purple"}]}), encoding="utf-8")
    d = q.collect(p)
    assert d["submitted"] == 0
    assert any(x["error"] == "bad_status" for x in d["errors"])


def test_out_file(tmp_path):
    out = tmp_path / "out.json"
    assert q.main(["--template", "--out", str(out)]) == 0
    assert out.is_file() and json.loads(out.read_text(encoding="utf-8"))["deadline"] == "07:50"
