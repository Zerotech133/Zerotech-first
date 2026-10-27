"""Advisory history-backfill priority and gap alerts over evidence bundles.

read-only policy: every function here is a pure function over caller-supplied
dicts shaped like the output of ``xq_acceptance_evidence.export_evidence()``.
This module performs no I/O: it issues no SQL, opens no sockets, and writes
nothing to disk or to any database. It never rewrites collection watermarks
and never triggers or schedules collection. Data-lake aggregation progress
must never trigger or rewrite collection watermarks (PRD P2 boundary); this
module does not read data-lake progress at all and therefore cannot affect
watermarks in either direction.

closed on failure: every invalid input raises ``ValueError`` before any
result is produced. A partial score, a partial alert list, or a partially
ranked target list is never returned.

Output is advisory only (``recommendation_only``): every ``ScoreResult``
carries ``advisory=True`` and is a recommendation for human or downstream
review, not a dispatch or scheduling instruction. Scores are explainable
weighted sums; each contributing factor is listed in ``reasons`` so the
number can be audited factor by factor. Missing external observations
(``marginal_output_hint``) stay neutral and are marked as missing; no value
is ever fabricated for them.

``trend_inputs`` is exported only as input material for long-run operations
trend aggregation. Trend aggregation is downstream work and is explicitly
``not_linked`` here; this module stores no time series of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Weight added when ``gaps.type == 'history_unfinished'`` carries a
#: confirmed ``next_cursor``: the backlog can be resumed immediately, so
#: the marginal output of the next history pages is expected to be high.
GAP_UNFINISHED_WITH_CURSOR_WEIGHT: float = 3.0

#: Weight added when history is demonstrably unfinished but the resume key
#: is unknown (for example a task waiting on a cursor request).
GAP_UNFINISHED_NO_CURSOR_WEIGHT: float = 1.0

#: Weight applied to the caller-supplied marginal-output observation.
MARGINAL_OUTPUT_WEIGHT: float = 1.0

#: Penalty applied when recent failures are dense in 403/timeout risk
#: signals; avoids wasting the request budget on a stock that is still in
#: risk-control cooling.
RISK_COOLING_PENALTY: float = -2.0

#: Penalty applied when coverage attribution is blocked; such stocks never
#: appear near the top of the advisory ranking.
ATTRIBUTION_BLOCKED_PENALTY: float = -100.0

#: How many of the most recent failures (newest first, as exported by
#: ``export_evidence()``) are inspected for risk-cooling signals.
FAILURE_WINDOW: int = 3

#: Risk-cooling density threshold over the failure window: a stock counts
#: as risk-cooling when at least 2/3 of the window is 403/timeout.
RISK_DENSE_NUMERATOR: int = 2
RISK_DENSE_DENOMINATOR: int = 3

#: Finite alert-type enum. Unknown labels are closed on failure.
ALERT_TYPES: tuple[str, ...] = (
    "history_unfinished",
    "attribution_blocked",
    "collection_paused",
    "risk_cooling",
)

#: Finite severity enum.
ALERT_SEVERITIES: tuple[str, ...] = ("info", "warning")

#: Key fields every input evidence bundle must carry. Missing fields are
#: closed on failure with ``ValueError`` instead of being invented.
REQUIRED_BUNDLE_KEYS: tuple[str, ...] = ("symbol", "lane", "coverage", "gaps")

#: Marker echoed into every ``ScoreResult.trend_inputs``: long-run trend
#: aggregation is downstream work and is not wired here.
TREND_AGGREGATION: str = "not_linked"


def _required_string(value: Any, label: str) -> str:
    """Require an exact nonempty identifier, without normalization."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _required_budget_units(value: Any) -> int:
    """Require a positive int budget; booleans are not counts.

    closed on failure: ``budget_units == 0``, negative values, booleans and
    non-integers all raise ``ValueError`` instead of producing a partial
    score.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("budget_units must be a positive integer")
    return value


def _optional_marginal_output_hint(value: Any) -> float | None:
    """Return the hint as a finite nonnegative float, or None when absent.

    closed on failure: booleans, strings, negative numbers, NaN and
    infinities all raise ``ValueError``. ``None`` means the observation is
    missing and is handled as neutral by the scorer, never fabricated.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("marginal_output_hint must be a nonnegative number or None")
    hint = float(value)
    if not (0.0 <= hint < float("inf")):
        raise ValueError("marginal_output_hint must be a nonnegative number or None")
    return hint


def _required_cursor(gaps: dict[str, Any]) -> str | None:
    """Validate ``gaps.next_cursor`` for an unfinished-history gap.

    When ``gaps.type == 'history_unfinished'`` the resume key may be
    ``None`` (the no-cursor branch). Any non-``None`` value must be a
    string that is nonempty after stripping; an empty or whitespace-only
    cursor is malformed and is never reinterpreted as a resumable cursor.

    closed on failure: a blank or non-string ``next_cursor`` raises
    ``ValueError`` instead of being accepted as "has a cursor". The value
    is only validated, never rewritten.

    Returns:
        The validated cursor: ``None`` or a nonempty string.

    Raises:
        ValueError: ``next_cursor`` is a blank string or not a string
            while ``gaps.type == 'history_unfinished'``. Closed on
            failure.
    """
    if gaps.get("type") != "history_unfinished":
        return None
    cursor = gaps.get("next_cursor")
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not cursor.strip():
        raise ValueError("gaps.next_cursor must be None or a nonempty string")
    return cursor


def _required_bundle(bundle: Any) -> dict[str, Any]:
    """Validate the evidence-bundle shape without rewriting any field.

    The bundle must be a dict carrying ``symbol``, ``lane``, ``coverage``
    and ``gaps``. ``coverage`` must be a dict with a nonempty ``status``
    string and ``gaps`` a dict with a nonempty ``type`` string so alerts
    and factors can quote them faithfully. On an unfinished-history gap,
    ``gaps.next_cursor`` must be ``None`` or a nonempty string (see
    :func:`_required_cursor`); a blank cursor is closed on failure and is
    never treated as resumable. Optional sections are validated
    too when present: ``failures`` must be a list of dicts, ``pending`` a
    dict, ``watermark`` a dict. Anything else is closed on failure with
    ``ValueError``.
    """
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be a dict")
    for key in REQUIRED_BUNDLE_KEYS:
        if key not in bundle:
            raise ValueError(f"bundle must contain '{key}'")
    _required_string(bundle["symbol"], "symbol")
    _required_string(bundle["lane"], "lane")
    coverage = bundle["coverage"]
    if not isinstance(coverage, dict) or "status" not in coverage:
        raise ValueError("coverage must be a dict with a status key")
    _required_string(coverage["status"], "coverage status")
    gaps = bundle["gaps"]
    if not isinstance(gaps, dict) or "type" not in gaps:
        raise ValueError("gaps must be a dict with a type key")
    _required_string(gaps["type"], "gaps type")
    _required_cursor(gaps)
    failures = bundle.get("failures", [])
    if not isinstance(failures, list) or any(
        not isinstance(item, dict) for item in failures
    ):
        raise ValueError("failures must be a list of dicts when present")
    pending = bundle.get("pending", {})
    if not isinstance(pending, dict):
        raise ValueError("pending must be a dict when present")
    watermark = bundle.get("watermark", {})
    if not isinstance(watermark, dict):
        raise ValueError("watermark must be a dict when present")
    return bundle


def _fmt_factor(value: float) -> str:
    """Format one factor contribution with an explicit sign."""
    return f"{value:+.1f}"


def _is_risk_status(status: Any) -> bool:
    """Recognize 403 / timeout risk-control signals in a failure status."""
    text = str(status).lower()
    return "403" in text or "timeout" in text


def _recent_failures(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the newest failure window; the bundle is never mutated.

    ``export_evidence()`` lists failures newest first, so the head of the
    list is the most recent attempt sample. Only ``FAILURE_WINDOW`` items
    are inspected.
    """
    failures = bundle.get("failures", [])
    return list(failures[:FAILURE_WINDOW])


def _last_fetched_at(bundle: dict[str, Any]) -> Any:
    """Echo the watermark fetch time when recorded, else None.

    Only referenced, never invented: a bundle without a watermark simply
    reports ``None`` in ``trend_inputs``.
    """
    watermark = bundle.get("watermark")
    if not isinstance(watermark, dict):
        return None
    return watermark.get("fetched_at")


@dataclass(frozen=True)
class ScoreResult:
    """One explainable advisory priority score for a stock and lane.

    ``priority_score`` is a plain weighted sum of the documented factors
    divided by ``budget_units``; every factor contributes one entry to
    ``reasons`` so the arithmetic is auditable. ``advisory`` is always
    ``True``: the result is a recommendation only (``recommendation_only``)
    and never constitutes a dispatch or scheduling instruction.
    ``trend_inputs`` carries raw operations-trend material for downstream
    aggregation, which is ``not_linked`` here.
    """

    symbol: str
    lane: str
    priority_score: float
    reasons: list[str]
    trend_inputs: dict[str, Any] = field(default_factory=dict)
    advisory: bool = True

    def __post_init__(self) -> None:
        """Validate every field at construction; closed on failure."""
        _required_string(self.symbol, "symbol")
        _required_string(self.lane, "lane")
        if isinstance(self.priority_score, bool) or not isinstance(
            self.priority_score, (int, float)
        ):
            raise ValueError("priority_score must be a number")
        if not isinstance(self.reasons, list) or any(
            not isinstance(item, str) for item in self.reasons
        ):
            raise ValueError("reasons must be a list of strings")
        if not isinstance(self.trend_inputs, dict):
            raise ValueError("trend_inputs must be a dict")
        if self.advisory is not True:
            raise ValueError("advisory is always True; results are recommendation_only")


@dataclass(frozen=True)
class GapAlert:
    """One explainable gap alert for a stock and lane.

    ``alert_type`` is drawn from the finite ``ALERT_TYPES`` enum and
    ``severity`` from ``ALERT_SEVERITIES``. ``evidence`` echoes the raw
    values that triggered the judgment (gap type, pending state, recent
    failure statuses); the values are only referenced, never rewritten.
    """

    symbol: str
    lane: str
    alert_type: str
    severity: str
    detail: str
    evidence: dict[str, Any]

    def __post_init__(self) -> None:
        """Validate every field at construction; closed on failure."""
        _required_string(self.symbol, "symbol")
        _required_string(self.lane, "lane")
        if self.alert_type not in ALERT_TYPES:
            raise ValueError(f"alert_type must be one of {ALERT_TYPES}")
        if self.severity not in ALERT_SEVERITIES:
            raise ValueError(f"severity must be one of {ALERT_SEVERITIES}")
        _required_string(self.detail, "detail")
        if not isinstance(self.evidence, dict):
            raise ValueError("evidence must be a dict")


def score_history_backlog(
    bundle: dict[str, Any],
    *,
    marginal_output_hint: float | None = None,
    budget_units: int = 1,
) -> ScoreResult:
    """Score one stock-lane backlog as an explainable advisory priority.

    The score is the sum of four documented factors divided by
    ``budget_units``:

    - gap size: ``gaps.type == 'history_unfinished'`` with a confirmed
      nonempty ``next_cursor`` adds ``GAP_UNFINISHED_WITH_CURSOR_WEIGHT``
      (resumable backlog, marginal output expected); unfinished history
      without a cursor adds the smaller ``GAP_UNFINISHED_NO_CURSOR_WEIGHT``;
      an ``unknown`` gap adds nothing. A blank or non-string
      ``next_cursor`` is closed on failure at bundle validation and is
      never reinterpreted as a resumable cursor.
    - marginal output: the caller-supplied ``marginal_output_hint`` (for
      example recent new posts per page) is scaled by
      ``MARGINAL_OUTPUT_WEIGHT``. When the hint is ``None`` the factor is
      exactly neutral (0.0) and ``reasons`` records that the hint is
      missing; no number is fabricated.
    - failure cost: when at least 2/3 of the most recent
      ``FAILURE_WINDOW`` failures are 403/timeout risk signals, the
      ``RISK_COOLING_PENALTY`` demotes the stock so budget is not wasted
      on a risk-control cooling period; ``reasons`` records the counts.
    - attribution: ``coverage.status == 'blocked'`` adds the large
      ``ATTRIBUTION_BLOCKED_PENALTY`` so blocked stocks never sit at the
      top of the ranking; ``reasons`` states "attribution blocked".

    This function is read-only and closed on failure: it performs no I/O,
    never mutates the input bundle, and raises ``ValueError`` before any
    result is produced when the bundle, the hint, or ``budget_units`` is
    invalid. The returned score is advisory only (``recommendation_only``);
    it never rewrites collection watermarks and never triggers collection.
    Data-lake aggregation progress is not an input here and must never be
    used to rewrite collection watermarks.

    Args:
        bundle: The ``export_evidence()`` result dict for one stock and
            lane; must carry ``symbol``, ``lane``, ``coverage`` and
            ``gaps``.
        marginal_output_hint: Optional external observation of marginal
            output (nonnegative, finite). ``None`` means missing.
        budget_units: Positive int risk-control budget used as the score
            divisor. Booleans are rejected.

    Returns:
        A ``ScoreResult`` with ``advisory=True``, an auditable ``reasons``
        list, and ``trend_inputs`` whose ``trend_aggregation`` marker is
        ``not_linked``.

    Raises:
        ValueError: The bundle is malformed (including a blank
            ``gaps.next_cursor`` on an unfinished-history gap), the hint
            is not a finite nonnegative number, or ``budget_units`` is not
            a positive int. Closed on failure; no partial score.
    """
    data = _required_bundle(bundle)
    budget = _required_budget_units(budget_units)
    hint = _optional_marginal_output_hint(marginal_output_hint)
    gaps = data["gaps"]
    coverage = data["coverage"]
    gaps_type = gaps["type"]
    status = coverage["status"]
    reasons: list[str] = []

    # 1) Gap size: only watermark-provable backlog structure is scored.
    # _required_cursor already guarantees next_cursor is None or a
    # nonempty string here, so "is not None" means exactly "resumable".
    if gaps_type == "history_unfinished" and gaps.get("next_cursor") is not None:
        gap_factor = GAP_UNFINISHED_WITH_CURSOR_WEIGHT
        gap_note = (
            f"gaps.type={gaps_type} with next_cursor "
            "(resumable backlog, marginal output expected)"
        )
    elif gaps_type == "history_unfinished":
        gap_factor = GAP_UNFINISHED_NO_CURSOR_WEIGHT
        gap_note = f"gaps.type={gaps_type} without next_cursor (resume key unknown)"
    else:
        gap_factor = 0.0
        gap_note = f"gaps.type={gaps_type} (no watermark-provable history backlog)"
    reasons.append(f"gap_factor={_fmt_factor(gap_factor)}: {gap_note}")

    # 2) Marginal output: missing hints stay neutral and are marked, never
    # fabricated.
    if hint is None:
        marginal_factor = 0.0
        marginal_note = "marginal_output_hint missing (neutral, not fabricated)"
    else:
        marginal_factor = MARGINAL_OUTPUT_WEIGHT * hint
        marginal_note = f"marginal_output_hint={hint} (external observation)"
    reasons.append(f"marginal_factor={_fmt_factor(marginal_factor)}: {marginal_note}")

    # 3) Failure cost: dense 403/timeout in the recent window demotes.
    window = _recent_failures(data)
    statuses = [item.get("status") for item in window]
    risk_count = sum(1 for value in statuses if _is_risk_status(value))
    if window and (
        risk_count * RISK_DENSE_DENOMINATOR >= len(window) * RISK_DENSE_NUMERATOR
    ):
        failure_factor = RISK_COOLING_PENALTY
        failure_note = (
            f"risk-cooling dense: {risk_count} of {len(window)} "
            "recent failures are 403/timeout"
        )
    elif window:
        failure_factor = 0.0
        failure_note = f"no dense 403/timeout in {len(window)} recent failures"
    else:
        failure_factor = 0.0
        failure_note = "no recent failures recorded"
    reasons.append(f"failure_factor={_fmt_factor(failure_factor)}: {failure_note}")

    # 4) Attribution: blocked coverage is demoted out of the ranking top.
    if status == "blocked":
        blocked_factor = ATTRIBUTION_BLOCKED_PENALTY
        blocked_note = (
            "attribution blocked (coverage.status=blocked); "
            "excluded from top of advisory ranking"
        )
    else:
        blocked_factor = 0.0
        blocked_note = f"coverage.status={status}"
    reasons.append(f"blocked_factor={_fmt_factor(blocked_factor)}: {blocked_note}")

    raw_score = gap_factor + marginal_factor + failure_factor + blocked_factor
    priority_score = float(raw_score) / float(budget)
    reasons.append(
        f"budget_divisor={budget}: priority_score = raw_score / budget_units"
    )

    trend_inputs = {
        "recent_failure_count": len(window),
        "risk_failure_count_recent": risk_count,
        "last_fetched_at": _last_fetched_at(data),
        "trend_aggregation": TREND_AGGREGATION,
    }
    return ScoreResult(
        symbol=data["symbol"],
        lane=data["lane"],
        priority_score=priority_score,
        reasons=reasons,
        trend_inputs=trend_inputs,
        advisory=True,
    )


def gap_alerts(bundle: dict[str, Any]) -> list[GapAlert]:
    """Derive the explainable gap alerts for one stock-lane bundle.

    Rules are a finite, auditable list; healthy stocks produce no alerts so
    no noise is manufactured:

    - ``gaps.type == 'history_unfinished'`` raises a ``history_unfinished``
      ``warning`` whose detail states whether ``next_cursor`` is present.
    - ``coverage.status == 'blocked'`` raises an ``attribution_blocked``
      ``warning``.
    - ``pending.state == 'paused'`` raises a ``collection_paused`` ``info``.
    - any 403/timeout status in the recent ``FAILURE_WINDOW`` raises a
      ``risk_cooling`` ``warning``.

    Every alert's ``evidence`` dict echoes the raw trigger values (gap
    type, next cursor, pending state, recent failure statuses) for audit;
    the values are only referenced, never rewritten. This function is
    read-only and closed on failure: it performs no I/O, never mutates the
    bundle, and raises ``ValueError`` on a malformed bundle before any
    alert is produced.

    Args:
        bundle: The ``export_evidence()`` result dict for one stock and
            lane.

    Returns:
        The list of ``GapAlert`` rows in the rule order above; empty for a
        healthy stock.

    Raises:
        ValueError: The bundle is malformed (including a blank
            ``next_cursor`` on an unfinished-history gap). Closed on
            failure; no partial alert list.
    """
    data = _required_bundle(bundle)
    symbol = data["symbol"]
    lane = data["lane"]
    gaps = data["gaps"]
    coverage = data["coverage"]
    pending = data.get("pending") or {}
    alerts: list[GapAlert] = []

    # Rule 1: demonstrably unfinished history backfill.
    if gaps["type"] == "history_unfinished":
        # Validated by _required_cursor: None or a nonempty string, so
        # "is not None" means exactly "resumable cursor present".
        cursor = gaps.get("next_cursor")
        if cursor is not None:
            detail = "history backlog unfinished; next_cursor present (resumable)"
        else:
            detail = (
                "history backlog unfinished; next_cursor missing (resume key unknown)"
            )
        alerts.append(
            GapAlert(
                symbol=symbol,
                lane=lane,
                alert_type="history_unfinished",
                severity="warning",
                detail=detail,
                evidence={"gaps_type": gaps["type"], "next_cursor": cursor},
            )
        )

    # Rule 2: coverage attribution blocked.
    if coverage["status"] == "blocked":
        alerts.append(
            GapAlert(
                symbol=symbol,
                lane=lane,
                alert_type="attribution_blocked",
                severity="warning",
                detail=(
                    "coverage attribution blocked; stock is not eligible "
                    "for the top of the advisory ranking"
                ),
                evidence={
                    "coverage_status": coverage["status"],
                    "coverage_reason": coverage.get("reason"),
                },
            )
        )

    # Rule 3: offline queue collection paused (informational only).
    if pending.get("state") == "paused":
        alerts.append(
            GapAlert(
                symbol=symbol,
                lane=lane,
                alert_type="collection_paused",
                severity="info",
                detail=(
                    "collection task paused in the offline queue "
                    "(state=paused); informational only"
                ),
                evidence={
                    "pending_state": "paused",
                    "pending_page_key": pending.get("page_key"),
                },
            )
        )

    # Rule 4: recent risk-control cooling signals in failures.
    window = _recent_failures(data)
    statuses = [item.get("status") for item in window]
    risk_count = sum(1 for value in statuses if _is_risk_status(value))
    if risk_count > 0:
        alerts.append(
            GapAlert(
                symbol=symbol,
                lane=lane,
                alert_type="risk_cooling",
                severity="warning",
                detail=(
                    f"recent failures include 403/timeout risk-cooling "
                    f"signals ({risk_count} of {len(window)} in window)"
                ),
                evidence={
                    "recent_failure_statuses": statuses,
                    "risk_failure_count_recent": risk_count,
                },
            )
        )

    return alerts


def rank_history_targets(
    bundles: list[dict[str, Any]],
    *,
    budget_units: int = 1,
) -> list[ScoreResult]:
    """Rank many stock-lane bundles into one advisory target list.

    Each bundle is scored with :func:`score_history_backlog` under the same
    ``budget_units`` and sorted by a three-key deterministic order:
    ``priority_score`` descending, then ``symbol`` ascending, then the
    ``new`` lane before the ``history`` lane. On equal scores the ``new``
    lane is therefore always ranked before the ``history`` lane, so the
    output order is deterministic and never depends on the input
    permutation. The output is then truncated to the first
    ``budget_units`` rows, so the budget plays two documented roles: score
    divisor and the maximum number of suggested targets.

    Marginal-output hints are not part of an evidence bundle, so ranking
    always treats that factor as missing/neutral; callers who hold an
    external hint can score individual bundles themselves. Every bundle is
    validated before any scoring happens: one malformed bundle rejects the
    whole call (closed on failure) and no partial ranking is returned.

    This function is read-only and closed on failure: no I/O, no mutation
    of the inputs, and the output is advisory only (``recommendation_only``)
    — it never rewrites collection watermarks, never triggers collection,
    and never schedules work.

    Args:
        bundles: List of ``export_evidence()``-shaped dicts, each carrying
            at least ``symbol``, ``lane``, ``coverage`` and ``gaps``.
        budget_units: Positive int; score divisor and top-N cutoff.

    Returns:
        Up to ``budget_units`` ``ScoreResult`` rows, best first.

    Raises:
        ValueError: ``bundles`` is not a list, any bundle is malformed, or
            ``budget_units`` is not a positive int. Closed on failure; no
            partial result.
    """
    if not isinstance(bundles, list):
        raise ValueError("bundles must be a list")
    budget = _required_budget_units(budget_units)
    validated = [_required_bundle(item) for item in bundles]
    scored = [score_history_backlog(item, budget_units=budget) for item in validated]
    # Three-key deterministic order: score desc, symbol asc, then the new
    # lane before the history lane (同分时 new 先于 history).
    scored.sort(
        key=lambda result: (
            -result.priority_score,
            result.symbol,
            0 if result.lane == "new" else 1,
        )
    )
    return scored[:budget]
