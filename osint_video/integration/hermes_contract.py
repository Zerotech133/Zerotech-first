"""Pure-Python contract and orchestration boundary for Hermes.

This module intentionally has no database, FastAPI, Hermes SDK, or platform
network dependency.  Production handlers inject ``enqueue_link`` and
``create_run`` callbacks backed by existing osint_video repositories; tests can
use in-memory callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Protocol

RunType = Literal["history_backfill", "daily_scan"]
_ALLOWED_ROUTES = frozenset({"D", "B", "AUTO"})
_SAFE_PROVIDER_KEYS = frozenset({"summary", "evidence", "index"})
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "auth",
        "cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "path",
        "media_path",
        "raw_path",
        "file_path",
        "url",
        "media_url",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RequestMeta:
    """Correlation metadata; secrets and tokens are intentionally excluded."""

    caller: str = "hermes-osint"
    request_id: str = ""
    correlation_id: str = ""
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.caller.strip()
            or not self.request_id.strip()
            or not self.correlation_id.strip()
        ):
            raise ValueError("caller, request_id, and correlation_id are required")


@dataclass(frozen=True)
class LinkCommand:
    """Link submission command accepted from Hermes."""

    urls: tuple[str, ...]
    created_by: str = "hermes"
    route: str = "AUTO"
    priority: int = 5

    def __post_init__(self) -> None:
        if not self.urls or any(not url.strip() for url in self.urls):
            raise ValueError("urls must contain at least one non-empty URL")
        if self.route not in _ALLOWED_ROUTES:
            raise ValueError(f"unsupported route: {self.route}")
        if not 1 <= self.priority <= 100:
            raise ValueError("priority must be between 1 and 100")
        if not self.created_by.strip():
            raise ValueError("created_by must not be empty")


@dataclass(frozen=True)
class CreatorRunCommand:
    """History or scan run command."""

    target_id: int
    run_type: RunType
    input_cursor: str | None = None

    def __post_init__(self) -> None:
        if self.target_id < 1:
            raise ValueError("target_id must be positive")
        if self.run_type not in ("history_backfill", "daily_scan"):
            raise ValueError(f"unsupported run_type: {self.run_type}")


@dataclass(frozen=True)
class IntegrationEnvelope:
    """Stable response envelope shared by all integration handlers."""

    code: int
    data: Any
    message: str = "ok"
    request_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class HermesEvent:
    """Safe lifecycle event with a stable id for replay and deduplication."""

    event_type: str
    request_id: str
    correlation_id: str
    event_id: str
    idempotency_key: str | None = None
    occurred_at: datetime = field(default_factory=_now)
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderOutput:
    """Provider result restricted to summary/evidence/index fields."""

    summary: Any = None
    evidence: tuple[Any, ...] = ()
    index: Any = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProviderOutput":
        unknown = set(value) - _SAFE_PROVIDER_KEYS
        if unknown:
            raise ValueError(
                f"provider output contains unsafe fields: {sorted(unknown)}"
            )

        def validate_nested(item: Any, location: str = "output") -> Any:
            if isinstance(item, Mapping):
                lowered = {str(key).lower() for key in item}
                sensitive = lowered & _SENSITIVE_KEYS
                if sensitive:
                    raise ValueError(
                        f"provider output contains unsafe nested fields at {location}: {sorted(sensitive)}"
                    )
                return {
                    key: validate_nested(val, f"{location}.{key}")
                    for key, val in item.items()
                }
            if isinstance(item, (list, tuple)):
                return tuple(
                    validate_nested(val, f"{location}[{index}]")
                    for index, val in enumerate(item)
                )
            return item

        raw_evidence = value.get("evidence", ())
        if isinstance(raw_evidence, (str, bytes)):
            raise ValueError("evidence must be a sequence")
        return cls(
            validate_nested(value.get("summary"), "summary"),
            tuple(
                validate_nested(item, f"evidence[{index}]")
                for index, item in enumerate(raw_evidence or ())
            ),
            validate_nested(value.get("index"), "index"),
        )


class Provider(Protocol):
    """Optional AI/content provider. It may return a ProviderOutput or mapping."""

    def generate(
        self, command: CreatorRunCommand, run: Mapping[str, Any]
    ) -> ProviderOutput | Mapping[str, Any]:
        """Return only safe summary, evidence and index fields."""


class EnqueueLink(Protocol):
    def __call__(
        self, urls: list[str], created_by: str, route: str, priority: int
    ) -> Any: ...


class CreateRun(Protocol):
    def __call__(
        self, target_id: int, run_type: str, input_cursor: str | None = None
    ) -> Mapping[str, Any]: ...


class HermesOrchestrator:
    """Thin orchestration layer delegating persistence to injected callbacks."""

    def __init__(
        self,
        enqueue_link: EnqueueLink,
        create_run: CreateRun,
        provider: Provider | None = None,
    ) -> None:
        self._enqueue_link = enqueue_link
        self._create_run = create_run
        self._provider = provider

    def submit_links(
        self, command: LinkCommand, meta: RequestMeta
    ) -> IntegrationEnvelope:
        result = self._enqueue_link(
            list(command.urls), command.created_by, command.route, command.priority
        )
        return IntegrationEnvelope(
            0, result, request_id=meta.request_id, correlation_id=meta.correlation_id
        )

    def create_creator_run(
        self, command: CreatorRunCommand, meta: RequestMeta
    ) -> IntegrationEnvelope:
        """Create a pending run only; workers invoke ``process_run`` later."""
        run = dict(
            self._create_run(command.target_id, command.run_type, command.input_cursor)
        )
        event = HermesEvent(
            "creator.run.created",
            meta.request_id,
            meta.correlation_id,
            event_id=f"{meta.idempotency_key or meta.request_id}:creator.run.created:{run.get('run_id', '')}",
            idempotency_key=meta.idempotency_key,
            data={"run_id": run.get("run_id")},
        )
        return IntegrationEnvelope(
            0,
            {"run": run, "event": event},
            request_id=meta.request_id,
            correlation_id=meta.correlation_id,
        )

    def process_run(
        self, command: CreatorRunCommand, run: Mapping[str, Any], meta: RequestMeta
    ) -> IntegrationEnvelope:
        """Invoke the optional provider from a worker context, never on trigger."""
        if self._provider is None:
            raise RuntimeError("provider is not configured")
        generated = (
            self._provider.generate(command, run)
            if hasattr(self._provider, "generate")
            else self._provider(command, run)
        )  # type: ignore[misc]
        safe = (
            generated
            if isinstance(generated, ProviderOutput)
            else ProviderOutput.from_mapping(generated)
        )
        return IntegrationEnvelope(
            0,
            {
                "summary": safe.summary,
                "evidence": list(safe.evidence),
                "index": safe.index,
            },
            request_id=meta.request_id,
            correlation_id=meta.correlation_id,
        )

    def history(
        self, target_id: int, meta: RequestMeta, input_cursor: str | None = None
    ) -> IntegrationEnvelope:
        return self.create_creator_run(
            CreatorRunCommand(target_id, "history_backfill", input_cursor), meta
        )

    def scan(
        self, target_id: int, meta: RequestMeta, input_cursor: str | None = None
    ) -> IntegrationEnvelope:
        return self.create_creator_run(
            CreatorRunCommand(target_id, "daily_scan", input_cursor), meta
        )


__all__ = [
    "CreateRun",
    "CreatorRunCommand",
    "EnqueueLink",
    "HermesEvent",
    "HermesOrchestrator",
    "IntegrationEnvelope",
    "LinkCommand",
    "Provider",
    "ProviderOutput",
    "RequestMeta",
    "RunType",
]
