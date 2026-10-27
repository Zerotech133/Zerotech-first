# -*- coding: utf-8 -*-
"""数据补齐：tushare 扩展表 -> 本地缓存（不写 curated 仓库）。

- 按股票：report_rc / forecast / express / stk_holdertrade / share_float / pledge_stat /
  repurchase / block_trade / dividend / fina_mainbz / stk_surv / irm_qa_sh / suspend_d / namechange
- 按交易日：top_list / top_inst / limit_list_d / stk_limit / moneyflow_hsgt

用法：
    python -m analysis.sync --codes 600337.SH,688023.SH --days 30
    python -m analysis.sync --codes 600337.SH --only report_rc,forecast,irm_qa_sh
    python -m analysis.sync --daily-only --days 30
"""
import argparse
import json
import os
import sys
import time

import pandas as pd

from . import config
import logging

TABLES = os.path.join(config.DATA_CACHE, "tables")
BY_CODE = os.path.join(TABLES, "by_code")
DAILY = os.path.join(TABLES, "daily")
META = os.path.join(TABLES, "_meta.json")

CODE_SPECS = {
    "report_rc": {"date": ("start_date", "end_date", 1095)},
    "forecast": {"date": ("start_date", "end_date", 1095)},
    "express": {"date": ("start_date", "end_date", 1095)},
    "stk_holdertrade": {"date": ("start_date", "end_date", 730)},
    "share_float": {"date": ("start_date", "end_date", 900), "future": True},
    "pledge_stat": {},
    "repurchase": {"date": ("start_date", "end_date", 730)},
    "block_trade": {"date": ("start_date", "end_date", 365)},
    "dividend": {},
    "fina_mainbz": {"date": ("start_date", "end_date", 1095), "extra": {"type": "P"}},
    "stk_surv": {},
    "irm_qa_sh": {},
    "irm_qa_sz": {},
    "suspend_d": {"date": ("start_date", "end_date", 365)},
    "namechange": {},
    "index_member_all": {},
}
DAILY_SPECS = ["top_list", "top_inst", "limit_list_d", "stk_limit", "moneyflow_hsgt"]


def _ts_call(api, params):
    sys.path.insert(0, os.path.join(config.MAIN_ROOT, "quant_backtest"))
    import tushare_sync as T
    return T.ts_call(api, params)


def _ymd(d):
    return str(d).replace("-", "")


def _meta():
    os.makedirs(TABLES, exist_ok=True)
    if os.path.exists(META):
        try:
            return json.load(open(META, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(meta):
    os.makedirs(TABLES, exist_ok=True)
    json.dump(meta, open(META, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _upsert(df, path, keys):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and len(df):
        try:
            old = pd.read_parquet(path)
            df = pd.concat([old, df], ignore_index=True)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/sync.py", exc)
    if len(df):
        df = df.drop_duplicates(subset=[k for k in keys if k in df.columns], keep="last")
        df.to_parquet(path, index=False)
    return len(df)


def sync_code(code, as_of=None, only=None, days=1095):
    """同步单只股票的按股票表。返回 {table: rows}。"""
    as_of = as_of or time.strftime("%Y-%m-%d")
    ymd = _ymd(as_of)
    meta = _meta()
    out = {}
    for table, spec in CODE_SPECS.items():
        if only and table not in only:
            continue
        params = {"ts_code": code}
        if "date" in spec:
            k1, k2, lookback = spec["date"]
            params[k1] = str(int(ymd[:4]) - max(1, lookback // 365)) + ymd[4:]
            params[k2] = ymd
        if "future" in spec:
            params["end_date"] = str(int(ymd[:4]) + 2) + "1231"
        params.update(spec.get("extra") or {})
        try:
            r = _ts_call(table, params)
            fields = r.get("fields") or []
            items = r.get("items") or []
            df = pd.DataFrame(items, columns=fields) if fields else pd.DataFrame()
            if not len(df):
                out[table] = 0
                continue
            path = os.path.join(BY_CODE, table, code.replace(".", "_") + ".parquet")
            n = _upsert(df, path, ["end_date", "ann_date", "report_date", "trade_date", "surv_date"])
            out[table] = n
            meta.setdefault(table, {})["last_sync"] = time.strftime("%F %T")
            meta[table]["rows"] = int(meta[table].get("rows", 0)) + len(df)
        except Exception as e:
            out[table] = "ERR:" + str(e)[:40]
    _save_meta(meta)
    return out


def trade_dates(days=30):
    p = os.path.join(config.CURATED, "trade_cal.parquet")
    cal = pd.read_parquet(p)
    cal = cal[cal["is_open"] == 1]
    cal["cal_date"] = cal["cal_date"].astype(str)
    today = time.strftime("%Y%m%d")
    cal = cal[cal["cal_date"] <= today].sort_values("cal_date")
    return cal["cal_date"].tolist()[-days:]


def sync_daily(days=30, only=None):
    """同步按交易日的全市场表。返回 {table: rows}。"""
    meta = _meta()
    out = {}
    dates = trade_dates(days)
    for table in DAILY_SPECS:
        if only and table not in only:
            continue
        frames = []
        for d in dates:
            try:
                r = _ts_call(table, {"trade_date": d})
                fields = r.get("fields") or []
                if fields and r.get("items"):
                    frames.append(pd.DataFrame(r["items"], columns=fields))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/sync.py", exc)
        if not frames:
            out[table] = 0
            continue
        df = pd.concat(frames, ignore_index=True)
        path = os.path.join(DAILY, table + ".parquet")
        n = _upsert(df, path, ["trade_date", "ts_code", "exalter"])
        out[table] = n
        meta.setdefault(table, {})["last_sync"] = time.strftime("%F %T")
        meta[table]["rows"] = n
    _save_meta(meta)
    return out


# ---------- 读取接口（features/verdict 使用） ----------

def load_code(table, code):
    p = os.path.join(BY_CODE, table, code.replace(".", "_") + ".parquet")
    if not os.path.exists(p):
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def load_daily(table):
    p = os.path.join(DAILY, table + ".parquet")
    if not os.path.exists(p):
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def status():
    meta = _meta()
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--only", default="")
    ap.add_argument("--daily-only", action="store_true")
    a = ap.parse_args()
    only = set(x.strip() for x in a.only.split(",") if x.strip()) or None
    res = {}
    if not a.daily_only:
        for code in [c.strip() for c in a.codes.split(",") if c.strip()]:
            res[code] = sync_code(code, a.as_of, only)
    if a.daily_only or not a.only:
        res["_daily"] = sync_daily(a.days, only)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
