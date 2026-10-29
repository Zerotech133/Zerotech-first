# -*- coding: utf-8 -*-
"""zhuge_c1_decisions 单测（C1 ↔ 决策记录，离线）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import zhuge_c1_decisions as b  # noqa: E402


def use_repo(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ZHUGE_C1DEC_REPO", str(repo))
    monkeypatch.setenv("ZHUGE_DECISION_REPO", str(repo))
    monkeypatch.setenv("ZHUGE_MEMORY_REPO", str(repo))
    monkeypatch.setenv("ZHUGE_MEMORY_AUTOWRITE", "1")
    return repo


def card_event(card_id: str, status: str = "open", outcome: str = "",
               responded_by: str = "", close_note: str = ""):
    card = {"card_id": card_id, "date": "2026-10-27", "severity": "normal",
            "status": status, "ref_questions": [
                {"q_id": "Q1", "question": "完成判据是什么？", "dimension": "goal",
                 "target_task": "T-1"}],
            "target_plan_snapshot": {"path": "tasks.json", "source_kind": "tasks_ledger"}}
    if responded_by:
        card["responded_by"] = responded_by
    if outcome:
        card["outcome"] = outcome
        card["closed_by"] = responded_by or "tianfeng"
        card["close_note"] = close_note
    return card


def write_ledger(repo: Path, events: list[dict]) -> Path:
    path = repo / "work" / "zhuge_challenge" / "cards.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return path


def created(card_id: str):
    return {"event": "created", "card_id": card_id, "at": "2026-10-27 10:00:00",
            "card": card_event(card_id)}


def test_sync_open_creates_proposed(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    ledger = write_ledger(repo, [created("ZQ-20261027-01")])
    out = b.sync_all(ledger=ledger)
    assert out["ok"] and out["cards"] == 1 and out["created"] == 1
    from zhuge_decision_record import list_records
    rows = list_records()["rows"]
    assert rows and rows[0]["source_card"] == "ZQ-20261027-01"
    assert rows[0]["status"] == "proposed"


def test_sync_accepted_decides_approved(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    ledger = write_ledger(repo, [created("ZQ-20261027-02")])
    b.sync_all(ledger=ledger)
    # 追加 answered + closed accepted
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "answered", "card_id": "ZQ-20261027-02",
                             "by": "田丰", "note": "已整改", "verdict": "accept",
                             "at": "2026-10-27 10:01:00"}, ensure_ascii=False) + "\n")
        fh.write(json.dumps({"event": "closed", "card_id": "ZQ-20261027-02",
                             "outcome": "accepted", "by": "田丰",
                             "note": "整改完成", "at": "2026-10-27 10:02:00"},
                            ensure_ascii=False) + "\n")
    out2 = b.sync_all(ledger=ledger)
    assert out2["ok"] and out2["decided"] == 1
    from zhuge_decision_record import show
    row = out2["rows"][0]
    assert row["status"] == "approved"
    rec = show(row["id"])["record"]
    assert rec["status"] == "approved" and rec["decided_by"] == "田丰"


def test_sync_rejected(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    ledger = write_ledger(repo, [created("ZQ-20261027-03")])
    b.sync_all(ledger=ledger)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "closed", "card_id": "ZQ-20261027-03",
                             "outcome": "rejected", "by": "田丰",
                             "note": "不成立", "at": "2026-10-27 10:03:00"},
                            ensure_ascii=False) + "\n")
    out2 = b.sync_all(ledger=ledger)
    assert out2["ok"] and out2["decided"] == 1
    from zhuge_decision_record import list_records
    rows = list_records()["rows"]
    assert any(r["source_card"] == "ZQ-20261027-03" and r["status"] == "rejected"
               for r in rows)


def test_dry_run_no_write(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    ledger = write_ledger(repo, [created("ZQ-20261027-04")])
    out = b.sync_all(ledger=ledger, dry_run=True)
    assert out["ok"] and out["created"] == 0
    from zhuge_decision_record import list_records
    assert list_records()["total"] == 0


def test_idempotent_resync(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    ledger = write_ledger(repo, [created("ZQ-20261027-05")])
    b.sync_all(ledger=ledger)
    out2 = b.sync_all(ledger=ledger)
    assert out2["created"] == 0
    from zhuge_decision_record import list_records
    assert list_records()["total"] == 1


def test_sync_accepted_writes_memory(monkeypatch, tmp_path):
    repo = use_repo(monkeypatch, tmp_path)
    ledger = write_ledger(repo, [created("ZQ-20261027-04")])
    b.sync_all(ledger=ledger)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "closed", "card_id": "ZQ-20261027-04",
                             "outcome": "accepted", "by": "田丰",
                             "note": "整改完成", "at": "2026-10-27 10:03:00"},
                            ensure_ascii=False) + "\n")
    b.sync_all(ledger=ledger)
    mem = repo / "work" / "p1" / "memory" / "memory.jsonl"
    assert mem.is_file()
    rows = [json.loads(x) for x in mem.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(r.get("kind") == "decision" and "ZQ-20261027-04" in " ".join(r.get("tags") or [])
               for r in rows)
