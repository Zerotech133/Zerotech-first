"""Safe composition root for production creator workers.

The composition root is intentionally explicit: callers provide an already
configured browser adapter (or a browser fetcher) and a knowledge repository.
No adapter, credentials, browser session, or platform client is created here.
The resulting dependency graph is::

    BrowserAdapter -> BrowserFetcher -> BrowserCollectorAdapter
                   -> SafeCollector -> ItemProcessor(repository)

A session health probe is performed before a composition is returned.  This
keeps an unauthenticated scheduler invocation from claiming a database run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from collectors.browser_mapper import BrowserCollectorAdapter
from collectors.ports import SafeCollector, SessionHealth, SessionStatus
from knowledge.browser_fetcher import (
    BrowserAdapter,
    BrowserFetcher,
    BrowserFetcherProtocol,
    BrowserSessionHealth,
    BrowserSessionHealthProbe,
    BrowserSessionStatus,
)
from knowledge.item_processor import ItemProcessor, KnowledgeWritePort


class CompositionError(RuntimeError):
    """Raised when safe worker dependencies cannot be composed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkerComposition:
    """Fully injected worker dependencies and their safe health snapshot."""

    fetcher: Callable[..., Any]
    processor: ItemProcessor
    collector: SafeCollector
    browser_fetcher: BrowserFetcherProtocol
    health: SessionHealth

    def assert_authenticated(self) -> SessionHealth:
        """Probe current health and reject all non-authenticated states."""
        health = self.collector.session_health()
        if health.status is not SessionStatus.AUTHENTICATED:
            raise CompositionError(
                _health_error_code(health.status), _health_error_message(health.status)
            )
        return health


def compose_worker_dependencies(
    *,
    repository: KnowledgeWritePort | None = None,
    adapter: BrowserAdapter | None = None,
    browser_fetcher: BrowserFetcherProtocol | None = None,
    health_probe: BrowserSessionHealthProbe | None = None,
    processing_run_key: str | None = None,
) -> WorkerComposition:
    """Build safe fetcher/processor dependencies from explicit inputs.

    Exactly one of ``adapter`` and ``browser_fetcher`` must be supplied.  An
    adapter is wrapped in :class:`BrowserFetcher`; an already-built fetcher is
    accepted for hosts that own browser lifecycle themselves.  ``repository``
    is required and is never defaulted to a global or test repository.
    """
    if repository is None:
        raise CompositionError(
            "REPOSITORY_REQUIRED", "knowledge repository is required"
        )
    if (adapter is None) == (browser_fetcher is None):
        raise CompositionError(
            "COLLECTOR_REQUIRED",
            "exactly one browser adapter or browser fetcher is required",
        )
    if adapter is not None:
        _require_callable(adapter, "check_session_health", "ADAPTER_INVALID")
        _require_callable(adapter, "fetch", "ADAPTER_INVALID")
        if getattr(adapter, "page_bound", False) is not True:
            raise CompositionError(
                "PAGE_BOUND_ADAPTER_REQUIRED",
                "browser adapter must be bound to one explicit visible page",
            )
        # A raw adapter is not sufficient evidence of an authenticated visible
        # browser session.  The host must build this probe in the same process
        # as its visible Playwright page (for example via
        # make_visible_page_health_probe(page)); we never attach to CDP,
        # inspect storage state, or infer health from credentials.
        if not callable(health_probe):
            raise CompositionError(
                "HEALTH_PROBE_REQUIRED",
                "same-process visible page health_probe is required",
            )
        # Keep the exact probe object in the browser fetcher so every fetch
        # rechecks the same visible page. The probe contract is deliberately
        # restricted to typed health values; arbitrary health JSON is rejected.
        fetcher: BrowserFetcherProtocol = BrowserFetcher(
            adapter, health_probe=_typed_health_probe(health_probe)
        )
    else:
        assert browser_fetcher is not None
        _require_callable(browser_fetcher, "session_health", "FETCHER_INVALID")
        _require_callable(browser_fetcher, "fetch", "FETCHER_INVALID")
        if not callable(health_probe):
            raise CompositionError(
                "HEALTH_PROBE_REQUIRED",
                "same-process visible page health_probe is required",
            )
        health_probe = _typed_health_probe(health_probe)
        probe_health = _probe_health(health_probe)
        if probe_health.status is not SessionStatus.AUTHENTICATED:
            raise CompositionError(
                _health_error_code(probe_health.status),
                _health_error_message(probe_health.status),
            )
        fetcher = browser_fetcher
    collector_adapter = BrowserCollectorAdapter(fetcher)
    collector = SafeCollector(collector_adapter)
    health = collector.session_health()
    if health.status is not SessionStatus.AUTHENTICATED:
        raise CompositionError(
            _health_error_code(health.status), _health_error_message(health.status)
        )
    processor = ItemProcessor(repository, processing_run_key=processing_run_key)

    def fetch_page(target: Any, cursor: str | None, run: Any = None) -> dict[str, Any]:
        """Adapt the collector's typed page to the worker page protocol.

        ``SafeCollector.__call__`` intentionally emits serialized mappings for
        generic callers.  The knowledge processor requires the typed
        :class:`VideoPage`, so this composition keeps the DTO in-process and
        only wraps the page in the worker's pagination envelope.
        """
        del run
        if not isinstance(target, Mapping):
            raise TypeError("worker target must be a mapping")
        source_link = target.get("source_link") or target.get("link")
        if not isinstance(source_link, str):
            raise ValueError("target source_link is required")
        page = collector.fetch_video(source_link, cursor)
        return {
            "items": [page],
            "next_cursor": page.next_cursor,
            "done": page.done,
        }

    return WorkerComposition(
        fetcher=fetch_page,
        processor=processor,
        collector=collector,
        browser_fetcher=fetcher,
        health=health,
    )


def build_worker_composition(**kwargs: Any) -> WorkerComposition:
    """Descriptive alias for :func:`compose_worker_dependencies`."""
    return compose_worker_dependencies(**kwargs)


def _typed_health_probe(probe: Callable[[], Any]) -> Callable[[], BrowserSessionHealth]:
    """Accept only typed visible-page health, never arbitrary health JSON."""

    def invoke() -> BrowserSessionHealth:
        raw = probe()
        if isinstance(raw, BrowserSessionHealth):
            return raw
        if isinstance(raw, SessionHealth):
            try:
                status = BrowserSessionStatus(raw.status.value)
            except ValueError:
                status = BrowserSessionStatus.UNKNOWN
            return BrowserSessionHealth(status=status, checked_at=raw.checked_at)
        if isinstance(raw, (BrowserSessionStatus, SessionStatus)):
            try:
                return BrowserSessionHealth(status=BrowserSessionStatus(raw.value))
            except (TypeError, ValueError) as exc:
                raise CompositionError(
                    "HEALTH_PROBE_INVALID", "visible page health status is invalid"
                ) from exc
        raise CompositionError(
            "HEALTH_PROBE_INVALID",
            "health_probe must return typed visible-page health",
        )

    return invoke


def _probe_health(probe: Callable[[], Any]) -> SessionHealth:
    """Normalize a host's visible-page health probe without retaining raw data."""
    try:
        raw = probe()
    except Exception as exc:
        raise CompositionError(
            "SESSION_UNKNOWN", "visible page health probe failed"
        ) from exc
    if isinstance(raw, SessionHealth):
        return raw
    if isinstance(raw, BrowserSessionHealth):
        try:
            return SessionHealth(
                status=SessionStatus(raw.status.value), checked_at=raw.checked_at
            )
        except (AttributeError, ValueError):
            return SessionHealth(status=SessionStatus.UNKNOWN)
    if hasattr(raw, "value"):
        raw = raw.value
    try:
        return SessionHealth(status=SessionStatus(str(raw)))
    except ValueError:
        return SessionHealth(status=SessionStatus.UNKNOWN)


def _require_callable(value: Any, attribute: str, code: str) -> None:
    if not callable(getattr(value, attribute, None)):
        raise CompositionError(
            code, f"injected dependency must provide callable {attribute}"
        )


def _health_error_code(status: SessionStatus) -> str:
    if status in {SessionStatus.LOGIN_REQUIRED, SessionStatus.EXPIRED}:
        return "AUTHORIZATION_REQUIRED"
    if status is SessionStatus.BLOCKED:
        return "SESSION_BLOCKED"
    return "SESSION_UNKNOWN"


def _health_error_message(status: SessionStatus) -> str:
    if status in {SessionStatus.LOGIN_REQUIRED, SessionStatus.EXPIRED}:
        return "collector session requires authorization"
    if status is SessionStatus.BLOCKED:
        return "collector session is blocked"
    return "collector session health is not authenticated"


__all__ = [
    "CompositionError",
    "WorkerComposition",
    "build_worker_composition",
    "compose_worker_dependencies",
]
