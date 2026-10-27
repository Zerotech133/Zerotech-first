"""Completion-gate tests for the queue-to-knowledge lifecycle boundary."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from queue_link import repository as queue_repository  # noqa: E402
from queue_link.models import LinkStatus, LinkTask  # noqa: E402


def _extracted_task() -> LinkTask:
    """Build an extracted task suitable for a done transition."""
    return LinkTask(
        id=42,
        sph_url="https://example.invalid/s/gate",
        url_hash="a" * 40,
        status=LinkStatus.EXTRACTED,
    )


@pytest.mark.parametrize("gate_result", [False, True])
def test_transition_done_calls_knowledge_gate(
    monkeypatch: pytest.MonkeyPatch, gate_result: bool
) -> None:
    """The done transition must consult the lifecycle gate before updating PG."""
    task = _extracted_task()
    calls: list[int] = []
    executed: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(queue_repository, "get_by_id", lambda task_id: task)

    def fake_gate(task_id: int) -> bool:
        calls.append(task_id)
        return gate_result

    monkeypatch.setattr("store.repository.can_mark_link_done", fake_gate)
    monkeypatch.setattr(
        queue_repository.db,
        "execute",
        lambda sql, params=None: executed.append((sql, params or {})) or 1,
    )

    result = queue_repository.transition(task.id or 0, LinkStatus.DONE)

    assert calls == [42]
    if gate_result:
        assert executed and executed[0][1]["status"] == LinkStatus.DONE
        assert result is task
    else:
        assert executed == []
        assert result is None
