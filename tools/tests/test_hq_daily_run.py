# -*- coding: utf-8 -*-
"""A0 每日跑板：13 部门 + 中军帐四席（17 席）编排逻辑（模型注入 mock）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_daily_run as d  # noqa: E402


def _fake(lead, date, base, timeout, data_brief=""):
    text = json.dumps({"status": "amber",
                       "yesterday_done": [lead["lead"] + " 昨天完成"],
                       "in_progress": [lead["lead"] + " 正在做"],
                       "blockers": ["当前卡点：需查证"], "decision_requests": ["请 A0 定"],
                       "research_topics": [{"title": lead["lead"] + " 研究题", "question": "怎么验证"}]},
                      ensure_ascii=False)
    return {"lead": lead, "text": text, "binding": "glm/glm-5.3", "latency_ms": 1}


def test_run_all_seats_and_write(monkeypatch, tmp_path):
    monkeypatch.setattr(d, "_ask", _fake)
    r = d.run("2026-11-04", write=True, out_dir=tmp_path)
    assert r["ok"] and r["submitted"] == r["expected"] == 17
    assert (tmp_path / "rollup.json").is_file()
    assert (tmp_path / "a0_briefing.md").is_file()
    b = json.loads((tmp_path / "rollup.json").read_text(encoding="utf-8"))["briefing"]
    assert b["schema"] == "hq/a0_briefing/v1"
    assert b["departments"][0]["yesterday_done"] and b["departments"][0]["in_progress"]
    assert len(b["research_topics"]) == 17
    leads={x["lead"] for x in b["departments"]}
    assert {"AI1","ZHAOYUN","GUANYU","HUANGZHONG"} <= leads
    assert "数据智能部" in (tmp_path / "a0_briefing.md").read_text(encoding="utf-8")


def test_partial_failure_is_visible(monkeypatch, tmp_path):
    def flaky(lead, date, base, timeout, data_brief=""):
        if lead["lead"] == "QA1":
            raise RuntimeError("cloud timeout")
        return _fake(lead, date, base, timeout, data_brief)
    monkeypatch.setattr(d, "_ask", flaky)
    r = d.run("2026-11-04", write=True, out_dir=tmp_path)
    assert r["ok"] is False
    assert r["submitted"] == 16 and len(r["failed"]) == 1
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


def test_main_no_write_summary(monkeypatch, capsys):
    monkeypatch.setattr(d, "_ask", _fake)
    assert d.main(["--no-write", "--json", "--date", "2026-11-04"]) == 0
    import json as _j
    out = _j.loads(capsys.readouterr().out)
    assert out["ok"] and out["submitted"] == 17 and out["out_dir"] is None


def test_brief_digest(tmp_path):
    import json as _j
    p = tmp_path / "b.json"
    p.write_text(_j.dumps({
        "as_of": "2026-11-04T05:00:00+08:00",
        "data_health": {"assets": {"all": 399, "registered": 350, "exposed": 80},
                        "catalog": {"version": 38, "tables": 304},
                        "freshness": {"data_date": "2026-11-02", "age_days": 2}},
        "alarms": [{"code": "today_zero", "summary": "雪球 today 0"}],
        "mining_signals": [{"id": "selection.executability", "summary": "0 获批/picks=0"}],
        "questions": [{"to": "A0", "q": "观察还是空仓？"}],
        "boundaries": ["不是交易建议"],
    }, ensure_ascii=False), encoding="utf-8")
    s = d._brief_digest(p)
    assert "数据简报" in s and "399" in s and "雪球 today 0" in s
    assert "selection.executability" in s and "观察还是空仓" in s and "不是交易建议" in s
    assert d._brief_digest(tmp_path / "missing.json") == ""
