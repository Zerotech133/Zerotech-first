# -*- coding: utf-8 -*-
"""端到端闭环演练（合成数据、临时仓库；不碰生产账本）。

链路：C1 质询卡 → P1-4 决策记录（proposed/approved）
      → 声明 adoption_domains → C3 采纳回填 → review-due。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_c1_decisions as c1b  # noqa: E402
import zhuge_c3_decisions as c3b  # noqa: E402
import zhuge_decision_record as dr  # noqa: E402


def test_full_closed_loop(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_C1DEC_REPO", str(repo))
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(repo))
    monkeypatch.setenv("ZHUGE_C3DEC_REPO", str(repo))

    # 1) C1 质询卡：created → answered → closed accepted
    c1_ledger = repo / "work" / "zhuge_challenge" / "cards.jsonl"
    c1_ledger.parent.mkdir(parents=True)
    card = {
        "card_id": "ZQ-20991231-01", "date": "2026-10-27", "severity": "normal",
        "status": "open",
        "ref_questions": [{"q_id": "Q1", "question": "整改判据是什么？",
                           "dimension": "goal", "target_task": "T-1"}],
        "target_plan_snapshot": {"path": "plans/2026-10-27_plan.json",
                                 "source_kind": "snapshot_file"},
    }
    events = [
        {"event": "created", "card_id": card["card_id"], "at": "2026-10-27 09:10:00",
         "card": card},
        {"event": "answered", "card_id": card["card_id"], "at": "2026-10-27 09:20:00",
         "by": "田丰", "note": "已整改", "verdict": "accept"},
        {"event": "closed", "card_id": card["card_id"], "at": "2026-10-27 09:30:00",
         "outcome": "accepted", "by": "田丰", "note": "整改完成"},
    ]
    with open(c1_ledger, "w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")

    # 2) C1 → 决策（-approved）
    sync = c1b.sync_all(ledger=c1_ledger)
    assert sync["ok"] and sync["created"] == 1
    recs = dr.list_records()["rows"]
    assert len(recs) == 1
    rid = recs[0]["id"]
    assert recs[0]["source_card"] == "ZQ-20991231-01"
    assert recs[0]["status"] == "approved"
    assert recs[0]["decided_by"] == "田丰"

    # 3) 决策声明采纳 domain（真实场景由人/建议侧声明）
    assert dr.update(rid, adoption_domains=["notify:deadline:us:postclose"])["ok"]

    # 4) C3 采纳信号 → 回填
    adoption = repo / "portal" / "data" / "zhuge_adoption.jsonl"
    adoption.parent.mkdir(parents=True)
    with open(adoption, "w", encoding="utf-8", newline="\n") as fh:
        for i in range(3):
            fh.write(json.dumps({"at": "2026-10-27T1%d:00:00+08:00" % i,
                                 "domain": "notify:deadline:us:postclose",
                                 "ua": "demo"}, ensure_ascii=False) + "\n")
    out = c3b.sync_adoptions()
    assert out["ok"] and out["linked"] and out["linked"][0]["id"] == rid
    rec = dr.show(rid)["record"]
    assert rec["adoption_id"] == "C3:notify:deadline:us:postclose"
    assert "3 条" in rec["outcome"]

    # 5) review-due 到期可复盘
    due = dr.review_due(day="2026-11-03")
    assert due["count"] == 1 and due["rows"][0]["id"] == rid

    # 6) 幂等：重复同步不新增/不重复回填
    before = len(dr._load_events())
    c1b.sync_all(ledger=c1_ledger)
    c3b.sync_adoptions()
    assert len([r for r in dr._load_events() if r.get("event") == "created"]) == 1
    assert len(dr._load_events()) >= before  # 允许 update，不允许重复 create
