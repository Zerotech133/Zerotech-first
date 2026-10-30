# -*- coding: utf-8 -*-
"""新鲜度报告：六表 prod_max（footer，不凭 mtime）+ 4 库停摆（只读 sqlite）。

用法：python tools/datawarehouse/freshness_report.py --json
退出码：0=全部达标 / 1=存在滞后 / 2=依赖缺失（pyarrow 或数据根）
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

DATE_COL = {"daily": "trade_date", "adj_factor": "trade_date", "daily_basic": "trade_date",
            "index_daily": "trade_date", "moneyflow": "trade_date", "margin_detail": "trade_date"}
# 管理期望：T+1 交易日内必须有数据（用自然日 2 天做宽松阈值，避免周末误报）
EXPECTED_LAG_DAYS = 2
# 源侧发布节奏例外（自然日兜底，2026-10-27 DIAG2）：margin_detail 由交易所
# T+1 上午发布，21:37 增量守卫取到的是「昨日已发布」数据；叠加周末最长 4 个
# 自然日仍正常（周一 10:33 时最新=上周五 20261023，非停摆）。
TABLE_LAG_DAYS = {"margin_detail": 4}
# 频率 → 期望最大滞后（自然日）；freq 词表见 build_catalog FREQ_ENUM
FREQ_LAG_DAYS = {"D": 2, "W": 10, "M": 45, "Q": 120, "A": 400, "H": 1, "T": 1}

STALL_DBS = [
    {"name": "quant.db", "path": common.DATA / "quant.db",
     "sql": "select max(detect_date) from chanlun_pool", "label": "chanlun_pool.detect_date", "max_days": 2},
    {"name": "ann_history.db", "path": Path(r"C:\项目\zerobound-kg\data\ann_history\ann_history.db"),
     "sql": "select max(notice_date) from ann", "label": "ann.notice_date", "max_days": 2},
    # 手工 staging 台账（entry 全为 manual，2026-10-11 后无写入）：不是心跳表，
    # 只有在出现非 manual 的定时写入时才按 max_days 判停摆。
    {"name": "warehouse_runs.sqlite3", "path": common.DATA / "tushare_staging" / "warehouse_runs.sqlite3",
     "sql": "select max(created_at) from runs", "label": "runs.created_at", "max_days": 7,
     "manual_only_sql": "select count(*) from runs where entry<>'manual'"},
    # trade_day 是「新闻日 T 的下一个 SSE 交易日（T+1 持有日）」，天然可能在未来；
    # 台账活性看 updated_at（写入心跳），trade_day 只作信息字段输出。
    {"name": "jev_audit.db", "path": common.REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db",
     "sql": "select max(substr(updated_at,1,10)) from dec_audit", "label": "dec_audit.updated_at", "max_days": 2,
     "info_sql": "select max(trade_day) from dec_audit", "info_label": "dec_audit.trade_day(T+1目标日)"},
]


def date_from_name(name: str) -> str | None:
    m = re.search(r"(20\d{6})", name)
    return m.group(1) if m else None


def _norm_date(v) -> str | None:
    if v is None:
        return None
    s = re.sub(r"[^0-9]", "", str(v))[:8]
    return s if len(s) == 8 else None


def table_latest_date(table: str, date_col: str) -> tuple[str | None, str]:
    try:
        import pyarrow.parquet as pq  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return None, "pyarrow_missing"
    catalog = common.load_json(common.CATALOG) or {}
    entry = (catalog.get("tables") or {}).get(table) or {}
    store = Path(str(entry.get("store") or ""))
    if not store.exists():
        return None, "store_missing"
    files = sorted(store.glob("*.parquet"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    if not files:
        return None, "no_parquet"
    vals = []
    for f in files:
        try:
            pf = pq.ParquetFile(f)
            if pf.metadata.num_row_groups == 0:
                continue
            tb = pf.read_row_group(pf.metadata.num_row_groups - 1, columns=[date_col])
            v = [_norm_date(x) for x in tb.column(date_col).to_pylist()]
            v = [x for x in v if x]
            if v:
                vals.append(max(v))
        except Exception:  # noqa: BLE001
            continue
    if vals:
        return max(vals), "footer"
    # 退化：文件名日期（标注 degraded）
    names = [date_from_name(f.name) for f in files]
    names = [n for n in names if n]
    return (max(names), "filename_degraded") if names else (None, "unknown")


def _parse_day(v) -> dt.date | None:
    """解析日期：date/datetime、YYYYMMDD、ISO(2026-10-26)、epoch 秒/毫秒。

    2026-10-28 修复：原实现先 float() 再回退，ISO 日期字符串会触发
    silent-except 告警（"could not convert string to float: '2026-10-26'"）。
    日期字符串优先按日期解析；epoch 仅接受 10~13 位数字。
    """
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v).strip() if v is not None else ""
    if not s:
        return None
    # epoch 秒/毫秒（纯 10~13 位数字，可带小数）
    if re.fullmatch(r"\d{10,13}(?:\.\d+)?", s):
        f = float(s)
        if f > 1e12:  # 毫秒
            f = f / 1000.0
        return dt.datetime.fromtimestamp(f).date()
    # 日期：20261026 / 2026-10-26 / 2026-10-26T00:30:00 / 2026/10/26
    m = re.match(r"^(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})(?:$|[T ])", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def sqlite_latest(db: Path, sql: str):
    if not Path(db).exists():
        return None, "db_missing"
    try:
        con = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True, timeout=3)
        try:
            row = con.execute(sql).fetchone()
        finally:
            con.close()
        return (row[0] if row else None), "ok"
    except Exception as e:  # noqa: BLE001
        return None, "sql_error:" + str(e)[:60]


def expected_lag(table: str, entry: dict | None = None) -> int | None:
    """按频率给期望滞后（自然日）：D=2 / W=10 / M=45 / Q=120 / A=400。

    返回 None 表示跳过检查（lifecycle=planned/deprecated/archived、deprecated=True、
    static_no_date=True 或 freq 未知）。
    """
    try:
        import lifecycle  # noqa: WPS433
        if lifecycle.skipped(table):
            return None
    except Exception:  # noqa: BLE001
        pass
    entry = entry or {}
    if entry.get("deprecated") or entry.get("static_no_date"):
        return None
    freq = str(entry.get("freq") or "D").upper()
    if freq in ("STATIC", "EVENT", "NONE"):
        return None
    if table in TABLE_LAG_DAYS:
        return TABLE_LAG_DAYS[table]
    return FREQ_LAG_DAYS.get(freq, EXPECTED_LAG_DAYS)


def collect() -> dict:
    today = dt.date.today()
    checks, issues = [], []
    catalog = common.load_json(common.CATALOG) or {}
    catalog_tables = catalog.get("tables") or {}
    for table, col in DATE_COL.items():
        entry = catalog_tables.get(table) or {}
        limit = expected_lag(table, entry)
        if limit is None:
            checks.append({"tool": "table", "table": table, "col": col,
                           "skipped": "lifecycle_or_static", "ok": True})
            continue
        latest, how = table_latest_date(table, col)
        d = _parse_day(latest)
        lag = (today - d).days if d else None
        future = bool(d and d > today)
        ok = bool(d and not future and lag is not None and lag <= limit)
        check = {"tool": "table", "table": table, "latest": latest, "col": col,
                 "source": how, "lag_days": lag, "future_date": future, "ok": ok}
        if table in TABLE_LAG_DAYS:
            check["lag_limit_days"] = limit
            check["note"] = "源侧 T+1 发布口径，阈值 %d 自然日" % limit
        checks.append(check)
        if future:
            issues.append("%s 出现未来日期 %s" % (table, latest))
        elif not ok:
            issues.append("%s 最新=%s（滞后 %s 天）" % (table, latest, lag))
    for spec in STALL_DBS:
        latest, how = sqlite_latest(spec["path"], spec["sql"])
        d = _parse_day(latest)
        lag = (today - d).days if d else None
        future = bool(d and d > today)
        ok = bool(d and not future and lag is not None and lag <= spec["max_days"])
        note = spec.get("note", "")
        if spec.get("manual_only_sql"):
            n_auto, _ = sqlite_latest(spec["path"], spec["manual_only_sql"])
            if n_auto == 0:
                ok, note = True, "手工台账（无定时写入），不按心跳判停摆"
        check = {"tool": "sqlite", "db": spec["name"], "label": spec["label"],
                 "latest": str(latest), "source": how, "lag_days": lag,
                 "future_date": future, "ok": ok}
        if note:
            check["note"] = note
        if spec.get("info_sql"):
            info, _ = sqlite_latest(spec["path"], spec["info_sql"])
            check["info"] = {spec.get("info_label", "info"): str(info)}
        checks.append(check)
        if future:
            issues.append("%s 出现未来日期：%s=%s" % (spec["name"], spec["label"], latest))
        elif not ok:
            issues.append("%s 停摆：%s 滞后 %s 天" % (spec["name"], spec["label"], lag))
    return {"tool": "freshness_report",
            "code": common.EXIT_WARN if issues else common.EXIT_OK,
            "as_of": today.isoformat(), "checks": checks, "issues": issues}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新鲜度报告（只读）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return common.emit(collect(), as_json=a.json, title="freshness_report")


if __name__ == "__main__":
    raise SystemExit(main())
