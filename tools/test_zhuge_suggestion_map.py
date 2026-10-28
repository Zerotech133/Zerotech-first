# -*- coding: utf-8 -*-
"""建议→决策映射 v1：决策记录 suggestion_ids + brief 精确 join（临时仓库）。"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import zhuge_decision_record as dr  # noqa: E402

BRIEF = TOOLS.parent / "portal" / "api_zhuge_brief.py"
BEIJING = timezone(timedelta(hours=8))
CLOCK = datetime(2026, 10, 27, 9, 0, tzinfo=BEIJING)


def _load_brief():
    spec = importlib.util.spec_from_file_location("api_zhuge_brief", BRIEF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _raw_with_blocked_notify():
    return {
        "notify": {
            "ok": True,
            "errors": [],
            "items": [{
                "id": "n1", "title": "阻断通知", "state": "blocked",
                "created_at": "2026-10-27T07:00:00+08:00",
                "reason": "通道异常",
            }],
        },
    }


def test_decision_suggestion_ids_create_update(monkeypatch, tmp_path):
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(tmp_path))
    out = dr.create("映射测试", "怎么映射？", "声明 suggestion_ids",
                    suggestion_ids=["task:t1", "notify:n1"])
    assert out["ok"]
    rec = dr.show(out["id"])["record"]
    assert rec["suggestion_ids"] == ["task:t1", "notify:n1"]
    assert dr.decide(out["id"], "approved", by="老田")["ok"]
    assert dr.update(out["id"], suggestion_ids=["alert:a1"])["ok"]
    rec = dr.show(out["id"])["record"]
    assert rec["suggestion_ids"] == ["task:t1", "notify:n1", "alert:a1"]


def test_brief_joins_decision_id(monkeypatch, tmp_path):
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(tmp_path))
    m = _load_brief()
    first = m.build_brief(_raw_with_blocked_notify(), CLOCK)
    assert first["suggestions"], "notify blocked 应产生建议"
    sid = first["suggestions"][0]["id"]
    assert "decision_id" not in first["suggestions"][0]

    out = dr.create("映射测试", "怎么映射？", "声明 suggestion_ids",
                    suggestion_ids=[sid])
    second = m.build_brief(_raw_with_blocked_notify(), CLOCK)
    hit = [s for s in second["suggestions"] if s.get("id") == sid]
    assert hit and hit[0]["decision_id"] == out["id"]


def test_brief_without_mapping_no_fabrication(monkeypatch, tmp_path):
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(tmp_path))
    m = _load_brief()
    out = m.build_brief(_raw_with_blocked_notify(), CLOCK)
    assert out["suggestions"]
    assert all("decision_id" not in s for s in out["suggestions"])
