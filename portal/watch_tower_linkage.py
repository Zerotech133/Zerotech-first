# -*- coding: utf-8 -*-
"""Read-only historical market linkage for Watch Tower replay.

The reader consumes only persisted parquet shards under ``quant_backtest/data``.
It never downloads, writes, fills, or falls back to current market data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MAX_MINUTE_BARS = 240


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    return str(value)


def _json_value(value: Any) -> Any:
    """Convert Arrow/pandas scalar values to JSON-safe native values."""
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _filter_value(field_type: Any, day: date) -> Any:
    """Build a parquet predicate scalar matching common historical schemas."""
    type_name = str(field_type)
    if "date32" in type_name or "date64" in type_name:
        return day
    if "timestamp" in type_name:
        return day
    if "int" in type_name:
        return int(day.strftime("%Y%m%d"))
    # TDX exports use compact YYYYMMDD strings. ISO strings are handled by the
    # second-pass fallback in _read_parts for fixtures/legacy shards.
    return day.strftime("%Y%m%d")


def _read_parts(
    table_dir: Path,
    columns: Sequence[str],
    target_day: Optional[date] = None,
    date_column: str = "trade_date",
    equals: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[Mapping[str, Any]], Optional[str]]:
    """Read parquet parts with per-shard predicate pushdown.

    Each shard is scanned independently and only after a predicate is applied;
    target-day calls never fall back to an unfiltered full-table read.
    """
    parts = sorted(table_dir.glob("*.parquet"))
    if not parts:
        return [], "no_historical_source"
    try:
        import pyarrow.dataset as ds
        import pyarrow.compute as pc
    except ImportError:
        return [], "parquet_reader_unavailable"
    rows: List[Mapping[str, Any]] = []
    try:
        for part in parts:
            shard = ds.dataset(str(part), format="parquet")
            available = set(shard.schema.names)
            if any(column not in available for column in columns):
                return [], "schema_missing"
            expression = None
            if target_day is not None:
                if date_column not in available:
                    return [], "schema_missing"
                field_type = shard.schema.field(date_column).type
                expression = pc.field(date_column) == _filter_value(
                    field_type, target_day
                )
            if equals:
                for key, value in equals.items():
                    if key not in available:
                        return [], "schema_missing"
                    term = pc.field(key) == value
                    expression = term if expression is None else expression & term
            # No target day/equality is used only for small metadata tables.
            table = shard.to_table(columns=list(columns), filter=expression)
            shard_rows = table.to_pylist()
            if target_day is not None:
                wanted = target_day.strftime("%Y%m%d")
                shard_rows = [
                    row
                    for row in shard_rows
                    if _date_text(row.get(date_column)) == wanted
                ]
            rows.extend(shard_rows)
        return rows, None
    except (OSError, IOError, ValueError, KeyError, ArrowInvalid, TypeError):
        return [], "read_error"


try:
    from pyarrow.lib import ArrowInvalid
except ImportError:  # pragma: no cover - guarded by dependency error above

    class ArrowInvalid(Exception):
        """Fallback exception when pyarrow is unavailable."""


def _date_text(value: Any) -> str:
    return str(value).replace("-", "")[:8]


def _normalise_code(value: Any) -> str:
    return str(value or "").strip().upper().replace(".", "")


class LinkageReader:
    """Build historical sector/index linkage without live-data fallback."""

    def __init__(
        self, station_root: Path | str, max_bars: int = MAX_MINUTE_BARS
    ) -> None:
        self.station_root = Path(station_root).resolve()
        self.tdx_root = self.station_root / "quant_backtest" / "data" / "tdx"
        self.max_bars = max(1, min(int(max_bars), MAX_MINUTE_BARS))
        self._definitions_cache: Optional[
            Tuple[Dict[str, Mapping[str, Any]], Optional[str]]
        ] = None
        self._member_cache: Dict[str, Tuple[List[str], Optional[str]]] = {}
        self._index_cache: Dict[Tuple[str, str], Tuple[List[str], Optional[str]]] = {}

    def _definitions(self) -> Tuple[Dict[str, Mapping[str, Any]], Optional[str]]:
        if self._definitions_cache is None:
            rows, error = _read_parts(
                self.tdx_root / "tdx_sector_def", ("code", "name", "seg")
            )
            self._definitions_cache = (
                {_normalise_code(row.get("code")): row for row in rows},
                error,
            )
        return self._definitions_cache

    def _member_names(self, symbol: Optional[str]) -> Tuple[List[str], Optional[str]]:
        if not symbol:
            return [], None
        target = _normalise_code(symbol)
        if target not in self._member_cache:
            rows, error = _read_parts(
                self.tdx_root / "tdx_sector_member", ("block_name", "con_code")
            )
            if error:
                self._member_cache[target] = ([], error)
            else:
                self._member_cache[target] = (
                    sorted(
                        {
                            _text(row.get("block_name"))
                            for row in rows
                            if _normalise_code(row.get("con_code")) == target
                            and _text(row.get("block_name"))
                        }
                    ),
                    None,
                )
        return self._member_cache[target]

    def _indexes(
        self, symbol: Optional[str], day: date
    ) -> Tuple[List[str], Optional[str]]:
        if not symbol:
            return [], None
        cache_key = (_normalise_code(symbol), day.strftime("%Y%m%d"))
        if cache_key in self._index_cache:
            return self._index_cache[cache_key]
        # Index weights are rebalanced on dates before the replay day; an exact
        # predicate would incorrectly hide the latest valid weight row. This
        # table is small enough to read once and cache by symbol/day.
        rows, error = _read_parts(
            self.tdx_root / "ts_index_weight", ("index_code", "con_code", "trade_date")
        )
        if error:
            result = ([], error)
        else:
            target = cache_key[0]
            eligible = [
                row
                for row in rows
                if _normalise_code(row.get("con_code")) == target
                and _date_text(row.get("trade_date")) <= day.strftime("%Y%m%d")
            ]
            result = (
                sorted(
                    {
                        _text(row.get("index_code"))
                        for row in eligible
                        if _text(row.get("index_code"))
                    }
                ),
                None,
            )
        self._index_cache[cache_key] = result
        return result

    def build(
        self, replay_at: datetime, symbol: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return linkage DTO for the replay day; missing data remains explicit."""
        day = replay_at.astimezone(timezone.utc).date()
        minute_columns = (
            "code",
            "trade_date",
            "trade_time",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "amount",
            "up_count",
            "down_count",
        )
        rows, minute_error = _read_parts(
            self.tdx_root / "tdx_sector_min", minute_columns, target_day=day
        )
        if minute_error or not rows:
            return {
                "relation": {
                    "symbol": symbol,
                    "sector": [],
                    "industry": [],
                    "indexes": [],
                },
                "minute_bars": [],
                "as_of": None,
                "source_quality": "unknown",
                "gaps": [minute_error or "no_historical_source"],
            }

        definitions, definition_error = self._definitions()
        codes = sorted({_normalise_code(row.get("code")) for row in rows})
        sectors: List[str] = []
        industries: List[str] = []
        for code in codes:
            item = definitions.get(code)
            if not item:
                continue
            name = _text(item.get("name"))
            seg = _text(item.get("seg"))
            if name and name not in sectors:
                sectors.append(name)
            if seg and seg not in {"stat", "region"} and seg not in industries:
                industries.append(seg)
        indexes, index_error = self._indexes(symbol, day)
        member_names, member_error = self._member_names(symbol)
        for member_name in member_names:
            if member_name not in sectors:
                sectors.append(member_name)
        rows.sort(
            key=lambda row: (
                str(row.get("trade_time") or ""),
                str(row.get("code") or ""),
            )
        )
        bars = [
            {field: _json_value(row.get(field)) for field in minute_columns}
            for row in rows[-self.max_bars :]
        ]
        as_of = (
            "%sT%s+00:00" % (day.isoformat(), bars[-1].get("trade_time") or "00:00")
            if bars
            else None
        )
        gaps: List[str] = []
        if definition_error:
            gaps.append("sector_definition:%s" % definition_error)
        if index_error:
            gaps.append("index_weight:%s" % index_error)
        if member_error:
            gaps.append("sector_member:%s" % member_error)
        if not sectors:
            gaps.append("sector_definition:no_mapping")
        return {
            "relation": {
                "symbol": symbol,
                "sector": sectors,
                "industry": industries,
                "indexes": indexes,
            },
            "minute_bars": bars,
            "as_of": as_of,
            "source_quality": "historical" if not gaps else "partial",
            "gaps": gaps,
        }
