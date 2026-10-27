# -*- coding: utf-8 -*-
"""只读数据完整度扫描：物理键、日历断面及真实查询，独立于 done 标记。"""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import pandas as pd
import pyarrow.parquet as pq
import logging

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


def _key_evidence(reference, actual, *, sample_size=100):
    """返回稳定的逐键缺口摘要，避免健康报告依赖行顺序或只看数量。"""
    missing = sorted(set(reference) - set(actual))
    digest = hashlib.sha256("\n".join(missing).encode("utf-8")).hexdigest()
    return {
        "missing_keys_count": len(missing),
        "missing_keys_sample": missing[:sample_size],
        "missing_keys_sha256": digest,
    }


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


def _reader_for_root(root):
    """为显式数据根绑定统一读层，避免探针误读当前代码副本。"""
    catalog_path = Path(root) / "catalog.json"
    if not catalog_path.exists():
        return None
    try:
        import datamart
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(catalog, dict) or not isinstance(catalog.get("tables"), dict):
            return None
    except (OSError, ValueError, ImportError):
        return None

    class RootReader:
        def read(self, table, start=None, end=None, codes=None, fields=None, limit=None):
            token = datamart._READ_CATALOG.set(catalog)
            try:
                return datamart.read(table, start=start, end=end, codes=codes,
                                     fields=fields, limit=limit)
            finally:
                datamart._READ_CATALOG.reset(token)

    return RootReader()


def _stock_list_dates(root):
    dates, sources, invalid = {}, [], 0
    for path in _parts(root, "tushare", "stock_basic") + _parts(root, "tdx", "ts_stock_basic"):
        sources.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        for frame in _frames([path], ["ts_code", "list_date"]):
            if not {"ts_code", "list_date"}.issubset(frame.columns):
                continue
            for code, raw in frame[["ts_code", "list_date"]].itertuples(index=False, name=None):
                if pd.isna(code) or pd.isna(raw):
                    continue
                value = str(raw).strip()
                normalized = None
                for fmt in ("%Y%m%d", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(value, fmt)
                        if parsed.strftime(fmt) == value:
                            normalized = parsed.strftime("%Y%m%d")
                            break
                    except ValueError as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/data_integrity.py", exc)
                if normalized is None:
                    invalid += 1
                    continue
                code = str(code)
                dates[code] = min(dates.get(code, normalized), normalized)
    return dates, sources, invalid


def _margin_detail_delistings(root):
    """仅使用已核验调出首日及官方明细范围证据，不推定后续历史资格。"""
    folder = root / "integrity"
    path = folder / "margin_detail_eligibility.json"
    if not path.exists():
        return {}, []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("records"), list):
        raise ValueError("融资明细资格证据版本或记录无效")
    exclusions, evidence = {}, []
    scope = "明细信息中仅包含当前融资融券标的证券的相关数据，汇总信息中包含被调出标的证券范围的证券的余额余量相关数据。"
    for row in payload["records"]:
        code, day = row.get("ts_code", ""), row.get("effective_date", "")
        if not re.fullmatch(r"6\d{5}\.SH", code):
            raise ValueError("融资调出资格股票身份无效")
        if not re.fullmatch(r"\d{8}", day) or datetime.strptime(day, "%Y%m%d").strftime("%Y%m%d") != day:
            raise ValueError("融资调出日期无效")
        if row.get("valid_dates") != [day]:
            raise ValueError("融资调出资格仅限已核验生效首日")
        if datetime.fromisoformat(row["available_at"]).tzinfo is None:
            raise ValueError("融资调出资格采集时间缺少时区")
        sources = row.get("sources", [])
        if len(sources) != 2 or {s.get("kind") for s in sources} != {"removal", "detail_scope"}:
            raise ValueError("融资资格缺少官方调出公告或明细范围原件")
        for source in sources:
            url = urlparse(source["url"])
            if url.scheme != "https" or url.netloc != "www.sse.com.cn":
                raise ValueError("融资资格来源必须为上交所官方域名")
            prefix = "/disclosure/magin/announcement/ssereport/" if source["kind"] == "removal" else "/market/othersdata/margin/detail/"
            if not url.path.startswith(prefix):
                raise ValueError("融资资格来源页面类型不符")
            raw_path = (folder / source["file"]).resolve()
            if not raw_path.is_relative_to(folder.resolve()):
                raise ValueError("融资资格原件路径越界")
            raw = raw_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError("融资资格原件SHA256不符")
            text = re.sub(r"\s+", "", unescape(re.sub(r"<[^>]*>", "", raw.decode("utf-8"))))
            excerpt = re.sub(r"\s+", "", source.get("excerpt", ""))
            if not excerpt or excerpt not in text:
                raise ValueError("融资资格摘录不在原件中")
            if source["kind"] == "removal":
                effective = "%d年%d月%d日" % (int(day[:4]), int(day[4:6]), int(day[6:]))
                if ("证券代码：" + code[:6] not in excerpt or effective not in excerpt
                        or "本所于" + effective + "起将以上证券调出融资融券标的证券名单" not in excerpt):
                    raise ValueError("融资公告未证明该股票于目标日调出")
            elif excerpt != scope:
                raise ValueError("融资官方范围未明确调出证券仅计入汇总")
        if code in exclusions.get(day, set()):
            raise ValueError("融资资格证据重复")
        exclusions.setdefault(day, set()).add(code)
        evidence.append(row)
    return exclusions, evidence


def _margin_coverage(root, keys, counts, recent, latest):
    reference_day = max(recent, key=lambda day: (len(keys.get(day, set())), day)) if recent else None
    reference = keys.get(reference_day, set())
    listed, sources, invalid = _stock_list_dates(root)
    delistings, delisting_sources = _margin_detail_delistings(root)
    coverage, violations = {}, []
    for day in recent:
        actual = keys.get(day, set())
        raw_missing = reference - actual
        excluded = sorted(code for code in raw_missing if code in listed and day < listed[code])
        delisted = sorted((raw_missing - set(excluded)) & delistings.get(day, set()))
        missing = sorted(raw_missing - set(excluded) - set(delisted))
        prelisting_present = sorted(code for code in actual if code in listed and day < listed[code])
        violations.extend({"ts_code": code, "trade_date": day, "list_date": listed[code]} for code in prelisting_present)
        coverage[day] = {"rows": counts.get(day, 0), "unique_codes": len(actual),
            "reference_unique_codes": len(reference), "raw_count": max(0, len(reference) - len(actual)),
            "raw_set": len(raw_missing), "prelisting_excluded_count": len(excluded), "missing_count": len(missing),
            "raw_missing": sorted(raw_missing), "historical_extra": sorted(actual - reference),
            "prelisting_excluded": [{"ts_code": code, "list_date": listed[code]} for code in excluded],
            "delisted_excluded": delisted, "delisted_excluded_count": len(delisted),
            "unknown_list_date_missing": sorted(code for code in raw_missing if code not in listed),
            "missing": missing, "prelisting_present": prelisting_present,
            "source_state": "source_empty_pending" if reference and not counts.get(day, 0) else "observed",
            **_key_evidence(reference, actual)}
    total = sum(row["missing_count"] for row in coverage.values())
    coverage["_summary"] = {"value_unit": "近期参考集合排除已证上市前及官方调出首日后的明细缺键总数",
        "window_start": recent[0] if recent else None, "window_end": latest,
        "reference_date": reference_day, "reference_unique_codes": len(reference),
        "latest_unique_codes": len(keys.get(latest, set())), "latest_missing_count": coverage.get(latest, {}).get("missing_count", 0),
        "gap_keys": _key_evidence(set().union(*(set(row["missing"]) for row in coverage.values())), set()),
        "latest_gap_keys": _key_evidence(reference, keys.get(latest, set())),
        "raw_count": sum(row["raw_count"] for row in coverage.values()),
        "raw_set": sum(row["raw_set"] for row in coverage.values()),
        "prelisting_excluded_count": sum(row["prelisting_excluded_count"] for row in coverage.values()),
        "delisted_excluded_count": sum(row["delisted_excluded_count"] for row in coverage.values()),
        "delisting_sources": delisting_sources,
        "prelisting_present": violations, "list_date_sources": sources, "invalid_list_date_rows": invalid,
        "policy": "近期最大股票集合；平手取最新日；排除已证上市前及有官方调出公告和明细范围原件的生效首日；保留原缺键和实际行；不推定后续日期；未知不豁免；任何未解释缺键或上市前记录均FAIL"}
    return coverage, total, bool(total or violations or not reference)


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
            coverage = {}
            for d in recent:
                actual_keys = keys.get(d, set())
                row = {"rows": counts.get(d, 0), "unique_codes": len(actual_keys),
                       "reference_unique_codes": base,
                       "missing_count": max(0, base - len(actual_keys)),
                       "source_state": "source_empty_pending" if base and not counts.get(d, 0) else "observed"}
                if base and row["missing_count"]:
                    row.update(_key_evidence(set().union(*(keys.get(x, set()) for x in recent)), actual_keys))
                coverage[d] = row
            bad = [d for d, v in coverage.items() if base and v["unique_codes"] < .9 * base]
            missing_count = sum(v["missing_count"] for v in coverage.values())
            if name == "tdx_sector_daily" and missing_count:
                bad = [d for d, v in coverage.items() if v["missing_count"]]
            reference_date = max(recent, key=lambda d: (len(keys.get(d, set())), d), default=latest)
            reference_keys = keys.get(reference_date, set())
            coverage["_summary"] = {"value_unit": "相对近期最大断面的缺键总数",
                                    "window_start": start, "window_end": latest,
                                    "reference_date": reference_date,
                                    "reference_unique_codes": base,
                                    "latest_unique_codes": len(keys.get(latest, set())),
                                    "latest_missing_count": max(0, base - len(keys.get(latest, set()))),
                                    "gap_keys": _key_evidence(
                                        set().union(*(set(reference_keys) - keys.get(d, set()) for d in recent)), set()),
                                    "latest_gap_keys": _key_evidence(reference_keys, keys.get(latest, set()))}
            if name == "ts_margin_detail":
                coverage, missing_count, failed = _margin_coverage(root, keys, counts, recent, latest)
            else:
                failed = bool(bad or not base)
            _metric(metrics, name + "_daily_coverage", "FAIL" if failed else "OK", missing_count, coverage)
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
            details = {}
            for d in recent:
                actual_keys = tables["tdx_sector_daily"].get(d, set())
                missing_keys = sorted(sector_def - actual_keys)
                details[d] = {"defined": len(sector_def), "actual": len(actual_keys),
                              "missing": missing_keys,
                              "extra": sorted(actual_keys - sector_def),
                              "source_state": "source_empty_pending" if not actual_keys else "observed",
                              **_key_evidence(sector_def, actual_keys)}
            absent = sum(len(v["missing"]) + len(v["extra"]) for v in details.values())
            details["_summary"] = {"window_start": start, "window_end": latest,
                                    "reference_date": max(recent, default=latest),
                                    "reference_unique_codes": len(sector_def),
                                    "gap_keys": _key_evidence(
                                        set().union(*(sector_def - tables["tdx_sector_daily"].get(d, set()) for d in recent)), set()),
                                    "latest_gap_keys": _key_evidence(
                                        sector_def, tables["tdx_sector_daily"].get(latest, set()))}
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
        minute_keys, minute_key_rows = defaultdict(set), defaultdict(int)
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
                    # 按日期保留代码/时刻键，合并主仓、尾仓及批次；空键另由必填检查报错。
                    valid_keys = ~(_null(group.code) | _null(group.trade_date) | _null(group.trade_time))
                    keyed = group.loc[valid_keys, ["code", "trade_time"]].astype(str)
                    minute_key_rows[day] += len(keyed)
                    minute_keys[day].update(keyed.itertuples(index=False, name=None))
        missing = [d for d in calendar if minute_days and min(minute_days) <= d <= latest and d not in minute_days]
        _metric(metrics, "sector_minute_calendar_gaps", "FAIL" if missing or not minute_days else "OK", len(missing),
                {"rows": total, "missing_days": missing, "first": min(minute_days) if minute_days else None,
                 "last": max(minute_days) if minute_days else None})
        base = len(sector_def) or max((len(v) for v in minute_codes.values()), default=0)
        details = {}
        for d in recent:
            actual_codes = minute_codes[d]
            missing_codes = sorted(sector_def - actual_codes) if sector_def else []
            row = {"rows": minute_counts[d], "unique_codes": len(actual_codes),
                   "reference_unique_codes": base,
                   "missing": missing_codes,
                   "missing_count": len(missing_codes) if sector_def else max(0, base - len(actual_codes)),
                   "source_state": "source_empty_pending" if base and not minute_counts[d] else "observed"}
            if base and row["missing_count"]:
                row.update(_key_evidence(sector_def if sector_def else set().union(*minute_codes.values()), actual_codes))
            details[d] = row
        severe = any(v["unique_codes"] < .9 * base for v in details.values()) if base else True
        missing_count = sum(v["missing_count"] for v in details.values())
        failed = bool(missing_count) if sector_def else severe
        details["_summary"] = {"value_unit": "近期分钟代码日期断面的缺键总数",
                               "window_start": start, "window_end": latest,
                               "reference_date": max(recent, key=lambda d: (len(minute_codes[d]), d), default=latest),
                               "reference_source": "sector_definition" if sector_def else "recent_max_unique_count",
                               "reference_unique_codes": base,
                               "latest_unique_codes": len(minute_codes[latest]),
                               "latest_missing_count": details.get(latest, {}).get("missing_count", 0),
                               "gap_keys": _key_evidence(
                                   set().union(*(
                                       (sector_def - minute_codes[d]) if sector_def else
                                       (set().union(*minute_codes.values()) - minute_codes[d])
                                       for d in recent)), set()),
                               "latest_gap_keys": _key_evidence(
                                   sector_def if sector_def else set().union(*minute_codes.values()), minute_codes[latest]),
                               "policy": "定义明确的任何缺键均FAIL；无定义保留近期数量参考及90%阈值，不推定历史资格"}
        _metric(metrics, "sector_minute_daily_coverage", "FAIL" if failed else "OK", missing_count, details)
        duplicates = {day: minute_key_rows[day] - len(minute_keys[day]) for day in minute_key_rows
                      if minute_key_rows[day] != len(minute_keys[day])}
        duplicate_rows = sum(duplicates.values())
        _metric(metrics, "sector_minute_duplicate_keys", "FAIL" if duplicate_rows else "OK", duplicate_rows, duplicates)
        _metric(metrics, "sector_minute_required_nulls", "FAIL" if any(minute_nulls.values()) else "OK",
                sum(minute_nulls.values()), dict(minute_nulls))
    except Exception as exc:
        _metric(metrics, "sector_minute_scan", "FAIL", 0, {"error": str(exc)})
    if run_probes:
        try:
            if reader is None:
                reader = _reader_for_root(root)
                if reader is None:
                    raise ValueError("显式数据根缺少可绑定 catalog.json")
            _probes(reader, metrics)
        except Exception as exc:
            _metric(metrics, "query_reader", "FAIL", 0, {"error": str(exc)})
    return {"generated_at": now.isoformat(), "data_root": str(root), "expected_latest": latest,
            "session_policy": "显式日期按收盘断面；自动扫描按上海时间15:00前排除当日",
            "window_start": start, "metrics": metrics,
            "fail_count": sum(m["status"] == "FAIL" for m in metrics),
            "warn_count": sum(m["status"] == "WARN" for m in metrics)}


_TARGET_SCHEMAS = {
    **{name: {"keys": [code, "trade_date"], "required": required}
       for name, (code, required) in TABLES.items()},
    "tdx_sector_min": {"keys": ["code", "trade_date", "trade_time"],
                       "required": ["open", "high", "low", "close", "vol", "amount"]},
}


def _target_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _target_path(root, value):
    """受控根内只读路径；拒绝符号链接及越界，不读取外部资格。"""
    if not isinstance(value, (str, Path)):
        raise ValueError("路径未声明")
    path = Path(value)
    path = path if path.is_absolute() else root / path
    if not path.is_relative_to(root) or any(part == ".." for part in path.parts):
        raise ValueError("路径越界")
    for parent in [path, *path.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("符号链接不属于合格证据")
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("路径越界")
    return resolved


def _target_day(value):
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y%m%d")
    if not isinstance(value, str):
        raise ValueError("日期不是明确文本")
    fmt = "%Y-%m-%d" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else "%Y%m%d"
    parsed = datetime.strptime(value, fmt)
    if parsed.strftime(fmt) != value:
        raise ValueError("日期格式或日期值无效")
    return parsed.strftime("%Y%m%d")


def _target_key(record, keys, day):
    if not isinstance(record, dict) or set(record) != set(keys):
        raise ValueError("资格键结构不符")
    values = []
    for key in keys:
        value = record[key]
        if key == "trade_date":
            value = _target_day(value)
            if value != day:
                raise ValueError("资格键日期与目标不符")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("资格键为空或类型未获核验")
        values.append(value)
    return tuple(values)


def _target_eligibility(root, table, day, rule):
    reference = rule.get("eligibility")
    if not isinstance(reference, dict):
        raise ValueError("没有逐键资格证据")
    path = _target_path(root, reference.get("path"))
    content = path.read_bytes()
    fingerprint = hashlib.sha256(content).hexdigest()
    if reference.get("sha256") != fingerprint:
        raise ValueError("资格版本已变化")
    payload = json.loads(content)
    keys = rule["primary_key"]
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("table") != table
            or payload.get("primary_key") != keys or payload.get("qualification_status") != "qualified"):
        raise ValueError("资格表名、版本或主键未获核验")
    iso_day = datetime.strptime(day, "%Y%m%d").date().isoformat()
    dates = payload.get("dates")
    if not isinstance(dates, dict):
        raise ValueError("资格日期结构无效")
    selected = dates.get(iso_day)
    sources = payload.get("sources")
    if not isinstance(selected, dict) or selected.get("complete") is not True or not isinstance(sources, dict):
        raise ValueError("目标逐键范围不完整")
    calendar = selected.get("calendar")
    if not isinstance(calendar, dict) or calendar.get("is_open") is not True:
        raise ValueError("目标日历不完整或不是开放日")
    source_hashes = {}

    def verify_sources(ids):
        if (not isinstance(ids, list) or not ids or any(not isinstance(i,str) or not i for i in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("缺少逐键来源关联")
        for source_id in ids:
            source = sources.get(source_id)
            if (not isinstance(source, dict) or not isinstance(source.get("valid_dates"),list)
                    or iso_day not in source["valid_dates"]):
                raise ValueError("来源证据不适用目标日")
            source_path = _target_path(root, source.get("path"))
            source_content = source_path.read_bytes()
            if not source_content.strip():
                raise ValueError("空来源原体不能证明逐键资格")
            actual = hashlib.sha256(source_content).hexdigest()
            if source.get("sha256") != actual:
                raise ValueError("来源摘要未核验")
            source_hashes[source_id] = {"path": str(source_path.relative_to(root)), "sha256": actual}

    verify_sources(calendar.get("source_ids"))
    required, excluded = set(), set()
    for field, output in [("keys", required), ("exclusions", excluded)]:
        entries = selected.get(field)
        if not isinstance(entries, list):
            raise ValueError("没有完整逐键资格清单")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("逐键资格结构无效")
            verify_sources(entry.get("source_ids"))
            key = _target_key(entry.get("key"), keys, day)
            if key in output or (field == "exclusions" and not entry.get("reason")):
                raise ValueError("资格重复或排除理由缺失")
            output.add(key)
    if required & excluded or not required:
        raise ValueError("范围冲突或空资格未获专属核验规则")
    return required, excluded, _target_digest({"ledger_sha256": fingerprint, "sources": source_hashes})


def _target_inventory(root, folder):
    inventory = {}
    for candidate in sorted(_target_path(root,folder).glob("part_*.parquet")):
        path = _target_path(root,candidate)
        stat = path.stat()
        inventory[str(path.relative_to(root))] = {"bytes":stat.st_size,"mtime_ns":stat.st_mtime_ns,"file_id":stat.st_ino}
    return inventory


def _target_frame(root, source, table, wanted, day):
    if source not in {"tdx", "tushare"} or table not in _TARGET_SCHEMAS:
        raise ValueError("未登记物理读取规则")
    folder = _target_path(root, Path(source) / table)
    frames, digests, missing = [], {}, set()
    inventory = _target_inventory(root,folder)
    for candidate in inventory:
        path = _target_path(root, candidate)
        pf = pq.ParquetFile(path)
        columns = [col for col in wanted if col in pf.schema_arrow.names]
        if "trade_date" not in columns:
            raise ValueError("无法确定目标范围：日期字段缺失")
        date_index = pf.schema_arrow.names.index("trade_date")
        row_groups = []
        for index in range(pf.metadata.num_row_groups):
            stats = pf.metadata.row_group(index).column(date_index).statistics
            try:
                if stats is not None and _target_day(stats.min) <= day <= _target_day(stats.max):
                    row_groups.append(index)
                elif stats is None:
                    row_groups.append(index)
            except (ValueError,TypeError):
                # 无可信统计必须读实值，不把缺统计当空结果。
                row_groups.append(index)
        if not row_groups:
            continue
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        selected_frames = []
        for batch in pf.iter_batches(batch_size=200000, columns=columns, row_groups=row_groups):
            frame = batch.to_pandas()
            def normalize(raw):
                try:
                    return _target_day(raw)
                except (ValueError, TypeError):
                    return None
            dates = frame.trade_date.map(normalize)
            selected = frame.loc[dates.eq(day)].copy()
            if selected.empty:
                continue
            selected["trade_date"] = day
            missing.update(set(wanted) - set(selected.columns))
            for column in set(wanted) - set(selected.columns):
                selected[column] = pd.NA
            selected_frames.append(selected[wanted])
        after = hashlib.sha256(path.read_bytes()).hexdigest()
        if after != before:
            raise ValueError("读取期间文件变化")
        if selected_frames:
            frames.extend(selected_frames)
            digests[str(path.relative_to(root))] = before
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=wanted)), digests, missing, inventory


def _target_observed(frame, keys):
    valid = pd.Series(True, index=frame.index)
    for key in keys:
        valid &= ~_null(frame[key])
    tuples = [tuple(str(value) for value in row) for row in frame.loc[valid, keys].itertuples(index=False, name=None)]
    return set(tuples), len(tuples) - len(set(tuples)), int((~valid).sum())


def _target_key_dicts(values, keys):
    return [dict(zip(keys, value)) for value in sorted(values)]


def verify_targets(data_root, targets, *, reader=None):
    """只核验冻结单日目标，返回 table 到证据摘要的字典，绝不写仓。

targets 来自服务端计划并附 version=1 的 verification_rule，不能接收客户端资格。
rule 明确 source/primary_key/required_columns/eligibility/query，可选 reconcile。
资格台账逐键绑定受控根内来源摘要；该台账须先由资格流程核验，SHA 本身
    不证明来源文本的业务含义。reader.root 必须由调用者绑定相同 root，在发布保护内
物化 read(table, codes=..., start=..., end=...)；缺 reader 不导入默认生产配置。
status 使用 passed/missing/partial/unknown，供每日视图映射数据状态。
"""
    root_path = Path(data_root)
    if root_path.is_symlink():
        raise ValueError("仓根不接受符号链接")
    root = root_path.resolve()
    if not root.is_dir() or not isinstance(targets, list):
        raise ValueError("需要显式仓根与冻结目标列表")
    result = {}
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("table"), str):
            raise ValueError("目标必须有表名")
        table = target["table"]
        if table in result:
            raise ValueError("同表目标重复；日期范围必须明确拆分调用")
        output = dict(status="unknown", verified_at=datetime.now(timezone.utc).isoformat(),
                      warehouse_digest=None, eligibility_digest=None, metrics={}, reason="verification_unknown")
        result[table] = output
        if target.get("status") == "waiting_release":
            output.update(status="waiting_release", reason="not_due")
            continue
        if target.get("status") != "due" or table not in _TARGET_SCHEMAS:
            output["reason"] = "target_or_table_unknown"
            continue
        rule = target.get("verification_rule")
        if not isinstance(rule, dict) or rule.get("version") != 1 or not rule.get("rule_id"):
            output["reason"] = "verification_rule_unknown"
            continue
        schema = _TARGET_SCHEMAS[table]
        keys, required = rule.get("primary_key"), rule.get("required_columns")
        if keys != schema["keys"] or required != schema["required"]:
            output["reason"] = "schema_qualification_unknown"
            continue
        try:
            day = _target_day(target.get("target"))
            expected, excluded, eligibility_digest = _target_eligibility(root, table, day, rule)
            output["eligibility_digest"] = eligibility_digest
        except (OSError, ValueError, TypeError, KeyError):
            output["reason"] = "eligibility_unknown"
            continue
        wanted = list(dict.fromkeys(keys + required))
        try:
            frame, fingerprints, missing_columns, inventory = _target_frame(root, rule.get("source"), table, wanted, day)
            inventories = {str(Path(rule["source"])/table):inventory}
            observed, duplicates, null_keys = _target_observed(frame, keys)
        except (OSError, ValueError, TypeError, KeyError):
            output["reason"] = "warehouse_read_failed"
            continue
        metrics = dict(scope={"start": datetime.strptime(day,"%Y%m%d").date().isoformat(),
                              "end": datetime.strptime(day,"%Y%m%d").date().isoformat()},
            history_verified=False, observed_rows=len(frame), observed_unique_keys=len(observed),
            expected_keys=len(expected), missing_keys=_target_key_dicts(expected-observed,keys),
            extra_keys=_target_key_dicts(observed-expected-excluded,keys),
            excluded_keys=_target_key_dicts(excluded,keys), duplicate_rows=duplicates,
            null_key_rows=null_keys, required_nulls={column:int(_null(frame[column]).sum()) for column in required},
            missing_columns=sorted(missing_columns), query={"status":"unknown"})
        output["metrics"] = metrics
        problems = bool(metrics["missing_keys"] or metrics["extra_keys"] or duplicates or null_keys
                        or missing_columns or any(metrics["required_nulls"].values()))
        reason = "target_incomplete" if problems else None
        reconciliation = rule.get("reconcile")
        if reconciliation:
            try:
                if not isinstance(reconciliation, dict) or reconciliation.get("primary_key") != keys:
                    raise ValueError("跨表对账规则未核验")
                other, other_sha, missing, other_inventory = _target_frame(root, reconciliation.get("source"),
                    reconciliation.get("table"), keys, day)
                other_keys, other_duplicates, other_nulls = _target_observed(other, keys)
                fingerprints.update(other_sha)
                inventories[str(Path(reconciliation["source"])/reconciliation["table"])] = other_inventory
                metrics["reconciliation"] = dict(missing_keys=_target_key_dicts(observed-other_keys,keys),
                    extra_keys=_target_key_dicts(other_keys-observed,keys), duplicate_rows=other_duplicates,
                    null_key_rows=other_nulls)
                if missing or other_duplicates or other_nulls or other_keys != observed:
                    problems, reason = True, "reconciliation_failed"
            except (OSError, ValueError, TypeError, KeyError):
                output["reason"] = "reconciliation_unknown"
                continue
        output["warehouse_digest"] = _target_digest({"files":fingerprints,"parts":inventories})
        query = rule.get("query")
        if (not isinstance(query, dict) or not isinstance(query.get("table"), str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}",query["table"])
                or not isinstance(query.get("codes"), list)
                or any(not isinstance(code,str) or not code for code in query["codes"])):
            output["reason"] = "query_rule_unknown"
            continue
        code_column = keys[0]
        expected_codes = {key[0] for key in expected}
        if not expected_codes <= set(query["codes"]):
            output["reason"] = "query_scope_incomplete"
            continue
        if reader is None or not callable(getattr(reader, "read", None)):
            output["reason"] = "query_reader_unavailable"
            continue
        try:
            if Path(reader.root).resolve() != root:
                raise ValueError("查询仓根不一致")
        except (AttributeError, ValueError, TypeError, OSError):
            output["reason"] = "query_root_unknown"
            continue
        try:
            queried = reader.read(query["table"], codes=query["codes"], start=day, end=day)
            if not isinstance(queried, pd.DataFrame) or set(wanted) - set(queried.columns):
                raise ValueError("查询未物化或缺字段")
            queried = queried[wanted].copy()
            queried["trade_date"] = queried.trade_date.map(_target_day)
            if not queried.trade_date.eq(day).all():
                raise ValueError("查询返回目标外行")
            query_keys, query_duplicates, query_nulls = _target_observed(queried, keys)
            physical = frame.loc[frame[code_column].astype(str).isin(query["codes"]), wanted]
            def canonical(value):
                return value.sort_values(keys).reset_index(drop=True)
            equal = (query_keys == observed and not query_duplicates and not query_nulls
                     and canonical(queried).equals(canonical(physical)))
            metrics["query"] = {"status":"passed" if equal else "failed", "observed_unique_keys":len(query_keys)}
            if not equal:
                problems, reason = True, "query_result_mismatch"
        except Exception as exc:
            from warehouse_commit import PublicationPending
            if isinstance(exc, PublicationPending):
                raise
            # 不保存查询异常自由文本，避免连接串或认证材料进入核验摘要。
            metrics["query"] = {"status":"failed", "error_kind":type(exc).__name__}
            problems, reason = True, "query_failed"
        # reader可能触发缓存或长查询；再次核文件/资格，版本变化不发布旧绿证据。
        try:
            _, _, current_eligibility = _target_eligibility(root,table,day,rule)
            if current_eligibility != eligibility_digest or any(
                    hashlib.sha256(_target_path(root,path).read_bytes()).hexdigest() != fingerprint
                    for path,fingerprint in fingerprints.items()) or any(
                    _target_inventory(root,folder) != inventory
                    for folder,inventory in inventories.items()):
                raise ValueError("核验版本已变化")
        except (OSError, ValueError, TypeError, KeyError):
            output.update(status="unknown",reason="verification_snapshot_changed")
            continue
        output.update(status="missing" if not observed else "partial" if problems else "passed", reason=reason)
    return result


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
