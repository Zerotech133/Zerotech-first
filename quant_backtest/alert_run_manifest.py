"""Pure in-memory, fail-closed verification of an independently attested nine-dimension run.

This module does not execute the producer, authenticate its caller, mint an approval,
read files, or issue a production success certificate. Every trust root and every
schedule/threshold value must be supplied by an independent caller. The caller MUST
verify signatures/ACLs and independently pin both calendar snapshot hashes before
constructing these Python objects: a caller-created object is not cryptographic proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
import re
from typing import Mapping, Protocol


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_CODE = re.compile(r"[0-9]{6}\.(?:SH|SZ)\Z")
_EXCHANGES = ("SSE", "SZSE")
_SHANGHAI = timezone(timedelta(hours=8))  # CN exchanges have no modern DST.


@dataclass(frozen=True)
class ArtifactEvidence:
    """Independently observed final bytes digest and byte count."""

    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ApprovedSpec:
    """Externally approved policy; construction alone does not authenticate it."""

    spec_hash: str
    approved: bool
    revoked: bool
    task_id: str
    action: str
    args: tuple[str, ...]
    calendar_refs: tuple[tuple[str, str], ...]
    calendar_versions: tuple[tuple[str, str], ...]
    calendar_snapshot_hashes: tuple[tuple[str, str], ...]
    schedule_weekday: int
    window_start: time
    window_end: time
    next_due_time: time
    due_grace: timedelta
    max_lookback_days: int
    artifact_names: tuple[str, ...]
    max_artifact_bytes: int
    max_total_bytes: int
    min_candidates: int
    min_full_ratio: float
    min_cov: float
    alert_threshold: float
    strong_alert_threshold: float
    mid_threshold: float
    buy_threshold: float


@dataclass(frozen=True)
class NineDimManifest:
    """Untrusted run claim, never itself evidence of execution success."""

    run_id: str
    spec_hash: str
    calendar_versions: tuple[tuple[str, str], ...]
    calendar_snapshot_hashes: tuple[tuple[str, str], ...]
    task_id: str
    action: str
    args: tuple[str, ...]
    run_date: date
    service_date: date
    status: str = ""


@dataclass(frozen=True)
class CompletionReceipt:
    """Independent trusted runner's completion observation (not producer output)."""

    run_id: str
    spec_hash: str
    calendar_versions: tuple[tuple[str, str], ...]
    calendar_snapshot_hashes: tuple[tuple[str, str], ...]
    task_id: str
    action: str
    args: tuple[str, ...]
    run_date: date
    service_date: date
    completed_at: datetime
    exit_code: int
    final_artifacts: tuple[ArtifactEvidence, ...]
    history_mode: str
    new_alert_count: int
    prior_history_sha256: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    """Offline verification result; valid does not authorize publication."""

    valid: bool
    reason_code: str
    service_date: date | None = None
    completed_at: datetime | None = None
    valid_until: datetime | None = None


class Calendar(Protocol):
    """Pre-authenticated, complete CN exchange calendar supplied by caller."""

    @property
    def identity(self) -> object:
        """Return identity with exchange, market, timezone, version and coverage."""

    def is_open(self, session_date: date, *, as_of: datetime | None = None) -> bool:
        """Return explicit open/closed status, or raise on coverage gap."""

    def session_bounds(
        self, session_date: date, *, as_of: datetime | None = None
    ) -> object:
        """Return session with timezone-aware close_at."""


class ValidationFailure(ValueError):
    """Internal explicit failure transformed to a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValidationFailure(code)


def _aware(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _day(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _pin_pairs(value: object) -> bool:
    if not isinstance(value, tuple) or len(value) != len(_EXCHANGES):
        return False
    return all(
        isinstance(item, tuple)
        and len(item) == 2
        and item[0] == _EXCHANGES[index]
        and isinstance(item[1], str)
        and bool(item[1])
        for index, item in enumerate(value)
    )


def _identity(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _clock(value: object) -> bool:
    return isinstance(value, time) and value.tzinfo is None


def _spec(spec: ApprovedSpec) -> None:
    _require(isinstance(spec, ApprovedSpec), "SPEC_MISSING")
    _require(
        type(spec.approved) is bool
        and spec.approved
        and type(spec.revoked) is bool
        and not spec.revoked,
        "SPEC_NOT_APPROVED",
    )
    _require(
        isinstance(spec.spec_hash, str) and bool(_HASH.fullmatch(spec.spec_hash)),
        "SPEC_INVALID",
    )
    _require(
        all(_identity(item) for item in (spec.task_id, spec.action)), "SPEC_INVALID"
    )
    _require(
        isinstance(spec.args, tuple)
        and all(isinstance(arg, str) and arg for arg in spec.args),
        "SPEC_INVALID",
    )
    _require(
        spec.action != "--perf"
        and "--perf" not in spec.args
        and "--dryrun" not in spec.args,
        "SPEC_INVALID",
    )
    _require(
        _pin_pairs(spec.calendar_refs)
        and _pin_pairs(spec.calendar_versions)
        and _pin_pairs(spec.calendar_snapshot_hashes)
        and all(_identity(ref) for _, ref in spec.calendar_refs)
        and all(
            bool(_HASH.fullmatch(version))
            for _, version in spec.calendar_versions + spec.calendar_snapshot_hashes
        ),
        "SPEC_INVALID",
    )
    _require(
        type(spec.schedule_weekday) is int
        and 0 <= spec.schedule_weekday <= 6
        and all(
            _clock(value)
            for value in (spec.window_start, spec.window_end, spec.next_due_time)
        )
        and spec.window_start < spec.window_end,
        "SPEC_INVALID",
    )
    _require(
        isinstance(spec.due_grace, timedelta)
        and timedelta(0) <= spec.due_grace < timedelta(days=7)
        and type(spec.max_lookback_days) is int
        and 0 < spec.max_lookback_days <= 366,
        "SPEC_INVALID",
    )
    _require(
        isinstance(spec.artifact_names, tuple)
        and all(
            isinstance(name, str)
            and bool(_PATH.fullmatch(name))
            and name not in (".", "..")
            for name in spec.artifact_names
        ),
        "SPEC_INVALID",
    )
    _require(
        len(set(spec.artifact_names)) == len(spec.artifact_names)
        and {"nine_dim_scores.json", "nine_dim_pool_eval_cache.json"}.issubset(
            spec.artifact_names
        ),
        "SPEC_INVALID",
    )
    _require(
        type(spec.max_artifact_bytes) is int
        and type(spec.max_total_bytes) is int
        and 0 < spec.max_artifact_bytes <= spec.max_total_bytes
        and type(spec.min_candidates) is int
        and spec.min_candidates > 0
        and all(
            type(value) in (int, float) and math.isfinite(value)
            for value in (
                spec.min_full_ratio,
                spec.min_cov,
                spec.alert_threshold,
                spec.strong_alert_threshold,
                spec.mid_threshold,
                spec.buy_threshold,
            )
        )
        and 0 <= spec.min_full_ratio <= 1
        and 0 <= spec.min_cov <= 1
        and 0
        <= spec.mid_threshold
        < spec.alert_threshold
        < spec.strong_alert_threshold
        <= 100
        and 0 <= spec.buy_threshold <= 100,
        "SPEC_INVALID",
    )


def _calendar(calendars: Mapping[str, Calendar], spec: ApprovedSpec, day: date) -> None:
    _require(
        isinstance(calendars, Mapping) and set(calendars) == set(_EXCHANGES),
        "CALENDAR_MISSING",
    )
    for exchange in _EXCHANGES:
        _require(calendars[exchange] is not None, "CALENDAR_UNTRUSTED")
        identity = calendars[exchange].identity
        _require(
            identity is not None
            and getattr(identity, "exchange", None) == exchange
            and getattr(identity, "market", None) == "CN"
            and getattr(identity, "timezone", None) == "Asia/Shanghai"
            and getattr(identity, "version", None)
            == dict(spec.calendar_versions)[exchange]
            and getattr(identity, "snapshot_hash", None)
            == dict(spec.calendar_snapshot_hashes)[exchange]
            and getattr(identity, "calendar_ref", None)
            == dict(spec.calendar_refs)[exchange]
            and getattr(identity, "source_authenticated", None) is True
            and getattr(identity, "exception_completeness_verified", None) is True
            and getattr(identity, "production_eligible", None) is True,
            "CALENDAR_UNTRUSTED",
        )
        _require(
            _day(getattr(identity, "coverage_start", None))
            and _day(getattr(identity, "coverage_end", None))
            and identity.coverage_start <= day <= identity.coverage_end,
            "CALENDAR_COVERAGE_MISSING",
        )


def _service_date(
    calendars: Mapping[str, Calendar],
    spec: ApprovedSpec,
    run_date: date,
    completed_at: datetime,
) -> date:
    # Inspect only earlier dates, including the last permitted lookback day.
    # No weekend or fixed-holiday assumptions: examine BOTH explicit calendars.
    for offset in range(1, spec.max_lookback_days + 1):
        candidate = run_date - timedelta(days=offset)
        _calendar(calendars, spec, candidate)
        opened = [calendars[exchange].is_open(candidate) for exchange in _EXCHANGES]
        _require(all(type(flag) is bool for flag in opened), "CALENDAR_UNTRUSTED")
        _require(opened[0] == opened[1], "CALENDAR_EXCHANGE_MISMATCH")
        if opened[0]:
            closes: list[datetime] = []
            for exchange in _EXCHANGES:
                bounds = calendars[exchange].session_bounds(candidate)
                close_at = bounds.close_at
                _require(
                    _aware(close_at)
                    and getattr(bounds, "session_date", None) == candidate,
                    "CALENDAR_UNTRUSTED",
                )
                closes.append(close_at)
            if all(close_at <= completed_at for close_at in closes):
                return candidate
    raise ValidationFailure("CALENDAR_COVERAGE_MISSING")


def _json(data: bytes, code: str) -> dict[str, object]:
    """Decode one JSON object, rejecting duplicate keys at every nesting depth."""

    def reject_constant(value: str) -> float:
        raise ValueError(f"non-finite JSON constant: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            data.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, ValueError, TypeError) as exc:
        raise ValidationFailure(code) from exc
    _require(isinstance(value, dict), code)
    return value


def _number(value: object, low: float, high: float) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def _rounded_coverage_matches(cov: float, full: bool, threshold: float) -> bool:
    """Check whether a raw coverage can round to cov with the reported full flag.

    The producer decides full before round(cov, 2), so equality of displayed
    coverage and the threshold does not prove the raw value passed the gate.
    Probe the threshold and the adjacent representable floats on both sides of
    the rounding interval; each accepted witness obeys Python's actual round.
    """
    lower = cov - 0.005
    upper = cov + 0.005
    witnesses = (
        cov,
        threshold,
        math.nextafter(threshold, -math.inf),
        math.nextafter(threshold, math.inf),
        lower,
        math.nextafter(lower, math.inf),
        upper,
        math.nextafter(upper, -math.inf),
        (lower + upper) / 2,
    )
    return any(
        0 <= raw <= 1 and round(raw, 2) == cov and (raw >= threshold) is full
        for raw in witnesses
    )


def _snapshot(
    data: bytes,
    spec: ApprovedSpec,
    service_date: date,
    window_start: datetime,
    completed_at: datetime,
) -> int:
    snap = _json(data, "SNAPSHOT_INVALID")
    _require(
        snap.get("trade_date") == service_date.strftime("%Y%m%d"), "SNAPSHOT_STALE"
    )
    try:
        generated_at = datetime.fromisoformat(snap["generated_at"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValidationFailure("SNAPSHOT_TIME_INVALID") from exc
    _require(_aware(generated_at), "SNAPSHOT_TIME_INVALID")
    _require(window_start <= generated_at <= completed_at, "SNAPSHOT_STALE")
    _require(
        all(
            _number(snap.get(key), 0, 100) and snap[key] == expected
            for key, expected in (
                ("alert_th", spec.alert_threshold),
                ("buy_th", spec.buy_threshold),
            )
        ),
        "SNAPSHOT_POLICY_MISMATCH",
    )
    _require(
        _number(snap.get("min_cov"), 0, 1) and snap["min_cov"] == spec.min_cov,
        "SNAPSHOT_POLICY_MISMATCH",
    )
    rows = snap.get("rows")
    _require(
        isinstance(rows, list)
        and len(rows) <= spec.max_artifact_bytes
        and type(snap.get("n_cand")) is int
        and snap["n_cand"] == len(rows)
        and len(rows) >= spec.min_candidates,
        "SNAPSHOT_COUNT_INVALID",
    )
    codes: set[str] = set()
    full_count = buy_count = alert_count = 0
    for row in rows:
        _require(isinstance(row, dict), "SNAPSHOT_SCHEMA_INVALID")
        code = row.get("code")
        _require(
            isinstance(code, str) and bool(_CODE.fullmatch(code)) and code not in codes,
            "SNAPSHOT_CODE_INVALID",
        )
        codes.add(code)
        cov, score, full, band = (
            row.get(key) for key in ("cov", "score", "full", "band")
        )
        _require(
            _number(cov, 0, 1) and type(full) is bool and isinstance(band, str),
            "SNAPSHOT_SCHEMA_INVALID",
        )
        _require(
            _rounded_coverage_matches(cov, full, spec.min_cov),
            "SNAPSHOT_COVERAGE_INVALID",
        )
        # A rounded display value cannot attest which side of the raw threshold
        # was observed when both full states can round to the same value.
        _require(
            not (
                _rounded_coverage_matches(cov, False, spec.min_cov)
                and _rounded_coverage_matches(cov, True, spec.min_cov)
            ),
            "SNAPSHOT_COVERAGE_AMBIGUOUS",
        )
        _require(
            (_number(score, -math.inf, 100) if full else score is None),
            "SNAPSHOT_SCORE_INVALID",
        )
        expected_band = (
            "na"
            if score is None
            else "alert2"
            if score >= spec.strong_alert_threshold
            else "alert"
            if score >= spec.alert_threshold
            else "mid"
            if score >= spec.mid_threshold
            else "low"
        )
        _require(band == expected_band, "SNAPSHOT_BAND_INVALID")
        full_count += int(full)
        buy_count += int(score is not None and score >= spec.buy_threshold)
        alert_count += int(band in ("alert", "alert2"))
    _require(full_count / len(rows) >= spec.min_full_ratio, "SNAPSHOT_COVERAGE_INVALID")
    _require(
        all(
            type(snap.get(key)) is int and snap[key] == count
            for key, count in (
                ("n_full", full_count),
                ("n_buy", buy_count),
                ("n_alert", alert_count),
            )
        ),
        "SNAPSHOT_COUNT_INVALID",
    )
    return alert_count


def _evidence(
    artifacts: Mapping[str, bytes],
    receipt: CompletionReceipt,
    spec: ApprovedSpec,
    alert_count: int,
    window_start: datetime,
) -> None:
    _require(
        receipt.history_mode in ("written", "unchanged_no_new_alert")
        and type(receipt.new_alert_count) is int
        and 0 <= receipt.new_alert_count <= alert_count,
        "HISTORY_RECEIPT_INVALID",
    )
    _require(
        isinstance(receipt.final_artifacts, tuple)
        and all(isinstance(item, ArtifactEvidence) for item in receipt.final_artifacts),
        "ARTIFACT_RECEIPT_INVALID",
    )
    _require(
        all(
            isinstance(item.name, str)
            and bool(_PATH.fullmatch(item.name))
            and item.name not in (".", "..")
            for item in receipt.final_artifacts
        ),
        "ARTIFACT_PATH_INVALID",
    )
    history = "nine_dim_history.jsonl"
    _require(
        (receipt.new_alert_count > 0) == (receipt.history_mode == "written"),
        "HISTORY_RECEIPT_INVALID",
    )
    _require(
        isinstance(artifacts, Mapping)
        and all(
            isinstance(name, str)
            and bool(_PATH.fullmatch(name))
            and name not in (".", "..")
            for name in artifacts
        ),
        "ARTIFACT_PATH_INVALID",
    )
    _require(
        all(type(data) is bytes for data in artifacts.values()),
        "ARTIFACT_RECEIPT_INVALID",
    )
    _require(
        all(0 < len(data) <= spec.max_artifact_bytes for data in artifacts.values())
        and sum(len(data) for data in artifacts.values()) <= spec.max_total_bytes,
        "ARTIFACT_TOO_LARGE",
    )
    _require(
        history not in artifacts or history in spec.artifact_names,
        "ARTIFACT_UNAPPROVED",
    )
    _require(
        receipt.history_mode != "written" or history in artifacts,
        "HISTORY_RECEIPT_INVALID",
    )
    _require(
        receipt.history_mode != "unchanged_no_new_alert"
        or (
            history not in artifacts
            or (
                isinstance(receipt.prior_history_sha256, str)
                and bool(_HASH.fullmatch(receipt.prior_history_sha256))
                and hashlib.sha256(artifacts[history]).hexdigest()
                == receipt.prior_history_sha256
            )
        ),
        "HISTORY_RECEIPT_INVALID",
    )
    _require(
        set(artifacts) == {item.name for item in receipt.final_artifacts}
        and {"nine_dim_scores.json", "nine_dim_pool_eval_cache.json"}.issubset(
            artifacts
        )
        and set(artifacts).issubset(spec.artifact_names),
        "ARTIFACT_SET_INVALID",
    )
    _require(len(receipt.final_artifacts) == len(artifacts), "ARTIFACT_SET_INVALID")
    total = 0
    for evidence in receipt.final_artifacts:
        _require(
            isinstance(evidence, ArtifactEvidence)
            and isinstance(evidence.sha256, str)
            and bool(_HASH.fullmatch(evidence.sha256))
            and type(evidence.size) is int
            and 0 < evidence.size <= spec.max_artifact_bytes,
            "ARTIFACT_RECEIPT_INVALID",
        )
        data = artifacts[evidence.name]
        _require(
            type(data) is bytes
            and len(data) == evidence.size
            and hashlib.sha256(data).hexdigest() == evidence.sha256,
            "ARTIFACT_INTEGRITY_INVALID",
        )
        total += len(data)
        _require(total <= spec.max_total_bytes, "ARTIFACT_TOO_LARGE")
    eval_doc = _json(artifacts["nine_dim_pool_eval_cache.json"], "EVAL_INVALID")
    _require(
        type(eval_doc.get("n_pool")) is int
        and eval_doc["n_pool"] >= 0
        and isinstance(eval_doc.get("r5"), dict)
        and isinstance(eval_doc.get("r20"), dict),
        "EVAL_INVALID",
    )
    try:
        eval_generated = datetime.fromisoformat(eval_doc["generated_at"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValidationFailure("EVAL_TIME_INVALID") from exc
    _require(
        _aware(eval_generated)
        and window_start <= eval_generated <= receipt.completed_at,
        "EVAL_TIME_INVALID",
    )
    if receipt.history_mode == "unchanged_no_new_alert":
        _require(
            receipt.new_alert_count == 0
            and (
                history in artifacts or (eval_doc["n_pool"] == 0 and alert_count == 0)
            ),
            "HISTORY_RECEIPT_INVALID",
        )
        if history in artifacts:
            _history(artifacts[history], receipt, eval_doc["n_pool"], 0)
    if receipt.history_mode == "written":
        _history(
            artifacts[history], receipt, eval_doc["n_pool"], receipt.new_alert_count
        )


def _history(
    data: bytes, receipt: CompletionReceipt, n_pool: int, new_count: int
) -> None:
    """Validate the attested full historical ledger when bytes are available."""
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValidationFailure("HISTORY_INVALID") from exc
    entries = [_json(line.encode("utf-8"), "HISTORY_INVALID") for line in lines]
    _require(
        len(entries) == n_pool
        and len(entries) >= new_count
        and all(isinstance(row, dict) for row in entries),
        "HISTORY_INVALID",
    )
    seen: set[tuple[str, str]] = set()
    for row in entries:
        key = (row.get("code"), row.get("dt"))
        _require(
            isinstance(key[0], str)
            and bool(_CODE.fullmatch(key[0]))
            and isinstance(key[1], str)
            and bool(re.fullmatch(r"[0-9]{8}", key[1]))
            and key not in seen,
            "HISTORY_INVALID",
        )
        try:
            parsed_day = datetime.strptime(key[1], "%Y%m%d").date()
        except ValueError as exc:
            raise ValidationFailure("HISTORY_INVALID") from exc
        _require(parsed_day.strftime("%Y%m%d") == key[1], "HISTORY_INVALID")
        seen.add(key)
    _require(
        sum(day == receipt.service_date.strftime("%Y%m%d") for _, day in seen)
        >= new_count,
        "HISTORY_INVALID",
    )


def validate_nine_dim_run(
    *,
    manifest: NineDimManifest | None,
    artifacts: Mapping[str, bytes] | None,
    approved_spec: ApprovedSpec | None,
    calendar: Mapping[str, Calendar] | None,
    completion: CompletionReceipt | None,
    observed_at: datetime,
) -> ValidationResult:
    """Verify evidence against externally trusted roots; never attest producer origin.

    Any malformed dependency fails closed. The caller must verify external
    signatures/ACLs for the approved spec, pinned calendar snapshot hashes,
    completion runner and final byte capture BEFORE invoking this function.
    This function compares injected claims; it cannot verify signatures or origin.
    """
    try:
        _spec(approved_spec)
        spec = approved_spec
        _require(_aware(observed_at), "OBSERVED_TIME_INVALID")
        _require(isinstance(manifest, NineDimManifest), "MANIFEST_MISSING")
        _require(isinstance(completion, CompletionReceipt), "COMPLETION_MISSING")
        _require(
            _day(manifest.run_date)
            and _day(manifest.service_date)
            and _day(completion.run_date)
            and _day(completion.service_date)
            and _identity(manifest.run_id),
            "MANIFEST_INVALID",
        )
        _require(
            (
                manifest.run_id,
                manifest.spec_hash,
                manifest.calendar_versions,
                manifest.calendar_snapshot_hashes,
                manifest.task_id,
                manifest.action,
                manifest.args,
                manifest.run_date,
                manifest.service_date,
            )
            == (
                completion.run_id,
                completion.spec_hash,
                completion.calendar_versions,
                completion.calendar_snapshot_hashes,
                completion.task_id,
                completion.action,
                completion.args,
                completion.run_date,
                completion.service_date,
            ),
            "RECEIPT_BINDING_MISMATCH",
        )
        _require(
            manifest.spec_hash == spec.spec_hash
            and manifest.calendar_versions == spec.calendar_versions
            and manifest.calendar_snapshot_hashes == spec.calendar_snapshot_hashes
            and (manifest.task_id, manifest.action, manifest.args)
            == (spec.task_id, spec.action, spec.args),
            "POLICY_BINDING_MISMATCH",
        )
        _require(
            type(completion.exit_code) is int and completion.exit_code == 0,
            "COMPLETION_FAILED",
        )
        _require(_aware(completion.completed_at), "COMPLETION_TIME_INVALID")
        completed = completion.completed_at.astimezone(_SHANGHAI)
        observed = observed_at.astimezone(_SHANGHAI)
        _require(completed <= observed, "COMPLETION_FUTURE")
        run_day = manifest.run_date
        _require(run_day.weekday() == spec.schedule_weekday, "RUN_DATE_INVALID")
        begin = datetime.combine(run_day, spec.window_start, _SHANGHAI)
        end = datetime.combine(run_day, spec.window_end, _SHANGHAI)
        valid_until = (
            datetime.combine(run_day + timedelta(days=7), spec.next_due_time, _SHANGHAI)
            + spec.due_grace
        )
        _require(begin <= completed <= end, "COMPLETION_OUTSIDE_WINDOW")
        _require(observed < valid_until, "NEXT_RUN_OVERDUE")
        _require(calendar is not None, "CALENDAR_MISSING")
        _calendar(calendar, spec, run_day)
        service = _service_date(calendar, spec, run_day, completed)
        _require(manifest.service_date == service, "SERVICE_DATE_MISMATCH")
        _require(
            artifacts is not None and isinstance(artifacts, Mapping),
            "ARTIFACT_SET_INVALID",
        )
        _require(
            all(
                isinstance(name, str)
                and bool(_PATH.fullmatch(name))
                and name not in (".", "..")
                for name in artifacts
            ),
            "ARTIFACT_PATH_INVALID",
        )
        _require("nine_dim_scores.json" in artifacts, "ARTIFACT_SET_INVALID")
        _require(
            type(artifacts["nine_dim_scores.json"]) is bytes, "ARTIFACT_RECEIPT_INVALID"
        )
        _require(
            len(artifacts["nine_dim_scores.json"]) <= spec.max_artifact_bytes,
            "ARTIFACT_TOO_LARGE",
        )
        alert_count = _snapshot(
            artifacts["nine_dim_scores.json"], spec, service, begin, completed
        )
        _evidence(artifacts, completion, spec, alert_count, begin)
        return ValidationResult(
            True,
            "SYNTHETIC_OR_EXTERNALLY_ATTESTED_VALID",
            service,
            completion.completed_at,
            valid_until,
        )
    except ValidationFailure as exc:
        return ValidationResult(False, exc.reason_code)
    except Exception:
        # A calendar exception, odd mapping, or hostile descriptor never becomes success.
        return ValidationResult(False, "DEPENDENCY_INVALID")
