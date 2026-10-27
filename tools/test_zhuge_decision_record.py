# -*- coding: utf-8 -*-
"""zhuge_decision_record v0 单测（离线，临时仓库）。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_decision_record as d  # noqa: E402


def use_repo(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(repo))
    return repo


def make(monkeypatch, tmp_path, **kw):
    use_repo(monkeypatch, tmp_path)
    args = {"title": "P1-4 上线", "question": "是否上线结构化决策记录？",
            "recommendation": "先 v0 独立台账，不接 L5", "confidence": "high"}
    args.update(kw)
    return d.create(**args)


def test_create_and_list_show(monkeypatch, tmp_path):
    out = make(monkeypatch, tmp_path)
    assert out["ok"] and out["id"].startswith("DR-")
    assert d.list_records()["total"] == 1
    got = d.show(out["id"])
    assert got["ok"] and got["record"]["recommendation"].startswith("先 v0")


def test_create_requires_fields(monkeypatch, tmp_path):
    use_repo(monkeypatch, tmp_path)
    assert d.create("", "q", "r")["ok"] is False
    assert d.create("t", "q", "r", confidence="bad")["ok"] is False


def test_duplicate(monkeypatch, tmp_path):
    a = make(monkeypatch, tmp_path)
    b = d.create(title="P1-4 上线", question="是否上线结构化决策记录？",
                 recommendation="先 v0 独立台账，不接 L5")
    assert b["ok"] and b["duplicate"] is True and b["id"] == a["id"]


def test_decide_requires_by_and_transition(monkeypatch, tmp_path):
    out = make(monkeypatch, tmp_path)
    rid = out["id"]
    assert d.decide(rid, "approved", by="")["ok"] is False
    assert d.decide(rid, "approved", by="老田", note="ok")["ok"] is True
    assert d.decide(rid, "deferred", by="老田")["ok"] is False  # approved→deferred 非法
    row = d.show(rid)["record"]
    assert row["status"] == "approved" and row["decided_by"] == "老田"


def test_update_outcome_and_review_due(monkeypatch, tmp_path):
    out = make(monkeypatch, tmp_path)
    rid = out["id"]
    d.decide(rid, "approved", by="老田")
    assert d.update(rid, outcome="本周独立台账已上线", review_at="2026-10-01")["updated"] is True
    due = d.review_due(day="2026-10-27")
    assert due["count"] == 1 and due["rows"][0]["id"] == rid


def test_invalid_transition_from_rejected(monkeypatch, tmp_path):
    out = make(monkeypatch, tmp_path)
    rid = out["id"]
    assert d.decide(rid, "rejected", by="老田")["ok"] is True
    assert d.decide(rid, "approved", by="老田")["ok"] is False


def test_validate_clean(monkeypatch, tmp_path):
    make(monkeypatch, tmp_path)
    assert d.validate()["ok"] is True
