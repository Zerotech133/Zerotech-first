# -*- coding: utf-8 -*-
"""P0-05 runtime-ledger readiness tests: temp files only, no production writes."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import d1_runtime_ledger_check as m  # noqa: E402


def _dump(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _decision():
    return {
        "decision_id": "d1-20261107160000-abcdef",
        "code": "002463.SZ",
        "decided_at": "2026-11-07T16:00:00+08:00",
        "decided_by": "D1",
        "tier": "HOLD",
        "schema": "d1_decision/v1",
        "paper_only": True,
    }


def _handoff():
    return {
        "decision_id": "d1-20261107160000-abcdef",
        "receipt_id": "d1h-20261107160000-abcdef",
        "schema": "d1_handoff/v2",
        "paper_only": True,
        "watch_plans": [{"plan_id": "wp-20261107-test"}],
    }


def _meeting():
    return {
        "meeting_id": "d1m-20261107160000-abcdef",
        "schema": "d1_meeting/v1",
        "status": "finalized",
    }


def _episode():
    return {
        "training_episode_id": "ep-20261107-ABCDEF12",
        "schema": "training_episode/v1",
    }


def _fixture(tmp_path: Path):
    paths = {
        "decisions_path": tmp_path / "d1_decisions.jsonl",
        "handoffs_path": tmp_path / "d1_handoffs.jsonl",
        "meetings_dir": tmp_path / "meetings" / "d1",
        "episodes_path": tmp_path / "training" / "episodes.jsonl",
        "plans_path": tmp_path / "watch_plans.jsonl",
        "triggers_path": tmp_path / "watch_triggers.jsonl",
        "paper_state_path": tmp_path / "paper" / "state.json",
        "paper_ledger_path": tmp_path / "paper" / "paper_ledger.jsonl",
    }
    _dump(paths["decisions_path"], [_decision()])
    _dump(paths["handoffs_path"], [_handoff()])
    paths["meetings_dir"].mkdir(parents=True)
    (paths["meetings_dir"] / "m.json").write_text(json.dumps(_meeting()), encoding="utf-8")
    _dump(paths["episodes_path"], [_episode()])
    _dump(paths["plans_path"], [{
        "plan_id": "wp-20261107-test",
        "trigger_id": "trg-20261107-test",
        "source": {"decision_id": "d1-20261107160000-abcdef"},
    }])
    _dump(paths["triggers_path"], [{
        "trigger_id": "trg-20261107-test",
        "confirmation_id": "CFM-TEST00000001",
        "decision_id": "d1-20261107160000-abcdef",
        "handoff_id": "d1h-20261107160000-abcdef",
    }])
    paths["paper_state_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["paper_state_path"].write_text(json.dumps({"positions": {}, "nav": 1.0}), encoding="utf-8")
    paths["paper_ledger_path"].write_text("", encoding="utf-8")
    return paths


def test_complete_three_books_and_chain(tmp_path):
    result = m.check(probe_api=False, **_fixture(tmp_path))
    assert result["ok"] is True
    assert result["ready"] is True
    assert result["incomplete_books"] == []
    books = result["books"]
    assert books["d1_decisions"]["rows"] == 1
    assert books["meetings"]["rows"] == 1
    assert books["training"]["rows"] == 1
    chain = books["chain"]
    assert chain["decisions_with_handoff"] == 1
    assert chain["plans_with_trigger"] == 1
    assert chain["triggers_with_confirmation"] == 1
    assert chain["paper_trade_ids"] == 0
    assert any("trade" in x for x in result["gaps"])


def test_missing_books_are_data_unavailable_not_fabricated(tmp_path):
    missing = {
        "decisions_path": tmp_path / "missing.jsonl",
        "handoffs_path": tmp_path / "missing-h.jsonl",
        "meetings_dir": tmp_path / "missing-meetings",
        "episodes_path": tmp_path / "missing-ep.jsonl",
        "plans_path": tmp_path / "missing-plans.jsonl",
        "triggers_path": tmp_path / "missing-triggers.jsonl",
        "paper_state_path": tmp_path / "missing-state.json",
        "paper_ledger_path": tmp_path / "missing-paper.jsonl",
    }
    result = m.check(probe_api=False, **missing)
    assert result["ok"] is True
    assert result["ready"] is False
    assert result["books"]["d1_decisions"]["rows"] == 0
    assert result["books"]["meetings"]["data_unavailable"] is True
    assert result["books"]["training"]["data_unavailable"] is True
    assert "meetings" in result["incomplete_books"]
    assert "training" in result["incomplete_books"]


def test_invalid_decision_is_structural_failure(tmp_path):
    paths = _fixture(tmp_path)
    bad = _decision()
    bad["schema"] = "bad/v1"
    _dump(paths["decisions_path"], [bad])
    result = m.check(probe_api=False, **paths)
    assert result["ok"] is False
    assert any("bad schema" in e for e in result["structural_errors"])


def test_render_markdown_reports_empty_books(tmp_path):
    paths = _fixture(tmp_path)
    for key in ("decisions_path", "handoffs_path", "episodes_path"):
        paths[key].unlink()
    result = m.check(probe_api=False, **paths)
    text = m.render_markdown(result)
    assert "data_unavailable" in text
    assert "P0-05" in text
    assert "不造数" in text
