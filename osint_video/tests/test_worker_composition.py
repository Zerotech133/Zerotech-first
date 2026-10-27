"""Pure tests for the explicit worker composition root."""

from __future__ import annotations

from types import SimpleNamespace

from workers.composition import CompositionError, compose_worker_dependencies
from workers.run import run
from collectors.ports import SessionHealth, SessionStatus


class Adapter:
    page_bound = True

    def __init__(self, status: str = "authenticated") -> None:
        self.status = status
        self.fetch_calls = 0

    def check_session_health(self):
        return {"status": self.status}

    def fetch(self, target, cursor, run):
        self.fetch_calls += 1
        return {"source_link": target["source_link"], "title": "safe"}


def test_composition_rejects_unbound_adapter() -> None:
    class UnboundAdapter(Adapter):
        page_bound = False

    try:
        compose_worker_dependencies(
            repository=SimpleNamespace(),
            adapter=UnboundAdapter(),
            health_probe=lambda: SessionHealth(status=SessionStatus.AUTHENTICATED),
        )
    except CompositionError as exc:
        assert exc.code == "PAGE_BOUND_ADAPTER_REQUIRED"
    else:
        raise AssertionError("unbound adapter must fail")


def test_composition_requires_explicit_repository_and_collector() -> None:
    try:
        compose_worker_dependencies(repository=None, adapter=Adapter())
    except CompositionError as exc:
        assert exc.code == "REPOSITORY_REQUIRED"
    else:
        raise AssertionError("missing repository must fail")

    try:
        compose_worker_dependencies(
            repository=SimpleNamespace(), adapter=None, browser_fetcher=None
        )
    except CompositionError as exc:
        assert exc.code == "COLLECTOR_REQUIRED"
    else:
        raise AssertionError("missing collector must fail")


def test_composition_rejects_unauthenticated_before_fetch() -> None:
    adapter = Adapter("login_required")
    try:
        compose_worker_dependencies(
            repository=SimpleNamespace(),
            adapter=adapter,
            health_probe=lambda: SessionHealth(status=SessionStatus.LOGIN_REQUIRED),
        )
    except CompositionError as exc:
        assert exc.code == "AUTHORIZATION_REQUIRED"
    else:
        raise AssertionError("unauthenticated composition must fail")
    assert adapter.fetch_calls == 0


def test_run_composes_only_authenticated_dependencies(monkeypatch) -> None:
    adapter = Adapter("expired")
    called = []
    monkeypatch.setattr(
        "workers.run.DailyScanWorker.run_once",
        lambda *args, **kwargs: called.append(True),
    )
    result = run(
        "daily-scan",
        browser_adapter=adapter,
        repository=SimpleNamespace(),
        health_probe=lambda: SessionHealth(status=SessionStatus.LOGIN_REQUIRED),
    )
    assert result.state == "failed"
    assert "AUTHORIZATION_REQUIRED" in (result.error or "")
    assert called == []


def test_authenticated_composition_exposes_safe_worker_ports() -> None:
    composition = compose_worker_dependencies(
        repository=SimpleNamespace(),
        adapter=Adapter(),
        health_probe=lambda: SessionHealth(status=SessionStatus.AUTHENTICATED),
    )
    assert composition.health.status.value == "authenticated"
    assert callable(composition.fetcher)
    assert callable(composition.processor)
