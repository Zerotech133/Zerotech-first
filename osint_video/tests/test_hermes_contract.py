"""Pure unit tests for the Hermes integration contract."""

from __future__ import annotations

import pytest

from integration.hermes_contract import (
    CreatorRunCommand,
    HermesOrchestrator,
    LinkCommand,
    ProviderOutput,
    RequestMeta,
)


def meta() -> RequestMeta:
    return RequestMeta(request_id="req-1", correlation_id="corr-1")


def test_link_submission_delegates_callback_and_envelopes_response() -> None:
    calls: list[tuple[object, ...]] = []

    def enqueue(
        urls: list[str], created_by: str, route: str, priority: int
    ) -> dict[str, int]:
        calls.append((urls, created_by, route, priority))
        return {"accepted": 1, "duplicated": 0}

    adapter = HermesOrchestrator(enqueue, lambda *_: {})
    response = adapter.submit_links(LinkCommand(("https://example.invalid/a",)), meta())
    assert response.code == 0
    assert response.data == {"accepted": 1, "duplicated": 0}
    assert calls[0][1:] == ("hermes", "AUTO", 5)


def test_creator_run_trigger_only_returns_run_and_event() -> None:
    adapter = HermesOrchestrator(
        lambda *_: {},
        lambda *_: {"run_id": 9, "state": "pending"},
        provider=lambda *_: {"summary": "must not run"},
    )
    response = adapter.history(3, meta(), "cursor-1")
    assert response.data["run"] == {"run_id": 9, "state": "pending"}
    assert response.data["event"].event_type == "creator.run.created"
    assert response.data["event"].event_id.endswith(":creator.run.created:9")
    assert "summary" not in response.data


def test_provider_runs_only_when_worker_processes_run() -> None:
    calls: list[int] = []

    class Provider:
        def generate(
            self, command: CreatorRunCommand, run: dict[str, object]
        ) -> dict[str, object]:
            calls.append(int(run["run_id"]))
            return {
                "summary": "ok",
                "evidence": [{"source_ref": "x"}],
                "index": {"status": "pending"},
            }

    adapter = HermesOrchestrator(lambda *_: {}, lambda *_: {"run_id": 3}, Provider())
    adapter.history(3, meta())
    assert calls == []
    response = adapter.process_run(
        CreatorRunCommand(3, "history_backfill"), {"run_id": 3}, meta()
    )
    assert calls == [3]
    assert response.data == {
        "summary": "ok",
        "evidence": [{"source_ref": "x"}],
        "index": {"status": "pending"},
    }


def test_provider_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        ProviderOutput.from_mapping({"summary": "ok", "token": "secret"})
    with pytest.raises(ValueError, match="nested"):
        ProviderOutput.from_mapping(
            {"evidence": ({"source_ref": "x", "cookie": "secret"},)}
        )


def test_invalid_commands_are_rejected() -> None:
    with pytest.raises(ValueError):
        LinkCommand((), route="AUTO")
    with pytest.raises(ValueError):
        CreatorRunCommand(0, "daily_scan")
