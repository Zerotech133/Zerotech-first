# -*- coding: utf-8 -*-
"""TDX 数据仓统一读取层（只读）。

读 data/tdx/ 下的 parquet 分片表与 tdx_info.db 三张信息表，
所有策略经此读数，tushare API 仅作兜底/增量，不再直连。

布局（与 tdx_sync.py 对齐）：
· data/tdx/<table>/part_NNNN.parquet   分片，tushare 原始 schema
· data/tdx/tdx_info.db                 公告/个股新闻/研报（SQLite）
· data/tdx/_manifest.json              同步进度（done 标志）

日期口径：trade_date 一律 'YYYYMMDD' 字符串；_norm_day 兼容
'2026-09-26' / '20260926' / date / datetime。

用法：
  from tdx_read import read_day, read_range, read_info, day_map, table_ready
  df = read_day("ts_daily_basic", "2026-09-26")                 # 单日全市场
  df = read_day("ts_top_list", "20260926", codes=["600519.SH"])
  df = read_range("ts_margin_detail", "20260901", "20260926")   # 含两端
  rows = read_info("stock_report", codes="603859", limit=20)    # list[dict]
  m = day_map("ts_daily_basic", "20260926",
              ["pe", "total_mv", "turnover_rate"])              # {code: {...}}
"""

import glob
import json
import os
import re
import sqlite3
from datetime import date, datetime

import pandas as pd
from pathlib import Path

from warehouse_read_guard import PublicationPending, guarded_read, read_resources
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tdx")
INFO_DB = os.path.join(OUT, "tdx_info.db")
MANIFEST = os.path.join(OUT, "_manifest.json")

SQLITE_TABLES = {"stock_notice", "stock_news", "stock_report"}
# 财务类表的日期列是 end_date，其余结构化表用 trade_date
DATE_COL = {
    "ts_income": "end_date",
    "ts_balancesheet": "end_date",
    "ts_cashflow": "end_date",
    "ts_fina_indicator": "end_date",
}
# tdx_sector_daily 用 code 而非 ts_code
CODE_COL_DEFAULT = "ts_code"


@guarded_read(lambda *args, **kwargs: [Path(MANIFEST).parent])
def _manifest():
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST, encoding="utf-8") as f:
                return json.load(f)
        except PublicationPending:
            raise
        except Exception:
            return {}
    return {}


@guarded_read(lambda *args, **kwargs: [Path(MANIFEST).parent])
def table_ready(tbl):
    """同步器是否已把该表标记完成（未完成的表读到的是不完整数据）。"""
    return bool(_manifest().get(tbl, {}).get("done"))


@guarded_read(lambda *args, **kwargs: [OUT])
def latest_date(tbl):
    """表内最新 trade_date（'YYYYMMDD'）；空表返回 None。

    远端数据不按日期排序，必须全分片取 max（只读日期列，代价可忽略）。
    """
    parts = _parts(tbl)
    if not parts:
        return None
    try:
        dcol = date_col_of(tbl)
        vals = []
        for p in parts:
            vals.extend(pd.read_parquet(p, columns=[dcol])[dcol].astype(str))
        vals = [v[:8] for v in vals if v and v[:8].isdigit()]
        return max(vals) if vals else None
    except PublicationPending:
        raise
    except Exception:
        return None


@guarded_read(lambda *args, **kwargs: [OUT, Path(MANIFEST).parent])
def fresh(tbl, day):
    """表已同步且覆盖到 day（增量任务兜底判断用）。"""
    if not table_ready(tbl):
        return False
    ld = latest_date(tbl)
    return bool(ld) and ld >= _norm_day(day)


def date_col_of(tbl):
    return DATE_COL.get(tbl, "trade_date")


def _parts(tbl):
    return sorted(glob.glob(os.path.join(OUT, tbl, "part_*.parquet")))


def _norm_day(d):
    """任意日期形态 → 'YYYYMMDD'。"""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    s = str(d).strip()
    if re.fullmatch(r"\d{8}", s):
        return s
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return "%s%02d%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    return s


def _norm_codes(codes):
    """ts_code 列表化；6 位纯数字自动按前缀匹配。"""
    if codes is None:
        return None, None
    if isinstance(codes, str):
        codes = [codes]
    exact = [c for c in codes if not re.fullmatch(r"\d{6}", str(c))]
    prefix = [str(c) for c in codes if re.fullmatch(r"\d{6}", str(c))]
    return exact or None, prefix or None


def _apply_code_filter(df, tbl, codes):
    exact, prefix = _norm_codes(codes)
    if exact is None and prefix is None:
        return df
    cols = [c for c in ("ts_code", "con_code", "code", "symbol") if c in df.columns]
    if not cols:
        return df.iloc[0:0]
    mask = pd.Series(False, index=df.index)
    for col in cols:
        s = df[col].astype(str)
        if exact:
            mask |= s.isin([str(c) for c in exact])
        if prefix:
            mask |= s.str[:6].isin(prefix)
    return df[mask]


@guarded_read(lambda *args, **kwargs: [OUT])
def read_day(tbl, day, codes=None, columns=None):
    """读某交易日（或 end_date）全表/指定票，返回 DataFrame（空表给空 df）。"""
    if tbl in SQLITE_TABLES:
        raise ValueError("%s 是信息表，请用 read_info()" % tbl)
    day = _norm_day(day)
    parts = _parts(tbl)
    if not parts or not day:
        return pd.DataFrame()
    dcol = date_col_of(tbl)
    cols = None
    if columns:
        want = set(columns) | {dcol}
        try:  # 用首片探测真实列名，避免请求不存在的列
            head = pd.read_parquet(parts[0])
            cols = [c for c in head.columns if c in want or c == _code_col(head)]
        except PublicationPending:
            raise
        except Exception:
            cols = None
    try:
        df = pd.concat(
            [pd.read_parquet(p, columns=cols) for p in parts], ignore_index=True
        )
    except PublicationPending:
        raise
    except Exception:
        return pd.DataFrame()
    if dcol not in df.columns:
        return pd.DataFrame()
    df = df[df[dcol].astype(str).str[:8] == day]
    return _apply_code_filter(df, tbl, codes).reset_index(drop=True)


@guarded_read(lambda *args, **kwargs: [OUT])
def read_range(tbl, start=None, end=None, codes=None, columns=None):
    """读 [start, end] 闭区间；start/end 任一可省。"""
    if tbl in SQLITE_TABLES:
        raise ValueError("%s 是信息表，请用 read_info()" % tbl)
    parts = _parts(tbl)
    if not parts:
        return pd.DataFrame()
    dcol = date_col_of(tbl)
    try:
        df = pd.concat(
            [pd.read_parquet(p, columns=columns) for p in parts], ignore_index=True
        )
    except PublicationPending:
        raise
    except Exception:
        return pd.DataFrame()
    if dcol not in df.columns:
        return pd.DataFrame()
    s = df[dcol].astype(str).str[:8]
    if start is not None:
        df = df[s >= _norm_day(start)]
        s = df[dcol].astype(str).str[:8]
    if end is not None:
        df = df[s <= _norm_day(end)]
    df = _apply_code_filter(df, tbl, codes)
    return df.sort_values(dcol).reset_index(drop=True)


@guarded_read(lambda *args, **kwargs: [OUT])
def read_all(tbl, columns=None, codes=None):
    """读整表（无日期列的映射表如 ts_ths_member 用）。"""
    parts = _parts(tbl)
    if not parts:
        return pd.DataFrame()
    try:
        df = pd.concat(
            [pd.read_parquet(p, columns=columns) for p in parts], ignore_index=True
        )
    except PublicationPending:
        raise
    except Exception:
        return pd.DataFrame()
    return _apply_code_filter(df, tbl, codes).reset_index(drop=True)


@guarded_read(lambda *args, **kwargs: [OUT])
def day_map(tbl, day, fields, codes=None):
    """单日 → {ts_code: {field: value}}，nine_dim 各 fetch 函数的直接替身。"""
    df = read_day(tbl, day, codes=codes, columns=list(fields) if fields else None)
    if df.empty:
        return {}
    code_col = _code_col(df)
    out = {}
    for r in df.to_dict("records"):
        c = r.get(code_col)
        if c:
            out[c] = {f: r.get(f) for f in fields}
    return out


def _code_col(df):
    for c in (CODE_COL_DEFAULT, "code", "symbol"):
        if c in df.columns:
            return c
    return None


@guarded_read(lambda *args, **kwargs: [Path(INFO_DB).parent])
def read_info(tbl, codes=None, limit=200, since=None):
    """信息三表（公告/个股新闻/研报）→ list[dict]，按日期倒序。

    codes：ts_code 精确或 6 位前缀；since：'YYYYMMDD' 起始。
    参数化查询，防注入。
    """
    if tbl not in SQLITE_TABLES:
        raise ValueError("%s 不是信息表" % tbl)
    if not os.path.exists(INFO_DB):
        return []
    date_col = {
        "stock_notice": "notice_date",
        "stock_news": "pub_date",
        "stock_report": "pub_date",
    }[tbl]
    where, params = [], []
    if codes:
        exact, prefix = _norm_codes(codes)
        if exact:
            where.append("ts_code IN (%s)" % ",".join("?" * len(exact)))
            params.extend(str(c) for c in exact)
        if prefix:
            where.append("substr(ts_code,1,6) IN (%s)" % ",".join("?" * len(prefix)))
            params.extend(prefix)
    if since:
        where.append("%s >= ?" % date_col)
        nd = _norm_day(since)  # 库内存 'YYYY-MM-DD'
        params.append("%s-%s-%s" % (nd[:4], nd[4:6], nd[6:8]))
    sql = "SELECT * FROM %s" % tbl  # nosec B608 - 值参数化/内部标识符（已抽查）
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY %s DESC LIMIT %d" % (date_col, int(limit))
    try:
        conn = sqlite3.connect(INFO_DB, timeout=30)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(sql, params)]
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


if __name__ == "__main__":
    with read_resources([OUT, Path(INFO_DB).parent, Path(MANIFEST).parent]):
        m = _manifest()
        print("%-22s %-6s %-10s" % ("table", "ready", "latest"))
        for t in sorted(m):
            if t in SQLITE_TABLES:
                n = 0
                if os.path.exists(INFO_DB):
                    conn = sqlite3.connect(INFO_DB)
                    try:
                        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）
                    except sqlite3.OperationalError as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tdx_read.py", exc)
                    conn.close()
                print("%-22s %-6s %-10s" % (t, "Y", "%d rows" % n))
            else:
                print(
                    "%-22s %-6s %-10s"
                    % (t, "Y" if table_ready(t) else "-", latest_date(t) or "—")
                )
