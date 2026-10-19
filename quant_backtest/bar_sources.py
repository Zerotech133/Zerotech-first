# -*- coding: utf-8 -*-
"""Read-only minute Bar providers with explicit source provenance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

# G1 包内收敛（RC-0 S1）：包路径 quant_backtest.bar_sources 走包内相对导入；
# 顶层加载（bar_store/execution_engine 顶层链 / 既有测试经 sys.path 注入）走顶层
# 导入，行为与包内化之前完全一致。
# 兼容守卫为过渡态：S2 落地后顶层加载链消失，届时删除 else 分支，仅保留相对导入。
if __package__:
    from .bar_dto import Bar, BarQuery, ensure_utc
    from .market_policy import get_policy, session_date_for, timezone_for
else:
    from bar_dto import Bar, BarQuery, ensure_utc
    from market_policy import get_policy, session_date_for, timezone_for


class SourceReadError(RuntimeError):
    """A configured source could not be read; callers must not silently switch."""
    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message


def _value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _aware(value: Any, fallback: Optional[datetime] = None, naive_tz: timezone = timezone.utc) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=naive_tz)
        return ensure_utc(value, "trade_time")
    if value is not None:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=naive_tz)
            return ensure_utc(parsed, "trade_time")
        except ValueError as exc:
            raise ValueError(f"invalid trade_time: {value}") from exc
    if fallback is not None:
        return ensure_utc(fallback, "trade_time")
    raise ValueError("trade_time is required")


def _bar(
    row: Mapping[str, Any],
    source: str,
    market: str,
    timeframe: str,
    source_version: str,
    source_trace: Optional[Tuple[str, ...]] = None,
    calendar_ref: Optional[str] = None,
    calendar_version: Optional[str] = None,
) -> Bar:
    policy = get_policy(market)
    local_tz = timezone_for(policy.timezone)
    dt = _aware(_value(row, "trade_time", "datetime", "dt", "ts"), naive_tz=local_tz)
    raw_symbol = _value(row, "symbol", "ts_code", "code")
    if raw_symbol is None or not str(raw_symbol).strip() or str(raw_symbol).lower() == "nan":
        raise ValueError("symbol is required")
    values = [_value(row, "o", "open"), _value(row, "h", "high"), _value(row, "l", "low"), _value(row, "c", "close")]
    if any(value is None or not math.isfinite(float(value)) for value in values):
        raise ValueError("OHLC must be finite")
    o, h, low, c = [float(value) for value in values]
    if min(o, h, low, c) <= 0 or h < max(o, c) or low > min(o, c):
        raise ValueError("OHLC relationship is invalid")
    close_raw = _value(row, "close_at")
    available_raw = _value(row, "available_at")
    close_at = _aware(close_raw, naive_tz=local_tz) if close_raw is not None else None
    available_at = _aware(available_raw, naive_tz=local_tz) if available_raw is not None else None
    evidence = close_at is not None and available_at is not None
    return Bar(symbol=str(raw_symbol), market=market, timeframe=timeframe, dt=dt,
               session_date=session_date_for(dt, market), o=o, h=h, l=low, c=c,
               v=_value(row, "v", "vol", "volume"), amount=_value(row, "amount", "turnover"),
               close_at=close_at, available_at=available_at,
               closed=bool(_value(row, "closed")) if evidence and _value(row, "closed") is not None else False,
               source=source, source_version=source_version, adjustment=str(_value(row, "adjustment") or "none"),
               quality=str(_value(row, "quality") or ("observed" if evidence else "availability_estimated")),
               source_trace=source_trace or (source,), calendar_ref=calendar_ref,
               calendar_version=calendar_version)


class LocalParquetBarSource:
    """Read local parquet shards under a configured root without network or writes."""

    @staticmethod
    def _validate_table(table: str) -> None:
        """Reject traversal and platform-specific absolute or device path syntax."""
        if not isinstance(table, str) or not table or "\x00" in table:
            raise SourceReadError(str(table), "invalid parquet table path")
        windows_path = PureWindowsPath(table)
        posix_path = PurePosixPath(table)
        components = table.replace("\\", "/").split("/")
        device_names = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10))}
        if (windows_path.anchor or posix_path.is_absolute() or
                any(not part or part in {".", ".."} or ":" in part or
                    part.rstrip(" .") != part or part.split(".", 1)[0].upper() in device_names
                    for part in components)):
            raise SourceReadError(table, "invalid parquet table path")

    def _validate_path(self, path: Path) -> None:
        """Check lexical containment and reject existing symlink/reparse components."""
        try:
            relative = path.relative_to(self.root)
            current = self.root
            for component in relative.parts:
                current = current / component
                try:
                    details = current.lstat()
                except FileNotFoundError:
                    continue
                reparse_attributes = getattr(details, "st_file_attributes", 0)
                if (stat.S_ISLNK(details.st_mode) or
                        reparse_attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                    raise SourceReadError(self.table, "parquet path contains symlink or reparse point")
            path.resolve().relative_to(self.root)
        except (ValueError, OSError, RuntimeError) as exc:
            raise SourceReadError(self.table, "parquet path escapes root or cannot be checked") from exc

    def __init__(self, root: Path | str, table: str, market: str, source_version: str = "parquet-v1") -> None:
        self._validate_table(table)
        self.root = Path(root).resolve(); self.table = table; self.market = market
        if self.root.name == "data":
            self.directory = self.root / ("tushare" if table in {"stk_mins", "idx_mins"} else "tdx") / table
        elif (self.root / "data" / "tushare" / table).is_dir():
            self.directory = self.root / "data" / "tushare" / table
        else:
            self.directory = self.root / table
        self._validate_path(self.directory)
        self.source_version = source_version
        self.last_metadata: Dict[str, Any] = {}

    def read(self, query: BarQuery, symbol: Optional[str] = None, timeframe: str = "1m") -> Tuple[Bar, ...]:
        try:
            self._validate_path(self.directory)
            if not self.directory.exists():
                raise SourceReadError(self.table, "no parquet source")
            parts: List[Path] = []
            pending = [self.directory]
            while pending:
                directory = pending.pop()
                for entry in directory.iterdir():
                    self._validate_path(entry)
                    if entry.is_dir():
                        pending.append(entry)
                    elif entry.name.endswith(".parquet"):
                        parts.append(entry)
            parts.sort()
        except SourceReadError:
            raise
        except (OSError, RuntimeError) as exc:
            raise SourceReadError(self.table, "parquet source cannot be enumerated") from exc
        if not parts:
            raise SourceReadError(self.table, "no parquet source")
        try:
            import pyarrow.dataset as ds
            import pyarrow.compute as pc
            dataset = ds.dataset([str(part) for part in parts], format="parquet")
            names = set(dataset.schema.names)
            code_col = "ts_code" if "ts_code" in names else "code" if "code" in names else None
            time_col = "trade_time" if "trade_time" in names else "datetime" if "datetime" in names else None
            if not code_col or not time_col:
                raise SourceReadError(self.table, "missing symbol/time columns")
            expression = pc.field(code_col) == symbol if symbol else None
            if "trade_date" not in names:
                is_timestamp = str(dataset.schema.field(time_col).type).startswith("timestamp[")
                local_tz = timezone_for(get_policy(self.market).timezone)
                def bound(value: datetime) -> Any:
                    return value.astimezone(local_tz).replace(tzinfo=None) if is_timestamp else value.astimezone(local_tz).replace(tzinfo=None).isoformat(sep=" ")
                if query.start is not None:
                    term = pc.field(time_col) >= bound(query.start); expression = term if expression is None else expression & term
                if query.end is not None:
                    term = pc.field(time_col) <= bound(query.end); expression = term if expression is None else expression & term
            rows = dataset.to_table(filter=expression).to_pylist()
        except SourceReadError:
            raise
        except Exception as exc:
            raise SourceReadError(self.table, str(exc)) from exc
        try:
            bars = [_bar(row, self.table, self.market, timeframe, self.source_version) for row in rows]
        except (TypeError, ValueError, KeyError) as exc:
            raise SourceReadError(self.table, str(exc)) from exc
        result = query.apply(bars)
        self.last_metadata = {"source": self.table, "rows": len(result), "coverage": "complete" if result else "missing", "missing": [] if result else ["no_rows"]}
        return result

    def explain(self) -> Dict[str, Any]:
        return dict(self.last_metadata or {"source": self.table, "rows": 0, "coverage": "unknown", "missing": ["not_read"]})


class TdxSectorBarSource:
    """Read-only TDX sector minutes from primary and tail parquet shards."""
    def __init__(self, root: Path | str, market: str = "CN", source_version: str = "tdx-sector-v1") -> None:
        base = Path(root).resolve()
        self.tdx_root = base / "tdx" if base.name == "data" else (base / "data" / "tdx" if (base / "data" / "tdx").is_dir() else base)
        self.market = market; self.source_version = source_version; self.last_metadata: Dict[str, Any] = {}

    def read(self, query: BarQuery, symbol: Optional[str] = None, timeframe: str = "1m") -> Tuple[Bar, ...]:
        try:
            import pyarrow.dataset as ds
            import pyarrow.compute as pc
        except ImportError as exc:
            raise SourceReadError("tdx_sector_min", "pyarrow unavailable") from exc
        directories = [self.tdx_root / "tdx_sector_min", self.tdx_root / "tdx_sector_min_tail"]
        parts = [part for directory in directories if directory.is_dir() for part in directory.rglob("*.parquet")]
        if not parts:
            raise SourceReadError("tdx_sector_min", "no primary or tail parquet source")
        rows: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
        row_sources: Dict[Tuple[str, str, str], List[str]] = {}
        try:
            for part in sorted(parts):
                shard = ds.dataset(str(part), format="parquet"); names = set(shard.schema.names)
                source_name = "tdx_sector_min_tail" if "tdx_sector_min_tail" in part.parts else "tdx_sector_min"
                required = {"code", "trade_date", "trade_time", "open", "high", "low", "close", "vol", "amount"}
                if not required.issubset(names):
                    raise SourceReadError("tdx_sector_min", "missing split timestamp/OHLC columns")
                expression = pc.field("code") == symbol if symbol else None
                if query.start is not None:
                    day = query.start.astimezone(timezone(timedelta(hours=8))).date().strftime("%Y%m%d")
                    expression = pc.field("trade_date") >= day if expression is None else expression & (pc.field("trade_date") >= day)
                if query.end is not None:
                    day = query.end.astimezone(timezone(timedelta(hours=8))).date().strftime("%Y%m%d")
                    term = pc.field("trade_date") <= day; expression = term if expression is None else expression & term
                cols = ["code", "trade_date", "trade_time", "open", "high", "low", "close", "vol", "amount"]
                for row in shard.to_table(columns=cols, filter=expression).to_pylist():
                    day = str(row["trade_date"]).replace("-", "")[:8]; tm = str(row["trade_time"])
                    key = (str(row["code"]), day, tm)
                    previous = rows.get(key)
                    if previous is not None:
                        compare_cols = ("open", "high", "low", "close", "vol", "amount")
                        if any(previous.get(col) != row.get(col) for col in compare_cols):
                            raise SourceReadError("tdx_sector_min", f"conflicting duplicate bar: {key}")
                        row_sources.setdefault(key, []).append(source_name)
                        continue
                    rows[key] = row
                    row_sources[key] = [source_name]
        except SourceReadError:
            raise
        except Exception as exc:
            raise SourceReadError("tdx_sector_min", str(exc)) from exc
        bars: List[Bar] = []
        for key, raw_row in rows.items():
            row = dict(raw_row); day = str(row["trade_date"]).replace("-", "")[:8]
            row["trade_time"] = f"{day[:4]}-{day[4:6]}-{day[6:8]} {row['trade_time']}"
            try:
                trace = tuple(dict.fromkeys(row_sources.get(key, ["tdx_sector_min"])))
                bars.append(_bar(row, "tdx_sector_min", self.market, timeframe, self.source_version, trace))
            except (TypeError, ValueError, KeyError) as exc:
                raise SourceReadError("tdx_sector_min", str(exc)) from exc
        result = query.apply(bars)
        self.last_metadata = {"source": "tdx_sector_min+tdx_sector_min_tail", "rows": len(result), "coverage": "complete" if result else "missing", "missing": [] if result else ["no_rows"]}
        return result

    def explain(self) -> Dict[str, Any]:
        return dict(self.last_metadata or {"source": "tdx_sector_min+tdx_sector_min_tail", "rows": 0, "coverage": "unknown", "missing": ["not_read"]})


class PostgresBarSource:
    """Read v2_bar_minute through an injected read-only connection factory."""
    def __init__(self, connection_factory: Callable[[], Any], source_version: str = "pg-v2_bar_minute") -> None:
        self.connection_factory = connection_factory; self.source_version = source_version

    def read(self, query: BarQuery, symbol: str, market: str, timeframe: str = "1m") -> Tuple[Bar, ...]:
        if query.start is None or query.end is None:
            raise SourceReadError("v2_bar_minute", "start and end are required")
        sql = ("SELECT symbol, bar_time AS trade_time, open, high, low, close, volume AS vol, turnover AS amount, close_at, available_at, closed, adjustment, quality FROM v2_bar_minute WHERE market=%s AND symbol=%s AND bar_time >= %s AND bar_time <= %s ORDER BY bar_time")
        connection = None
        try:
            connection = self.connection_factory()
            with connection.cursor() as cursor:
                cursor.execute(sql, (market, symbol, query.start, query.end)); columns = [item[0] for item in cursor.description]; rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            raise SourceReadError("v2_bar_minute", str(exc)) from exc
        finally:
            if connection is not None:
                try: connection.close()
                except Exception: pass
        try:
            bars = [_bar(row, "v2_bar_minute", market, timeframe, self.source_version) for row in rows]
        except (TypeError, ValueError, KeyError) as exc:
            raise SourceReadError("v2_bar_minute", str(exc)) from exc
        return query.apply(bars)
