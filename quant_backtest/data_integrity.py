# -*- coding: utf-8 -*-
"""只读数据完整度扫描：物理键、日历断面及真实查询，独立于 done 标记。"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
TABLES = {
    "ts_daily": ("ts_code", ["open", "high", "low", "close", "vol", "amount"]),
    "ts_daily_basic": ("ts_code", ["turnover_rate", "total_mv", "circ_mv"]),
    "tdx_sector_daily": ("code", ["open", "high", "low", "close", "vol", "amount"]),
    "ts_margin_detail": ("ts_code", ["rzye", "rzrqye"]),
}


def _day(values):
    return values.astype("string").str.replace("-", "", regex=False).str.slice(0, 8)


def _null(values):
    return values.isna() | values.astype(str).str.strip().eq("")


def _parts(root, source, table):
    return sorted((root / source / table).glob("part_*.parquet"))


def _frames(paths, wanted, start=None, end=None):
    for path in paths:
        pf = pq.ParquetFile(path)
        cols = [c for c in wanted if c in pf.schema_arrow.names]
        if not cols:
            raise ValueError("文件缺少所有所需列: %s" % path)
        # 日期统计只用于剪枝；缺统计时读原行，不能假定文件不在窗口内。
        if start and "trade_date" in pf.schema_arrow.names:
            j = pf.schema_arrow.names.index("trade_date")
            ranges = [pf.metadata.row_group(i).column(j).statistics for i in range(pf.metadata.num_row_groups)]
            if ranges and all(s is not None and s.min is not None and s.max is not None for s in ranges):
                lo = min(str(s.min).replace("-", "")[:8] for s in ranges)
                hi = max(str(s.max).replace("-", "")[:8] for s in ranges)
                if hi < start or (end and lo > end):
                    continue
        for batch in pf.iter_batches(batch_size=200000, columns=cols):
            frame = batch.to_pandas()
            if "trade_date" in frame:
                dates = _day(frame.trade_date)
                mask = pd.Series(True, index=frame.index)
                if start:
                    mask &= dates >= start
                if end:
                    mask &= dates <= end
                frame = frame.loc[mask].copy()
                frame["trade_date"] = dates.loc[frame.index]
            yield frame


def _calendar(root, today):
    paths = _parts(root, "tushare", "trade_cal")
    if not paths:
        raise ValueError("缺少 SSE 交易日历")
    days, represented = set(), set()
    for frame in _frames(paths, ["exchange", "cal_date", "is_open"]):
        if not {"cal_date", "is_open"}.issubset(frame.columns):
            raise ValueError("交易日历缺少 cal_date/is_open")
        selected = frame
        if "exchange" in frame:
            selected = frame.loc[frame.exchange.eq("SSE")]
        normalized = _day(selected.cal_date)
        represented.update(normalized.dropna())
        mask = selected.is_open.astype(str).isin(["1", "1.0", "True"])
        days.update(normalized.loc[mask].dropna())
    if today not in represented:
        raise ValueError("SSE 日历未覆盖今天: " + today)
    expected_dates = set(pd.date_range(min(represented), today).strftime("%Y%m%d"))
    absent = sorted(expected_dates - represented)
    if absent:
        raise ValueError("SSE 日历自然日记录缺口: " + ",".join(absent[:10]))
    return sorted(d for d in days if d <= today)


def _metric(out, name, status, value, details):
    out.append({"name": name, "status": status, "value": value, "details": details})


def _eligible_suspensions(root, day):
    path = root / "integrity" / "stock_daily_eligibility.json"
    if not path.exists():
        return set()
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != 1:
        raise ValueError("停牌证据版本不支持")
    eligible = set()
    for row in evidence.get("records", []):
        if (row.get("eligibility") != "listed_full_day_suspended"
                or day not in row.get("valid_dates", [])
                or not row.get("valid_start", "") <= day <= row.get("valid_through", "")):
            continue
        sources = row.get("sources", [])
        daily = [s for s in sources if s.get("provider") == "tushare.suspend_d"]
        notices = [s for s in sources if s.get("content_sha256") and s.get("url") and "停牌" in s.get("excerpt", "")]
        if len(daily) != 1 or not notices:
            raise ValueError("停牌资格缺少逐日记录或公告正文证据")
        matching = [r for r in daily[0].get("daily_rows", [])
                    if r.get("ts_code") == row.get("ts_code") and r.get("trade_date") == day]
        if len(matching) != 1 or matching[0].get("suspend_type") != "S" or matching[0].get("suspend_timing") not in (None, ""):
            raise ValueError("停牌资格与逐日全天停牌记录矛盾")
        if datetime.fromisoformat(row["available_at"]).tzinfo is None:
            raise ValueError("资格实际采集时间缺少时区")
        eligible.add(row["ts_code"])
    return eligible


def _table(root, name, code, required, window, end, metrics):
    paths = _parts(root, "tdx", name)
    if not paths:
        raise ValueError("无物理数据文件")
    dates, keys = set(), defaultdict(set)
    counts = defaultdict(int)
    nulls = defaultdict(int)
    optional = defaultdict(int)
    missing_columns = set()
    invalid_dates, full_key_nulls = 0, defaultdict(int)
    for frame in _frames(paths, [code, "trade_date"]):
        if "trade_date" not in frame or code not in frame:
            raise ValueError("缺少代码/日期键")
        valid = frame.trade_date.str.fullmatch(r"\d{8}", na=False)
        valid &= pd.to_datetime(frame.trade_date, format="%Y%m%d", errors="coerce").notna()
        invalid_dates += int((~valid).sum())
        for col in [code, "trade_date"]:
            full_key_nulls[col] += int(_null(frame[col]).sum())
        dates.update(frame.loc[valid, "trade_date"])
    wanted = [code, "trade_date"] + required + (["pe", "pb", "ps", "dv_ratio"] if name == "ts_daily_basic" else [])
    for frame in _frames(paths, wanted, start=window, end=end):
        if frame.empty:
            continue
        missing_columns.update(set([code, "trade_date"] + required) - set(frame.columns))
        for col in required:
            if col in frame:
                nulls[col] += int(_null(frame[col]).sum())
        for col in ["pe", "pb", "ps", "dv_ratio"]:
            if col in frame:
                optional[col] += int(_null(frame[col]).sum())
        for day, group in frame.groupby("trade_date"):
            counts[day] += len(group)
            keys[day].update(group.loc[~_null(group[code]), code].astype(str))
    nulls.update(full_key_nulls)
    _metric(metrics, name + "_required_nulls", "FAIL" if missing_columns or invalid_dates or any(nulls.values()) else "OK",
            sum(nulls.values()), {"nulls": dict(nulls), "missing_columns": sorted(missing_columns), "invalid_date_keys": invalid_dates,
                                 "key_scope": "全历史", "start": window, "end": end})
    if optional:
        _metric(metrics, name + "_optional_nulls", "INFO", sum(optional.values()), dict(optional))
    duplicate_rows = sum(counts[d] - len(keys[d]) for d in counts)
    _metric(metrics, name + "_duplicate_keys", "FAIL" if duplicate_rows else "OK", duplicate_rows,
            {d: counts[d] - len(keys[d]) for d in counts if counts[d] != len(keys[d])})
    return dates, keys, counts


def _probes(reader, metrics):
    def execute(name, fn):
        try:
            ok, details = fn()
            _metric(metrics, name, "OK" if ok else "FAIL", details.get("rows", 0), details)
        except Exception as exc:
            _metric(metrics, name, "FAIL", 0, {"error": str(exc)})
    def history():
        frame = reader.read("daily", codes="600519.SH", start="20230720", end="20230804")
        expected = ["20230720", "20230721", "20230724", "20230725", "20230726", "20230727", "20230728",
                    "20230731", "20230801", "20230802", "20230803", "20230804"]
        actual = sorted(set(_day(frame.trade_date))) if "trade_date" in frame else []
        return actual == expected and len(frame) == 12, {"rows": len(frame), "expected_days": expected, "actual_days": actual}
    execute("query_daily_history", history)
    for table, code, start, end in [("cb_daily", "110075.SH", "20260901", "20260930"),
                                    ("income", "600519.SH", None, None),
                                    ("top10_holders", "600519.SH", None, None),
                                    ("top10_floatholders", "600519.SH", None, None)]:
        def query(table=table, code=code, start=start, end=end):
            a = reader.read(table, codes=code, start=start, end=end)
            b = reader.read(table, codes=code[:6], start=start, end=end)
            cols = list(a.columns)
            equal = len(a) > 0 and set(cols) == set(b.columns) and a.astype(str).sort_values(cols).reset_index(drop=True).equals(b[cols].astype(str).sort_values(cols).reset_index(drop=True))
            details = {"rows": len(a), "six_digit_rows": len(b), "equal": equal}
            if table.startswith("top10_"):
                p = [c for c in ["ts_code", "ann_date", "end_date", "holder_name", "holder_type"] if c in a]
                details["detail_duplicate_keys"] = int(a.duplicated(p).sum()) if p else len(a)
                details["max_same_announcement_details"] = int(a.groupby("ann_date").size().max()) if "ann_date" in a and len(a) else 0
                equal = equal and details["detail_duplicate_keys"] == 0 and details["max_same_announcement_details"] > 1
            return equal, details
        execute("query_" + table, query)


def scan_integrity(data_root=None, today=None, reader=None, run_probes=True):
    root = Path(data_root or HERE / "data")
    now = datetime.now(timezone(timedelta(hours=8)))
    explicit_day = today is not None
    today = today or now.strftime("%Y%m%d")
    metrics, tables = [], {}
    try:
        calendar = _calendar(root, today)
        if not explicit_day and now.hour < 15:
            calendar = [d for d in calendar if d < today]
        if not calendar:
            raise ValueError("今天以前没有开市日期")
        _metric(metrics, "calendar", "OK", len(calendar), {"expected_latest": calendar[-1], "checked_today": today})
    except Exception as exc:
        _metric(metrics, "calendar", "FAIL", 0, {"error": str(exc)})
        calendar = []
    latest = calendar[-1] if calendar else today
    recent = calendar[-20:]
    start = recent[0] if recent else today
    for name, (code, required) in TABLES.items():
        try:
            dates, keys, counts = _table(root, name, code, required, start, latest, metrics)
            tables[name] = keys
            if calendar and dates:
                missing = [d for d in calendar if min(dates) <= d <= latest and d not in dates]
                _metric(metrics, name + "_calendar_gaps", "FAIL" if missing else "OK", len(missing),
                        {"missing_days": missing, "first": min(dates), "last": max(dates), "expected_latest": latest})
            elif not dates:
                _metric(metrics, name + "_calendar_gaps", "FAIL", 0, {"error": "无有效日期"})
            base = max((len(v) for v in keys.values()), default=0)
            coverage = {d: {"rows": counts.get(d, 0), "unique_codes": len(keys.get(d, set())),
                            "reference_unique_codes": base,
                            "missing_count": max(0, base - len(keys.get(d, set())))} for d in recent}
            bad = [d for d, v in coverage.items() if base and v["unique_codes"] < .9 * base]
            missing_count = sum(v["missing_count"] for v in coverage.values())
            if name == "tdx_sector_daily" and missing_count:
                bad = [d for d, v in coverage.items() if v["missing_count"]]
            coverage["_summary"] = {"value_unit": "相对近期最大断面的缺键总数",
                                    "reference_unique_codes": base,
                                    "latest_unique_codes": len(keys.get(latest, set())),
                                    "latest_missing_count": max(0, base - len(keys.get(latest, set())))}
            _metric(metrics, name + "_daily_coverage", "FAIL" if bad or not base else "OK", missing_count, coverage)
        except Exception as exc:
            _metric(metrics, name + "_scan", "FAIL", 0, {"error": str(exc)})
    if "ts_daily" in tables and "ts_daily_basic" in tables:
        details = {d: {"daily": len(tables["ts_daily"].get(d, set())),
                       "daily_basic": len(tables["ts_daily_basic"].get(d, set())),
                       "missing": sorted(tables["ts_daily"].get(d, set()) - tables["ts_daily_basic"].get(d, set())),
                       "extra": sorted(tables["ts_daily_basic"].get(d, set()) - tables["ts_daily"].get(d, set()))} for d in recent}
        difference = sum(len(v["missing"]) + len(v["extra"]) for v in details.values())
        _metric(metrics, "daily_basic_key_reconciliation", "FAIL" if difference else "OK", difference, details)
    sector_def = set()
    try:
        paths = _parts(root, "tdx", "tdx_sector_def")
        for frame in _frames(paths, ["code"]):
            sector_def.update(frame.loc[~_null(frame.code), "code"].astype(str))
        if sector_def and "tdx_sector_daily" in tables:
            details = {d: {"defined": len(sector_def), "actual": len(tables["tdx_sector_daily"].get(d, set())),
                           "missing": sorted(sector_def - tables["tdx_sector_daily"].get(d, set())),
                           "extra": sorted(tables["tdx_sector_daily"].get(d, set()) - sector_def)} for d in recent}
            absent = sum(len(v["missing"]) + len(v["extra"]) for v in details.values())
            _metric(metrics, "sector_definition_key_reconciliation", "FAIL" if absent else "OK", absent, details)
        elif not paths:
            _metric(metrics, "sector_definition_key_reconciliation", "WARN", 0, {"error": "板块定义不可用，使用近期唯一代码基准"})
    except Exception as exc:
        _metric(metrics, "sector_definition_key_reconciliation", "FAIL", 0, {"error": str(exc)})
    try:
        active = set()
        for frame in _frames(_parts(root, "tushare", "stock_basic")[-1:], ["ts_code", "list_date", "delist_date", "list_status"]):
            mask = _day(frame.list_date) <= latest
            if "list_status" in frame:
                mask &= frame.list_status.eq("L")
            if "delist_date" in frame:
                mask &= frame.delist_date.isna() | frame.delist_date.eq("") | (_day(frame.delist_date) > latest)
            active.update(frame.loc[mask, "ts_code"].astype(str))
        if not active:
            raise ValueError("最新上市集合为空")
        absent = sorted(active - tables.get("ts_daily", {}).get(latest, set()))
        suspended = sorted(set(absent) & _eligible_suspensions(root, latest))
        unexplained = sorted(set(absent) - set(suspended))
        _metric(metrics, "listed_stock_latest_coverage", "WARN" if unexplained else "OK", len(unexplained),
                {"date": latest, "listed": len(active), "observed": len(active) - len(absent),
                 "eligible_suspended_count": len(suspended), "missing_expected_count": len(unexplained),
                 "missing": absent, "eligible_suspended": suspended, "missing_expected": unexplained,
                 "value_unit": "未解释缺行情股票数", "note": "资格仅限逐日已证全天停牌，实际行情数量保留"})
    except Exception as exc:
        _metric(metrics, "listed_stock_latest_coverage", "FAIL", 0, {"error": str(exc)})
    try:
        manifest_path = root / "tdx" / "_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        minute_days = set()
        minute_codes, minute_counts, minute_nulls = defaultdict(set), defaultdict(int), defaultdict(int)
        total = 0
        for table in ["tdx_sector_min", "tdx_sector_min_tail"]:
            paths = _parts(root, "tdx", table)
            actual = sum(pq.ParquetFile(p).metadata.num_rows for p in paths)
            total += actual
            entry = manifest.get(table, {})
            if table == "tdx_sector_min" or paths:
                expected_parts = entry.get("parts", entry.get("files", entry.get("part")))
                ok = actual == entry.get("rows") and (expected_parts is None or len(paths) == expected_parts)
                _metric(metrics, "sector_minute_manifest" if table == "tdx_sector_min" else "sector_minute_tail_manifest", "OK" if ok else "FAIL", actual,
                        {"path": str(root / "tdx" / table), "actual_parts": len(paths), "expected_parts": expected_parts,
                         "actual_rows": actual, "expected_rows": entry.get("rows")})
            for frame in _frames(paths, ["trade_date"]):
                values = frame.trade_date
                valid = values.str.fullmatch(r"\d{8}", na=False) & pd.to_datetime(values, format="%Y%m%d", errors="coerce").notna()
                minute_nulls["invalid_date_keys"] += int((~valid).sum())
                minute_days.update(values.loc[valid].unique())
            required = ["code", "trade_date", "trade_time", "open", "high", "low", "close", "vol", "amount"]
            for frame in _frames(paths, required, start=start, end=latest):
                for col in required:
                    if col not in frame:
                        raise ValueError("分钟缺少键列: " + col)
                    minute_nulls[col] += int(_null(frame[col]).sum())
                for day, group in frame.groupby("trade_date"):
                    minute_counts[day] += len(group)
                    minute_codes[day].update(group.loc[~_null(group.code), "code"].astype(str))
        missing = [d for d in calendar if minute_days and min(minute_days) <= d <= latest and d not in minute_days]
        _metric(metrics, "sector_minute_calendar_gaps", "FAIL" if missing or not minute_days else "OK", len(missing),
                {"rows": total, "missing_days": missing, "first": min(minute_days) if minute_days else None,
                 "last": max(minute_days) if minute_days else None})
        base = len(sector_def) or max((len(v) for v in minute_codes.values()), default=0)
        details = {d: {"rows": minute_counts[d], "unique_codes": len(minute_codes[d]), "reference_unique_codes": base,
                       "missing": sorted(sector_def - minute_codes[d]) if sector_def else []} for d in recent}
        severe = any(v["unique_codes"] < .9 * base for v in details.values()) if base else True
        _metric(metrics, "sector_minute_daily_coverage", "FAIL" if severe else "OK", base, details)
        _metric(metrics, "sector_minute_required_nulls", "FAIL" if any(minute_nulls.values()) else "OK",
                sum(minute_nulls.values()), dict(minute_nulls))
    except Exception as exc:
        _metric(metrics, "sector_minute_scan", "FAIL", 0, {"error": str(exc)})
    if run_probes:
        try:
            if reader is None:
                import datamart as reader
            _probes(reader, metrics)
        except Exception as exc:
            _metric(metrics, "query_reader", "FAIL", 0, {"error": str(exc)})
    return {"generated_at": now.isoformat(), "data_root": str(root), "expected_latest": latest,
            "session_policy": "显式日期按收盘断面；自动扫描按上海时间15:00前排除当日",
            "window_start": start, "metrics": metrics,
            "fail_count": sum(m["status"] == "FAIL" for m in metrics),
            "warn_count": sum(m["status"] == "WARN" for m in metrics)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = scan_integrity(args.data_root)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    else:
        print(text)
