"""Pure-memory structural validation of a weekly premarket completion receipt.

The caller must acquire and authenticate the approval, calendar snapshots, runner
observation, and before/after raw bytes independently of ``premarket.py``. This
module has no filesystem, scheduler, publication, or cryptographic trust root.
Even a valid synthetic fixture NEVER conveys production permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Mapping, Protocol


SHANGHAI = timezone(timedelta(hours=8))
ARTIFACT_NAMES = ("premarket_latest.json", "premarket_history.jsonl", "premarket.html")
EXCHANGES = ("SSE", "SZSE")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TS = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}\Z")


@dataclass(frozen=True)
class PremarketApproval:
    """Separately authorized weekly policy; constructing it is not approval."""

    approved: bool
    revoked: bool
    spec_hash: str
    task_id: str
    action: str
    args: tuple[str, ...]
    period: str
    service_date_policy: str
    service_date_policy_version: str
    schedule_weekday: int
    window_start: time
    window_end: time
    next_due_time: time
    grace: timedelta
    max_artifact_bytes: int
    calendar_refs: tuple[tuple[str, str], ...]
    calendar_versions: tuple[tuple[str, str], ...]
    calendar_snapshot_hashes: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PremarketClaim:
    """Untrusted producer claim, never independent completion evidence."""

    run_id: str
    spec_hash: str
    task_id: str
    action: str
    args: tuple[str, ...]
    run_date: date
    service_date: date
    status: str
    service_date_policy: str = ""
    service_date_policy_version: str = ""
    force: bool = False


@dataclass(frozen=True)
class ByteEvidence:
    """Independent before/after raw byte digest and size for one artifact."""

    name: str
    before_sha256: str | None
    before_size: int | None
    after_sha256: str
    after_size: int


@dataclass(frozen=True)
class PremarketReceipt:
    """Trusted runner's externally sourced exit and final byte observations.

    A producer-signed receipt, a constant-true verifier callback, or an mtime is
    not an independent observation. Caller must pin and authenticate this object.
    """

    run_id: str
    spec_hash: str
    task_id: str
    action: str
    args: tuple[str, ...]
    run_date: date
    service_date: date
    runner_id: str
    observation_origin: str
    started_at: datetime
    completed_at: datetime
    exit_code: int
    skipped: bool
    forced: bool
    artifacts: tuple[ByteEvidence, ...]
    service_date_policy: str = ""
    service_date_policy_version: str = ""


@dataclass(frozen=True)
class PremarketVerdict:
    """Offline structural verdict, never a production authorization."""

    valid: bool
    reason_code: str
    service_date: date | None = None
    valid_until: datetime | None = None
    production_permitted: bool = False


class Calendar(Protocol):
    """Caller-authenticated exchange calendar with explicit covered sessions."""

    @property
    def identity(self) -> object:
        """Expose exchange, market, timezone, references, hashes and coverage."""
        raise NotImplementedError

    def is_open(self, session_date: date) -> bool:
        """Return an actual observed boolean, or raise if uncovered."""
        raise NotImplementedError


class InvalidEvidence(ValueError):
    """A stable structural refusal code."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InvalidEvidence(reason)


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _day(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _pins(value: object, hashes: bool = False) -> bool:
    return (isinstance(value, tuple) and len(value) == 2
            and all(isinstance(pair, tuple) and len(pair) == 2
                    and pair[0] == EXCHANGES[index] and isinstance(pair[1], str)
                    and bool((_HASH if hashes else _ID).fullmatch(pair[1]))
                    for index, pair in enumerate(value)))


def _json(data: bytes, reason: str) -> dict[str, object]:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError("non-finite JSON value: " + value)

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=unique,
                            parse_constant=reject_constant)
    except (UnicodeError, ValueError, TypeError) as exc:
        raise InvalidEvidence(reason) from exc
    _require(isinstance(result, dict), reason)
    return result


def _validate_approval(spec: object) -> PremarketApproval:
    _require(isinstance(spec, PremarketApproval), "APPROVAL_MISSING")
    _require(spec.approved is True and spec.revoked is False
             and isinstance(spec.spec_hash, str) and bool(_HASH.fullmatch(spec.spec_hash))
             and all(isinstance(value, str) and bool(_ID.fullmatch(value))
                     for value in (spec.task_id, spec.action)), "APPROVAL_INVALID")
    _require(isinstance(spec.args, tuple) and all(isinstance(arg, str) and arg
             for arg in spec.args) and all(not arg.startswith(
                 ("--force", "--dry-run", "--dryrun", "--perf"))
                 for arg in spec.args)
             and spec.period == "weekly"
             and spec.service_date_policy == "next_calendar_day_both_exchanges_open"
             and isinstance(spec.service_date_policy_version, str)
             and bool(_ID.fullmatch(spec.service_date_policy_version))
             and type(spec.schedule_weekday) is int and 0 <= spec.schedule_weekday <= 6
             and all(isinstance(clock, time) and clock.tzinfo is None
                     for clock in (spec.window_start, spec.window_end, spec.next_due_time))
             and spec.window_start < spec.window_end
             and isinstance(spec.grace, timedelta) and timedelta(0) <= spec.grace < timedelta(days=7)
             and type(spec.max_artifact_bytes) is int and 0 < spec.max_artifact_bytes <= 50_000_000
             and _pins(spec.calendar_refs) and _pins(spec.calendar_versions, True)
             and _pins(spec.calendar_snapshot_hashes, True), "APPROVAL_INVALID")
    return spec


def _open_pair(calendar: Mapping[str, Calendar], spec: PremarketApproval,
               day: date) -> bool:
    _require(isinstance(calendar, Mapping) and set(calendar) == set(EXCHANGES),
             "CALENDAR_MISSING")
    states: list[bool] = []
    for exchange in EXCHANGES:
        cal = calendar[exchange]
        identity = cal.identity
        _require(identity is not None and getattr(identity, "exchange", None) == exchange
                 and getattr(identity, "market", None) == "CN"
                 and getattr(identity, "timezone", None) == "Asia/Shanghai"
                 and getattr(identity, "calendar_ref", None) == dict(spec.calendar_refs)[exchange]
                 and getattr(identity, "version", None) == dict(spec.calendar_versions)[exchange]
                 and getattr(identity, "snapshot_hash", None) ==
                 dict(spec.calendar_snapshot_hashes)[exchange]
                 and getattr(identity, "source_authenticated", None) is True
                 and getattr(identity, "exception_completeness_verified", None) is True
                 and getattr(identity, "production_eligible", None) is True,
                 "CALENDAR_UNTRUSTED")
        start = getattr(identity, "coverage_start", None)
        end = getattr(identity, "coverage_end", None)
        _require(_day(start) and _day(end) and start <= day <= end,
                 "CALENDAR_COVERAGE_MISSING")
        state = cal.is_open(day)
        _require(type(state) is bool, "CALENDAR_UNTRUSTED")
        states.append(state)
    _require(states[0] == states[1], "CALENDAR_EXCHANGE_MISMATCH")
    return states[0]


def _validate_bytes(artifacts: Mapping[str, bytes], previous: Mapping[str, bytes | None],
                    receipt: PremarketReceipt, limit: int, run_day: date,
                    started: datetime, completed: datetime) -> None:
    _require(isinstance(artifacts, Mapping) and isinstance(previous, Mapping)
             and set(artifacts) == set(ARTIFACT_NAMES)
             and set(previous) == set(ARTIFACT_NAMES)
             and isinstance(receipt.artifacts, tuple)
             and len(receipt.artifacts) == len(ARTIFACT_NAMES)
             and all(isinstance(entry, ByteEvidence) for entry in receipt.artifacts)
             and {entry.name for entry in receipt.artifacts} == set(ARTIFACT_NAMES),
             "ARTIFACT_SET_INVALID")
    for item in receipt.artifacts:
        before = previous[item.name]
        after = artifacts[item.name]
        _require((before is None or type(before) is bytes) and type(after) is bytes
                 and 0 < len(after) <= limit and (before is None or len(before) <= limit),
                 "ARTIFACT_BYTES_INVALID")
        _require(type(item.after_size) is int and item.after_size == len(after)
                 and isinstance(item.after_sha256, str) and
                 hashlib.sha256(after).hexdigest() == item.after_sha256
                 and ((before is None and item.before_sha256 is None and item.before_size is None)
                      or (before is not None and type(item.before_size) is int
                          and item.before_size == len(before)
                          and isinstance(item.before_sha256, str)
                          and hashlib.sha256(before).hexdigest() == item.before_sha256)),
                 "ARTIFACT_BINDING_MISMATCH")
        _require(before != after, "ARTIFACT_UNCHANGED")
    latest = _json(artifacts[ARTIFACT_NAMES[0]], "LATEST_INVALID")
    stamp = latest.get("ts")
    _require(isinstance(stamp, str) and bool(_TS.fullmatch(stamp)), "LATEST_TIME_UNTRUSTED")
    try:
        local_ts = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=SHANGHAI)
    except ValueError as exc:
        raise InvalidEvidence("LATEST_TIME_UNTRUSTED") from exc
    # Legacy minute-resolution ts is a display consistency check only; it cannot
    # attest the completion time supplied independently by the trusted runner.
    _require(started.replace(second=0, microsecond=0) <= local_ts
             <= completed.replace(second=0, microsecond=0)
             and local_ts.date() == run_day, "LATEST_TIME_UNTRUSTED")
    overall, prob, verdict, vals, alerts = (latest.get(key) for key in
                                            ("overall", "prob", "verdict", "vals", "alerts"))
    _require(type(overall) in (int, float) and math.isfinite(overall)
             and type(prob) is int and 0 <= prob <= 100
             and isinstance(verdict, str) and bool(verdict)
             and isinstance(vals, dict) and any(type(value) in (int, float)
             and math.isfinite(value) for value in vals.values())
             and isinstance(alerts, list) and all(isinstance(value, str) for value in alerts),
             "LATEST_EMPTY_OR_INVALID")
    history = artifacts[ARTIFACT_NAMES[1]]
    _require(history.endswith(b"\n"), "HISTORY_INVALID")
    rows = [_json(line, "HISTORY_INVALID") for line in history.splitlines()]
    _require(len(rows) > 0, "HISTORY_INVALID")
    days: list[str] = []
    for row in rows:
        raw_day = row.get("date")
        _require(isinstance(raw_day, str) and bool(re.fullmatch(r"\d{8}", raw_day)),
                 "HISTORY_INVALID")
        try:
            parsed = datetime.strptime(raw_day, "%Y%m%d").date()
        except ValueError as exc:
            raise InvalidEvidence("HISTORY_INVALID") from exc
        _require(parsed.strftime("%Y%m%d") == raw_day and raw_day not in days
                 and parsed <= run_day, "HISTORY_INVALID")
        days.append(raw_day)
    today = run_day.strftime("%Y%m%d")
    _require(rows[-1].get("date") == today and rows[-1].get("overall") == overall
             and rows[-1].get("prob") == prob and rows[-1].get("verdict") == verdict
             and rows[-1].get("vals") == vals and rows[-1].get("alerts") == len(alerts),
             "HISTORY_LATEST_MISMATCH")
    prior = previous[ARTIFACT_NAMES[1]]
    if prior is not None:
        _require(not prior or prior.endswith(b"\n"), "HISTORY_PRIOR_INVALID")
        prior_lines = prior.splitlines(keepends=True)
        prior_rows = [_json(line, "HISTORY_PRIOR_INVALID") for line in prior_lines]
        prior_days = [entry.get("date") for entry in prior_rows]
        _require(all(isinstance(value, str) and bool(re.fullmatch(r"\d{8}", value))
                     for value in prior_days) and len(set(prior_days)) == len(prior_days)
                 and all(datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value
                         for value in prior_days)
                 and all(value <= today for value in prior_days)
                 and [line for line, row in zip(prior_lines, prior_rows)
                      if row.get("date") != today] == history.splitlines(keepends=True)[:-1],
                 "HISTORY_PRIOR_MISMATCH")
    else:
        _require(len(rows) == 1, "HISTORY_PRIOR_MISSING")
    try:
        page = artifacts[ARTIFACT_NAMES[2]].decode("utf-8")
    except UnicodeError as exc:
        raise InvalidEvidence("HTML_INVALID") from exc
    _require("盘前分析" in page and stamp in page and str(overall) in page,
             "HTML_LATEST_MISMATCH")


def validate_premarket_run(*, claim: PremarketClaim | None,
                           approval: PremarketApproval | None,
                           receipt: PremarketReceipt | None,
                           artifacts: Mapping[str, bytes] | None,
                           previous: Mapping[str, bytes | None] | None,
                           calendar: Mapping[str, Calendar] | None,
                           observed_at: datetime) -> PremarketVerdict:
    """Check independent weekly evidence, failing closed on malformed dependencies.

    The caller MUST separately establish the authority of approval, calendar and
    runner and independently acquire BEFORE and AFTER bytes. A synthetic success
    only means those injected objects are structurally consistent.
    """
    try:
        spec = _validate_approval(approval)
        _require(isinstance(claim, PremarketClaim) and isinstance(receipt, PremarketReceipt),
                 "CLAIM_OR_RECEIPT_MISSING")
        _require(_aware(observed_at) and _aware(receipt.started_at)
                 and _aware(receipt.completed_at), "TIME_INVALID")
        _require(_day(claim.run_date) and _day(claim.service_date)
                 and _day(receipt.run_date) and _day(receipt.service_date)
                 and isinstance(claim.run_id, str) and bool(_ID.fullmatch(claim.run_id)),
                 "CLAIM_INVALID")
        _require((claim.run_id, claim.spec_hash, claim.task_id, claim.action,
                  claim.args, claim.run_date, claim.service_date,
                  claim.service_date_policy, claim.service_date_policy_version) ==
                 (receipt.run_id, receipt.spec_hash, receipt.task_id, receipt.action,
                  receipt.args, receipt.run_date, receipt.service_date,
                  receipt.service_date_policy, receipt.service_date_policy_version),
                 "RECEIPT_CLAIM_MISMATCH")
        _require((claim.spec_hash, claim.task_id, claim.action, claim.args,
                  claim.service_date_policy, claim.service_date_policy_version) ==
                 (spec.spec_hash, spec.task_id, spec.action, spec.args,
                  spec.service_date_policy, spec.service_date_policy_version),
                 "APPROVAL_BINDING_MISMATCH")
        _require(claim.status == "completed" and claim.force is False
                 and receipt.skipped is False and receipt.forced is False
                 and type(receipt.exit_code) is int and receipt.exit_code == 0,
                 "RUN_NOT_COMPLETED")
        _require(isinstance(receipt.runner_id, str) and bool(_ID.fullmatch(receipt.runner_id))
                 and receipt.observation_origin == "independent_runner",
                 "RUNNER_UNTRUSTED")
        started = receipt.started_at.astimezone(SHANGHAI)
        completed = receipt.completed_at.astimezone(SHANGHAI)
        observed = observed_at.astimezone(SHANGHAI)
        _require(started <= completed <= observed, "TIME_ORDER_INVALID")
        run_day = claim.run_date
        # The next-calendar-day rule is admissible only because the independently
        # approved, versioned policy was bound to both claim and runner receipt.
        _require(run_day.weekday() == spec.schedule_weekday and
                 spec.service_date_policy == "next_calendar_day_both_exchanges_open"
                 and claim.service_date == run_day + timedelta(days=1),
                 "SERVICE_DATE_INVALID")
        window_start = datetime.combine(run_day, spec.window_start, SHANGHAI)
        window_end = datetime.combine(run_day, spec.window_end, SHANGHAI)
        valid_until = (datetime.combine(run_day + timedelta(days=7),
                                        spec.next_due_time, SHANGHAI) + spec.grace)
        _require(window_start <= started <= completed <= window_end,
                 "RUN_OUTSIDE_WINDOW")
        _require(observed < valid_until, "NEXT_RUN_OVERDUE")
        _require(calendar is not None and _open_pair(calendar, spec, run_day)
                 and _open_pair(calendar, spec, claim.service_date),
                 "CLOSED_SESSION")
        _validate_bytes(artifacts, previous, receipt, spec.max_artifact_bytes,
                        run_day, started, completed)
        return PremarketVerdict(True, "SYNTHETIC_STRUCTURALLY_CONSISTENT",
                                claim.service_date, valid_until)
    except InvalidEvidence as exc:
        return PremarketVerdict(False, str(exc))
    except Exception:
        return PremarketVerdict(False, "DEPENDENCY_INVALID")
