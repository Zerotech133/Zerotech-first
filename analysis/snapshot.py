# -*- coding: utf-8 -*-
"""S1 数据快照：本地 curated 仓库 + 少量 tushare 在线补齐；产出 domains/as-of/覆盖率。"""
import json
import os
import sqlite3

import pandas as pd

from . import config
import logging


def _q(name):
    return os.path.join(config.CURATED, name + ".parquet")


def _read(table, code, cols=None):
    p = _q(table)
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    if "ts_code" in df.columns:
        df = df[df["ts_code"] == code]
    if cols:
        df = df[[c for c in cols if c in df.columns]]
    return df


def _latest_date(df, col="trade_date"):
    if df is None or not len(df) or col not in df.columns:
        return None
    return str(df[col].astype(str).max())[:10]


def _ymd(d):
    return str(d).replace("-", "")


def _map_industry(code, basic_industry, mem, cls):
    """P0M：行业映射纠偏（返回 {index_code,l1_name,match,level}）。

    优先用 stock_basic.industry 名称匹配申万分类（L2/L3 命中后沿 parent_code 上溯到 L1）；
    匹配不到才回退 sw_l1_member（历史数据存在陈旧映射，如 300308 被标成电力设备）。
    注意：sw_classify_l1.parent_code 存的是 industry_code（如 730000），不是 index_code，
    因此上溯时按 industry_code / index_code 两列都尝试。
    """
    out = {}
    try:
        if cls is None or not len(cls):
            return out
        name_col = "industry_name"
        has_level = "level" in cls.columns

        def _find_parent(parent):
            for col in ("industry_code", "index_code"):
                if col in cls.columns:
                    pr = cls[cls[col].astype(str) == str(parent)]
                    if len(pr):
                        return pr.iloc[0]
            return None

        if basic_industry and name_col in cls.columns:
            cand = cls[cls[name_col].astype(str) == str(basic_industry)]
            if len(cand):
                row = cand.iloc[0]
                ic = str(row.get("index_code"))
                if has_level and str(row.get("level")) == "L1":
                    return {"index_code": ic, "l1_name": str(row[name_col]), "match": "stock_basic", "level": "L1"}
                parent = row.get("parent_code")
                seen = set()
                while parent is not None and str(parent) not in ("0", "nan", "None", "") and str(parent) not in seen:
                    seen.add(str(parent))
                    prow = _find_parent(parent)
                    if prow is None:
                        break
                    if has_level and str(prow.get("level")) == "L1":
                        return {"index_code": str(prow["index_code"]), "l1_name": str(prow[name_col]),
                                "match": "stock_basic", "level": "L1"}
                    parent = prow.get("parent_code")
        if mem is not None and len(mem) and "con_code" in mem.columns:
            hit = mem[mem["con_code"] == code]
            if len(hit):
                ic = str(hit["index_code"].iloc[0])
                lv1 = cls[cls["level"].astype(str) == "L1"] if has_level else cls
                row = lv1[lv1["index_code"].astype(str) == ic] if "index_code" in lv1.columns else lv1.iloc[0:0]
                if len(row):
                    return {"index_code": ic, "l1_name": str(row[name_col].iloc[0]),
                            "match": "sw_l1_member", "level": "L1"}
                return {"index_code": ic, "l1_name": ic, "match": "sw_l1_member", "level": "?"}
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/snapshot.py", exc)
    return out


def trade_cal_last():
    p = _q("trade_cal")
    if not os.path.exists(p):
        return None
    cal = pd.read_parquet(p)
    cal = cal[cal["is_open"] == 1]
    return str(cal["cal_date"].astype(str).max())[:8]


def load_snapshot(code, as_of):
    """as_of: YYYY-MM-DD。返回 dict：facts 数据 + domains 元信息。"""
    ymd = _ymd(as_of)
    snap = {"code": code, "as_of": as_of, "domains": {}, "raw": {}}

    def put(domain, df, date_field="trade_date", status=None, reason=None):
        d = {"as_of": _latest_date(df, date_field), "rows": 0 if df is None else int(len(df)),
             "source": "curated"}
        d["status"] = status or ("ok" if d["rows"] else "missing")
        if d["rows"] == 0:
            d["missing_reason"] = reason or "no rows"
        snap["domains"][domain] = d
        snap["raw"][domain] = df
        return df

    # 股票基础信息
    basic = _read("stock_basic", code)
    snap["basic"] = basic.iloc[0].to_dict() if basic is not None and len(basic) else {}
    name = snap["basic"].get("name", code)

    # 行情
    daily = _read("daily", code)
    if daily is not None:
        daily["trade_date"] = daily["trade_date"].astype(str)
        daily = daily[daily["trade_date"] <= ymd].sort_values("trade_date")
        daily = daily.tail(600)
    put("quote", daily)

    # 估值
    db = _read("daily_basic", code)
    if db is not None:
        db["trade_date"] = db["trade_date"].astype(str)
        db = db[db["trade_date"] <= ymd].sort_values("trade_date").tail(300)
    put("valuation", db)

    # 财务
    inc = bs = cf = fi = None
    for t, key in (("income", "inc"), ("balancesheet", "bs"), ("cashflow", "cf"), ("fina_indicator", "fi")):
        df = _read(t, code)
        if df is not None:
            df["end_date"] = df["end_date"].astype(str)
            df = df[df["end_date"] <= ymd].drop_duplicates("end_date", keep="last").sort_values("end_date")
        if key == "inc":
            inc = df
        elif key == "bs":
            bs = df
        elif key == "cf":
            cf = df
        else:
            fi = df
    fin_dom = {"income": inc, "balancesheet": bs, "cashflow": cf, "fina": fi}
    rows = sum(0 if v is None else len(v) for v in fin_dom.values())
    snap["domains"]["financial"] = {"as_of": _latest_date(inc, "end_date"), "rows": int(rows),
                                    "status": "ok" if rows else "missing", "source": "curated"}
    snap["raw"]["financial"] = fin_dom

    # 资金
    mf = _read("moneyflow", code)
    if mf is not None:
        mf["trade_date"] = mf["trade_date"].astype(str)
        mf = mf[mf["trade_date"] <= ymd].tail(60)
    put("moneyflow", mf)

    # 筹码
    cyq = _read("cyq_perf", code)
    if cyq is not None:
        cyq["trade_date"] = cyq["trade_date"].astype(str)
        cyq = cyq[cyq["trade_date"] <= ymd].tail(5)
    put("chips", cyq, reason="cyq_perf 未覆盖该股票或未同步")

    # 两融
    mg = _read("margin_detail", code)
    if mg is not None:
        mg["trade_date"] = mg["trade_date"].astype(str)
        mg = mg[mg["trade_date"] <= ymd].tail(30)
    put("margin", mg, reason="非两融标的或未同步")

    # 公告
    anns = _read("anns_d", code)
    if anns is not None:
        anns["ann_date"] = anns["ann_date"].astype(str)
        anns = anns[anns["ann_date"] <= ymd]
        anns = anns.sort_values("ann_date", ascending=False).head(3000)
    put("announcement", anns, date_field="ann_date")

    # 新闻（news.db）
    news = []
    dbpath = os.path.join(config.MAIN_ROOT, "news_collector", "news.db")
    if os.path.exists(dbpath):
        try:
            con = sqlite3.connect(dbpath)
            pat = "%" + name.replace("ST", "").strip() + "%"
            rows = con.execute("SELECT ts, title FROM news WHERE title LIKE ? ORDER BY ts DESC LIMIT 200", (pat,)).fetchall()
            con.close()
            news = [{"ts": r[0], "title": r[1]} for r in rows]
        except Exception:
            news = []
    snap["news"] = news
    snap["domains"]["news"] = {"as_of": (news[0]["ts"][:10] if news else None), "rows": len(news),
                               "status": "ok" if news else "missing", "source": "news.db",
                               "missing_reason": None if news else "近 7 天无关联新闻"}
    snap["raw"]["news"] = news

    # 股东
    h = _read("top10_holders", code)
    if h is not None:
        h["end_date"] = h["end_date"].astype(str)
        latest = h["end_date"].max()
        h = h[h["end_date"] == latest]
    put("holders", h, date_field="end_date")

    hn = _read("stk_holdernumber", code)
    if hn is not None:
        hn["end_date"] = hn["end_date"].astype(str)
    snap["holder_num"] = hn.sort_values("end_date").tail(6).to_dict("records") if hn is not None else []

    # 行业（P0Q：优先 tushare 申万权威成分缓存 → L1/L2/L3；回退 P0M 名称匹配/旧表）
    snap["industry"] = {}
    try:
        from . import industry as sw_industry
        m = sw_industry.map_stock(code)
        if m:
            snap["industry"] = m
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/snapshot.py", exc)
    if not snap["industry"]:
        try:
            mem = pd.read_parquet(os.path.join(config.CURATED, "sw_l1_member.parquet"))
            cls = pd.read_parquet(os.path.join(config.CURATED, "sw_classify_l1.parquet"))
            basic_ind = None
            try:
                sb = _read("stock_basic", code, cols=["ts_code", "industry"])
                if sb is not None and len(sb):
                    basic_ind = str(sb["industry"].iloc[0])
            except Exception:  # noqa: BLE001
                basic_ind = None
            m = _map_industry(code, basic_ind, mem, cls)
            if m:
                m["basic_industry"] = basic_ind
                snap["industry"] = m
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/snapshot.py", exc)
    snap["domains"]["industry"] = {"as_of": None, "rows": 0 if not snap["industry"] else 1,
                                   "status": "ok" if snap["industry"] else "missing",
                                   "source": (snap["industry"] or {}).get("match") or "none"}

    # 研报
    rr = _read("research_report", code)
    if rr is not None:
        rr["trade_date"] = rr["trade_date"].astype(str)
        rr = rr[rr["trade_date"] <= ymd].sort_values("trade_date").tail(200)
    put("research", rr, reason="本地研报库未覆盖（需 report_rc/研报同步补齐）")

    # 日历（在线补齐，失败则缺失）
    try:
        import sys
        sys.path.insert(0, os.path.join(config.MAIN_ROOT, "quant_backtest"))
        import tushare_sync as T
        r = T.ts_call("disclosure_date", {"ts_code": code, "end_date": "20260930"})
        fields = r.get("fields") or ["ts_code", "ann_date", "end_date", "pre_date", "actual_date"]
        items = r.get("items") or []
        cal = [dict(zip(fields, it)) for it in items]
        cal = [{k: (str(v) if v is not None else None) for k, v in c.items()} for c in cal]
        snap["calendar"] = cal
        snap["domains"]["calendar"] = {"as_of": None, "rows": len(cal), "status": "ok" if cal else "missing",
                                       "source": "disclosure_date", "missing_reason": None if cal else "无披露日"}
    except Exception as e:
        snap["calendar"] = []
        snap["domains"]["calendar"] = {"as_of": None, "rows": 0, "status": "missing",
                                       "source": "disclosure_date", "missing_reason": str(e)[:80]}

    # 覆盖率
    req = [k for k, v in config.DOMAINS.items() if v["required"]]
    cov = sum(1 for k in config.DOMAINS if snap["domains"].get(k, {}).get("rows", 0) > 0) / len(config.DOMAINS) * 100
    snap["coverage"] = round(cov, 1)
    snap["required_missing"] = [k for k in req if snap["domains"].get(k, {}).get("status") == "missing"]
    snap["name"] = name
    snap["last_trade_date"] = _latest_date(daily)

    # P0M：数据时效（落后几个交易日）——供 G1 告警与评级置信度下调
    snap["data_lag"] = {}
    try:
        from datetime import date
        last8 = _ymd(snap["last_trade_date"])
        today8 = _ymd(date.today().isoformat())
        cal = pd.read_parquet(_q("trade_cal"))
        open_days = cal[cal["is_open"] == 1]["cal_date"].astype(str)
        trade_gap = int(((open_days > last8) & (open_days < today8)).sum())
        cd = (date.today() - date.fromisoformat(str(snap["last_trade_date"]).replace("/", "-"))).days
        snap["data_lag"] = {"last_trade_date": snap["last_trade_date"], "today": today8,
                            "calendar_days": cd, "trade_days": trade_gap}
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/snapshot.py", exc)

    # 数据域时效修正：老数据（如非两融标的的 margin）标为 missing/N/A，而不是"滞后"误报
    def _age_days(d8):
        try:
            from datetime import date
            a = date.fromisoformat(str(d8).replace("/", "-")[:10])
            b = date.fromisoformat(str(snap["last_trade_date"]).replace("/", "-"))
            return (b - a).days
        except Exception:
            return None

    for dom, max_age in (("margin", 30), ("chips", 15)):
        d = snap["domains"].get(dom) or {}
        if d.get("rows") and d.get("as_of"):
            age = _age_days(d["as_of"])
            if age is not None and age > max_age:
                d["status"] = "missing"
                d["missing_reason"] = "非两融标的或长期未更新（最后 %s，%d 天前）" % (d["as_of"], age)
    return snap


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "600337.SH"
    s = load_snapshot(code, "2026-10-23")
    print(json.dumps({k: v for k, v in s.items() if k != "raw"}, ensure_ascii=False, indent=1, default=str)[:4000])
