"""Unit tests for injectable creator workers; no platform or media calls."""
from __future__ import annotations

from typing import Any

from knowledge import creator_workers


def test_page_without_pagination_metadata_is_terminal() -> None:
    assert creator_workers._page({"items": [{"id": 1}]} ) == ([{"id": 1}], None, True)
    assert creator_workers._page({"items": [{"id": 1}], "next_cursor": "next"}) == ([{"id": 1}], "next", False)


def _patch_run(monkeypatch, run: dict[str, Any], target: dict[str, Any]):
    state = {"run": dict(run), "target": dict(target), "finished": []}

    monkeypatch.setattr(creator_workers, "_run_row", lambda run_id: state["run"])
    monkeypatch.setattr(creator_workers, "_target_row", lambda target_id: state["target"])

    def start(run_id: int):
        state["run"]["state"] = "running"
        return dict(state["run"])

    def finish(run_id: int, **kwargs: Any):
        state["run"].update(kwargs)
        state["run"]["state"] = kwargs["state"]
        state["finished"].append(dict(kwargs))
        return dict(state["run"])

    monkeypatch.setattr(creator_workers.creator_ops, "start_run", start)
    monkeypatch.setattr(creator_workers.creator_ops, "finish_run", finish)
    monkeypatch.setattr(creator_workers.creator_ops, "advance_target", lambda *args, **kwargs: None)
    return state


def test_history_success_advances_cursor(monkeypatch):
    state = _patch_run(
        monkeypatch,
        {"run_id": 7, "target_id": 3, "run_type": "history_backfill", "state": "pending", "input_cursor": None},
        {"target_id": 3, "operation_state": "history_backfill_running"},
    )
    processed: list[int] = []

    def fetch(target, cursor, run):
        assert cursor is None
        return {"items": [{"id": 1}, {"id": 2}], "done": True}

    def process(item, target, run):
        processed.append(item["id"])
        return {"cursor": f"c{item['id']}"}

    result = creator_workers.run_history_backfill(7, fetch, process)
    assert result.state == "succeeded"
    assert result.cursor == "c2"
    assert result.items_succeeded == 2
    assert processed == [1, 2]
    assert state["finished"][-1]["output_cursor"] == "c2"


def test_history_item_failure_isolated_and_preserves_last_success(monkeypatch):
    state = _patch_run(
        monkeypatch,
        {"run_id": 8, "target_id": 3, "run_type": "history_backfill", "state": "pending", "input_cursor": "old"},
        {"target_id": 3, "operation_state": "history_backfill_running"},
    )

    def fetch(target, cursor, run):
        return {"items": [{"id": 1}, {"id": 2}], "done": True}

    def process(item, target, run):
        if item["id"] == 2:
            raise RuntimeError("bad record")
        return {"cursor": "after-1"}

    result = creator_workers.run_history_backfill(8, fetch, process)
    assert result.state == "failed"
    assert result.cursor == "after-1"
    assert result.items_seen == 2
    assert result.items_succeeded == 1
    assert result.items_failed == 1
    assert state["finished"][-1]["error_code"] == "ITEM_PROCESS_FAILED"


def test_daily_scan_uses_claimed_targets_and_isolates_target_failures(monkeypatch):
    run_counter = iter([10, 11])
    claimed = [
        {"target_id": 3, "operation_state": "daily_monitoring", "last_success_cursor": "a"},
        {"target_id": 4, "operation_state": "daily_monitoring", "last_success_cursor": "b"},
    ]
    monkeypatch.setattr(creator_workers.creator_ops, "claim_due_targets", lambda limit: claimed)
    monkeypatch.setattr(
        creator_workers.creator_ops,
        "create_run",
        lambda target_id, run_type, cursor: {
            "run_id": next(run_counter), "target_id": target_id, "run_type": run_type,
            "state": "pending", "input_cursor": cursor,
        },
    )
    monkeypatch.setattr(creator_workers, "_run_row", lambda run_id: {
        "run_id": run_id, "target_id": 3 if run_id == 10 else 4,
        "run_type": "daily_scan", "state": "pending", "input_cursor": "a" if run_id == 10 else "b",
    })
    monkeypatch.setattr(creator_workers, "_target_row", lambda target_id: {"target_id": target_id, "operation_state": "daily_monitoring"})
    monkeypatch.setattr(creator_workers.creator_ops, "start_run", lambda run_id: creator_workers._run_row(run_id) | {"state": "running"})
    monkeypatch.setattr(creator_workers.creator_ops, "finish_run", lambda run_id, **kwargs: {"state": kwargs["state"], **kwargs})
    monkeypatch.setattr(creator_workers.creator_ops, "advance_target", lambda *args, **kwargs: None)

    def fetch(target, cursor, run):
        if target["target_id"] == 4:
            raise RuntimeError("temporary platform error")
        return {"items": [{"id": target["target_id"]}], "done": True}

    worker = creator_workers.DailyScanWorker(fetch, lambda item, target, run: {"cursor": f"{target['target_id']}:done"})
    results = worker.run_once(limit=2)
    assert [result.target_id for result in results] == [3, 4]
    assert results[0].state == "succeeded"
    assert results[1].state == "failed"
