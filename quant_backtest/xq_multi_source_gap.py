"""Read-only multi-source gap view over one acceptance-evidence bundle.

This module merges labelled supplementary market rows (TDX / Tushare) onto
the output of ``xq_acceptance_evidence.export_evidence()`` without touching
any stored state. It is read-only: the function issues no SQL, opens no
sockets, and writes nothing to disk or database. It is closed on failure:
every invalid input raises ``ValueError`` before a view is produced, and a
partial merge is never returned.

Supplementary payloads are attach-only evidence and never upgrade Xueqiu
post coverage. ``coverage`` and ``gaps`` are copied verbatim from the input
bundle; their ``status`` and ``type`` values do not change whether zero,
one, or many supplements are present. The ``degradation`` section always
carries the note "supplementary data does not upgrade Xueqiu post coverage"
so a downstream reader cannot mistake market-data availability for Xueqiu
post coverage success.

Event, classification, and factor pipelines are not wired here (explicitly
``not_linked`` downstream). The copied ``source_chain`` section keeps its
``downstream_units: "not_linked"`` marker verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Supplementary providers accepted by the merge view. Xueqiu is the
#: evidenced source itself and is never a valid supplement label.
SUPPLEMENT_SOURCES: tuple[str, ...] = ("tdx", "tushare")

#: Constant degradation note: supplementary rows never upgrade Xueqiu
#: post coverage, no matter how complete their payloads look.
UPGRADE_NOTE: str = "supplementary data does not upgrade Xueqiu post coverage"

#: Key fields every input evidence bundle must carry. Missing fields are
#: closed on failure with ``ValueError`` instead of being invented.
_REQUIRED_BUNDLE_KEYS: tuple[str, ...] = ("symbol", "lane", "coverage", "gaps")


def _required_string(value: Any, label: str) -> str:
    """Require an exact nonempty identifier, without normalization."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _required_as_of(value: Any) -> int | str:
    """Accept only a nonnegative millisecond int or a nonempty label str.

    Booleans do not count as millisecond integers. A negative integer is
    closed on failure with ``ValueError``, matching the family ``_timestamp``
    nonnegative lower bound used by store, ledger and queue. An empty or
    blank string is closed on failure with ``ValueError`` too.
    """
    if isinstance(value, bool):
        raise ValueError("as_of must be a nonnegative int (ms) or nonempty str")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(
                "as_of must be a nonnegative int (ms) or nonempty str")
        return value
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError("as_of must be a nonnegative int (ms) or nonempty str")


def _as_of_sort_key(as_of: int | str) -> tuple[int, int | str]:
    """Order millisecond integers first, then lexicographic label strings.

    The two families never compare against each other because the leading
    discriminator differs; mixed groups stay deterministic.
    """
    if isinstance(as_of, int):
        return (0, as_of)
    return (1, as_of)


@dataclass(frozen=True)
class Supplement:
    """One supplementary market-data row tagged with its provider.

    ``source`` is only ``'tdx'`` or ``'tushare'``. ``data_type`` names the
    kind of supplementary payload (for example ``'quote'``, ``'timeseries'``
    or ``'fundamental'``). ``as_of`` is the payload's own effective time:
    Unix milliseconds as ``int``, or a nonempty provider label ``str``.
    ``payload`` is the provider's raw structured row.

    Construction is closed on failure: invalid fields raise ``ValueError``
    instead of producing a partial row.
    """

    source: str
    symbol: str
    data_type: str
    payload: dict[str, Any]
    as_of: int | str

    def __post_init__(self) -> None:
        """Validate every field at construction; closed on failure."""
        if self.source not in SUPPLEMENT_SOURCES:
            raise ValueError("source must be 'tdx' or 'tushare'")
        _required_string(self.symbol, "symbol")
        _required_string(self.data_type, "data_type")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dict")
        _required_as_of(self.as_of)


def _required_bundle(evidence_bundle: Any) -> dict[str, Any]:
    """Validate the input bundle shape without rewriting any of its fields.

    The bundle must be a dict carrying ``symbol``, ``lane``, ``coverage``
    and ``gaps``. ``coverage`` must be a dict with a nonempty ``status``
    string and ``gaps`` a dict with a nonempty ``type`` string so the
    degradation section can describe them faithfully. Anything else is
    closed on failure with ``ValueError``.
    """
    if not isinstance(evidence_bundle, dict):
        raise ValueError("evidence_bundle must be a dict")
    for key in _REQUIRED_BUNDLE_KEYS:
        if key not in evidence_bundle:
            raise ValueError(f"evidence_bundle must contain '{key}'")
    _required_string(evidence_bundle["symbol"], "symbol")
    _required_string(evidence_bundle["lane"], "lane")
    coverage = evidence_bundle["coverage"]
    if not isinstance(coverage, dict) or "status" not in coverage:
        raise ValueError("coverage must be a dict with a status key")
    _required_string(coverage["status"], "coverage status")
    gaps = evidence_bundle["gaps"]
    if not isinstance(gaps, dict) or "type" not in gaps:
        raise ValueError("gaps must be a dict with a type key")
    _required_string(gaps["type"], "gaps type")
    return evidence_bundle


def _required_supplements(supplements: Any) -> list[Supplement]:
    """Require a list holding only ``Supplement`` rows; closed on failure."""
    if not isinstance(supplements, list):
        raise ValueError("supplements must be a list of Supplement")
    rows: list[Supplement] = []
    for item in supplements:
        if not isinstance(item, Supplement):
            raise ValueError("supplements must contain only Supplement rows")
        rows.append(item)
    return rows


def _supplements_section(rows: list[Supplement]) -> dict[str, list[dict[str, Any]]]:
    """Group supplements by provider without ever merging or deduplicating.

    Only providers that actually contributed rows appear as keys, so an
    empty input yields an empty section. Within one provider the rows are
    ordered by ``as_of`` ascending (integers numerically, label strings
    lexicographically) with ``data_type`` as a deterministic tie-break.
    Same ``source`` + ``data_type`` rows are all kept, sorted by ``as_of``,
    and never collapsed into one another. Each entry carries exactly
    ``source`` / ``data_type`` / ``as_of`` / ``payload`` so rows stay
    distinguishable; the payload is copied shallowly into the view.
    """
    grouped: dict[str, list[Supplement]] = {}
    for row in rows:
        grouped.setdefault(row.source, []).append(row)
    section: dict[str, list[dict[str, Any]]] = {}
    for source in SUPPLEMENT_SOURCES:
        if source not in grouped:
            continue
        ordered = sorted(
            grouped[source],
            key=lambda row: (_as_of_sort_key(row.as_of), row.data_type),
        )
        section[source] = [
            {
                "source": row.source,
                "data_type": row.data_type,
                "as_of": row.as_of,
                "payload": dict(row.payload),
            }
            for row in ordered
        ]
    return section


def _degradation_section(bundle: dict[str, Any]) -> dict[str, Any]:
    """Describe Xueqiu-side degradation honestly, independent of supplements.

    The section is degraded exactly when ``coverage.status`` is ``blocked``
    or ``empty``, or when ``gaps.type`` is not ``unknown``. ``xueqiu_posts``
    restates the untouched coverage status and ``gaps_type`` the untouched
    gap type; both are copied verbatim and never rewritten by the merge.
    The constant note records that supplementary data does not upgrade
    Xueqiu post coverage, and a healthy bundle never claims a gap.
    """
    status = bundle["coverage"]["status"]
    gaps_type = bundle["gaps"]["type"]
    degraded = status in ("blocked", "empty") or gaps_type != "unknown"
    return {
        "degraded": degraded,
        "xueqiu_posts": status,
        "gaps_type": gaps_type,
        "note": UPGRADE_NOTE,
    }


def merge_supplement(
    evidence_bundle: dict[str, Any], supplements: list[Supplement],
) -> dict[str, Any]:
    """Return a read-only merged view of one bundle plus its supplements.

    The view keeps every input section (``coverage``, ``gaps``, ``watermark``,
    ``post_samples``, ``failures``, ``pending``, ``source_chain``, plus
    ``symbol`` / ``lane``) verbatim, then adds:

    - ``supplements``: rows grouped by provider (``tdx`` / ``tushare``),
      each entry carrying ``source`` / ``data_type`` / ``as_of`` / ``payload``
      and ordered by ``as_of`` within a provider. Same ``source`` +
      ``data_type`` rows are allowed and are neither deduplicated nor
      merged. An empty input yields ``{}``.
    - ``degradation``: the honest downgrade statement with ``degraded``,
      ``xueqiu_posts`` (the untouched coverage status), ``gaps_type`` (the
      untouched gap type) and the constant note "supplementary data does
      not upgrade Xueqiu post coverage".

    Supplements never change ``coverage.status`` or ``gaps.type``: market
    data arriving from TDX or Tushare does not upgrade Xueqiu post
    coverage. Each supplement's ``symbol`` must match the bundle symbol
    exactly, without normalization.

    This function is read-only and closed on failure: it performs no I/O,
    never mutates the input bundle, and raises ``ValueError`` before any
    view is produced when the bundle or a supplement is invalid. Event,
    classification and factor pipelines are not wired here; downstream
    units stay explicitly ``not_linked`` inside ``source_chain``.

    Args:
        evidence_bundle: The ``export_evidence()`` result dict for one
            stock and lane; must carry ``symbol``, ``lane``, ``coverage``
            and ``gaps``.
        supplements: Zero or more validated ``Supplement`` rows for the
            same stock.

    Returns:
        A new dict: the input bundle verbatim plus ``supplements`` and
        ``degradation``.

    Raises:
        ValueError: The bundle is not a dict or lacks ``symbol`` / ``lane``
            / ``coverage`` / ``gaps``, ``coverage`` lacks ``status``,
            ``gaps`` lacks ``type``, ``supplements`` is not a list of
            ``Supplement``, or a supplement's symbol does not exactly match
            the bundle symbol. Closed on failure; no partial view.
    """
    bundle = _required_bundle(evidence_bundle)
    rows = _required_supplements(supplements)
    symbol = bundle["symbol"]
    for row in rows:
        if row.symbol != symbol:
            raise ValueError(
                "supplement symbol must exactly match the bundle symbol")
    merged: dict[str, Any] = dict(bundle)
    merged["supplements"] = _supplements_section(rows)
    merged["degradation"] = _degradation_section(bundle)
    return merged
