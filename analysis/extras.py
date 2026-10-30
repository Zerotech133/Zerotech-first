# -*- coding: utf-8 -*-
"""扩展数据（sync 缓存）：一致预期 / 业绩预告 / 龙虎榜 / 股东行为 / 主营构成 / 互动问答。

所有数据来自 analysis.sync 的本地缓存；缺失则返回 None，不阻断主流程。
"""
import pandas as pd

from . import sync


def _days_ago(ymd, n):
    return str(int(ymd) - n * 10000) if False else None


def _ymd(s):
    return str(s).replace("-", "")


def _cutoff(ymd, days):
    import datetime as dt
    d = dt.datetime.strptime(ymd, "%Y%m%d") - dt.timedelta(days=days)
    return d.strftime("%Y%m%d")


def build(snap):
    code = snap["code"]
    ymd = _ymd(snap["as_of"])
    out = {"consensus": None, "forecast": None, "lhb": None, "holder_actions": None,
           "mainbz": None, "interaction": None, "surveys": None, "dividend": None}

    # ---- 一致预期（report_rc）----
    rc = sync.load_code("report_rc", code)
    if rc is not None and len(rc):
        rc = rc.copy()
        rc["report_date"] = rc["report_date"].astype(str)
        rc = rc[rc["report_date"] <= ymd].sort_values("report_date")
        if len(rc):
            c180 = _cutoff(ymd, 180)
            recent = rc[rc["report_date"] >= c180]
            last = rc.iloc[-1]
            ratings = recent["rating"].dropna().astype(str).tolist() if "rating" in recent else []
            out["consensus"] = {
                "reports_180d": int(len(recent)),
                "latest_date": str(last["report_date"]),
                "latest_org": str(last.get("org_name") or ""),
                "latest_title": str(last.get("report_title") or "")[:60],
                "latest_rating": str(last.get("rating") or ""),
                "buy_ratio": (sum(1 for r in ratings if "买入" in r or "增持" in r) / len(ratings)) if ratings else None,
                "eps_last": float(last["eps"]) if pd.notna(last.get("eps")) else None,
                "np_last_yi": round(float(last["np"]) / 1e4, 2) if pd.notna(last.get("np")) else None,
                "as_of": str(last["report_date"]),
            }

    # ---- 业绩预告 / 快报 ----
    for table, kind in (("forecast", "forecast"), ("express", "express")):
        df = sync.load_code(table, code)
        if df is not None and len(df):
            df = df.copy()
            df["ann_date"] = df["ann_date"].astype(str)
            df = df[df["ann_date"] <= ymd].sort_values("ann_date")
            if len(df):
                r = df.iloc[-1]
                if kind == "forecast":
                    out["forecast"] = {"ann_date": str(r["ann_date"]), "end_date": str(r.get("end_date")),
                                       "type": str(r.get("type")), "summary": str(r.get("summary") or "")[:80],
                                       "net_min_yi": round(float(r["net_profit_min"]) / 1e4, 2) if pd.notna(r.get("net_profit_min")) else None,
                                       "net_max_yi": round(float(r["net_profit_max"]) / 1e4, 2) if pd.notna(r.get("net_profit_max")) else None}
                else:
                    out["express"] = {"ann_date": str(r["ann_date"]), "end_date": str(r.get("end_date")),
                                      "revenue_yi": round(float(r["revenue"]) / 1e8, 2) if pd.notna(r.get("revenue")) else None,
                                      "n_income_yi": round(float(r["n_income"]) / 1e8, 2) if pd.notna(r.get("n_income")) else None}

    # ---- 龙虎榜 ----
    tl, ti = sync.load_daily("top_list"), sync.load_daily("top_inst")
    if tl is not None and len(tl):
        tl = tl.copy()
        tl["trade_date"] = tl["trade_date"].astype(str)
        c90 = _cutoff(ymd, 90)
        rec = tl[(tl["ts_code"] == code) & (tl["trade_date"] >= c90)]
        if len(rec):
            out["lhb"] = {"count_90d": int(len(rec)), "last_date": str(rec["trade_date"].max())}
            if ti is not None and len(ti):
                ti = ti.copy()
                ti["trade_date"] = ti["trade_date"].astype(str)
                ir = ti[(ti["ts_code"] == code) & (ti["trade_date"] >= c90)]
                if len(ir):
                    # P0T：tushare 的 buy/sell/net_buy 单位是「元」，对外字段是「万元」，必须 /1e4
                    if "net_buy" in ir.columns:
                        net_yuan = float(pd.to_numeric(ir["net_buy"], errors="coerce").fillna(0).sum())
                    elif "buy" in ir.columns and "sell" in ir.columns:
                        net_yuan = float((pd.to_numeric(ir["buy"], errors="coerce").fillna(0)
                                          - pd.to_numeric(ir["sell"], errors="coerce").fillna(0)).sum())
                    else:
                        net_yuan = None
                    out["lhb"]["seat_net_wan"] = round(net_yuan / 1e4, 1) if net_yuan is not None else None
                    out["lhb"]["seats"] = int(len(ir))

    # ---- 股东行为 ----
    acts = {}
    ht = sync.load_code("stk_holdertrade", code)
    if ht is not None and len(ht):
        ht = ht.copy()
        ht["ann_date"] = ht["ann_date"].astype(str)
        c365 = _cutoff(ymd, 365)
        rec = ht[(ht["ann_date"] <= ymd) & (ht["ann_date"] >= c365)]
        acts["holdertrade"] = [{"ann_date": str(r["ann_date"]), "holder": str(r.get("holder_name") or "")[:24],
                                "in_de": str(r.get("in_de") or ""),
                                "ratio": round(float(r["change_ratio"]), 2) if pd.notna(r.get("change_ratio")) else None}
                               for _, r in rec.iterrows()][:6]
    ps = sync.load_code("pledge_stat", code)
    if ps is not None and len(ps):
        ps = ps.copy()
        ps["end_date"] = ps["end_date"].astype(str)
        ps = ps[ps["end_date"] <= ymd].sort_values("end_date")
        if len(ps):
            acts["pledge_ratio"] = round(float(ps.iloc[-1]["pledge_ratio"]), 2) if pd.notna(ps.iloc[-1].get("pledge_ratio")) else None
    sf = sync.load_code("share_float", code)
    if sf is not None and len(sf):
        sf = sf.copy()
        sf["float_date"] = sf["float_date"].astype(str)
        fut = sf[(sf["float_date"] >= ymd) & (sf["float_date"] <= _cutoff(ymd, -180))]
        acts["float_next_180d"] = [{"date": str(r["float_date"]), "ratio": round(float(r["float_ratio"]), 2) if pd.notna(r.get("float_ratio")) else None}
                                   for _, r in fut.iterrows()][:4]
    rp = sync.load_code("repurchase", code)
    if rp is not None and len(rp):
        rp = rp.copy()
        rp["ann_date"] = rp["ann_date"].astype(str)
        rp = rp[rp["ann_date"] <= ymd].sort_values("ann_date")
        if len(rp):
            r = rp.iloc[-1]
            acts["repurchase"] = {"ann_date": str(r["ann_date"]), "proc": str(r.get("proc") or ""),
                                  "amount_yi": round(float(r["amount"]) / 1e8, 2) if pd.notna(r.get("amount")) else None}
    bt = sync.load_code("block_trade", code)
    if bt is not None and len(bt):
        bt = bt.copy()
        bt["trade_date"] = bt["trade_date"].astype(str)
        rec = bt[(bt["trade_date"] <= ymd) & (bt["trade_date"] >= _cutoff(ymd, 90))]
        if len(rec):
            acts["block_trade_90d"] = {"count": int(len(rec)),
                                       "amount_yi": round(float(rec["amount"].fillna(0).sum()) / 1e4, 2)}
    if acts:
        out["holder_actions"] = acts

    # ---- 调研 / 互动 ----
    sv = sync.load_code("stk_surv", code)
    if sv is not None and len(sv):
        sv = sv.copy()
        sv["surv_date"] = sv["surv_date"].astype(str)
        rec = sv[(sv["surv_date"] <= ymd) & (sv["surv_date"] >= _cutoff(ymd, 365))]
        if len(rec):
            out["surveys"] = {"count_365d": int(len(rec)), "last_date": str(rec["surv_date"].max())}
    ir = sync.load_code("irm_qa_sh", code) if sync.load_code("irm_qa_sh", code) is not None else sync.load_code("irm_qa_sz", code)
    if ir is not None and len(ir):
        ir = ir.copy()
        ir["pub_time"] = ir["pub_time"].astype(str)
        rec = ir[ir["pub_time"].str[:10] <= snap["as_of"]]
        if len(rec):
            c180 = _cutoff(ymd, 180)
            c180_iso = c180[:4] + "-" + c180[4:6] + "-" + c180[6:8]
            recent = rec[rec["pub_time"].str[:10] >= c180_iso]
            out["interaction"] = {"total": int(len(rec)), "recent_180d": int(len(recent)),
                                  "latest_time": str(rec["pub_time"].max())[:19]}

    # ---- 主营构成 ----
    mb = sync.load_code("fina_mainbz", code)
    if mb is not None and len(mb):
        mb = mb.copy()
        mb["end_date"] = mb["end_date"].astype(str)
        mb = mb[mb["end_date"] <= ymd]
        if len(mb):
            latest = mb["end_date"].max()
            cur = mb[mb["end_date"] == latest].copy()
            cur["sales_yi"] = cur["bz_sales"].fillna(0) / 1e8
            cur = cur.sort_values("sales_yi", ascending=False)
            total = cur["sales_yi"].sum() or 1
            out["mainbz"] = {"end_date": latest,
                             "items": [{"item": str(r["bz_item"])[:24], "sales_yi": round(float(r["sales_yi"]), 2),
                                        "share": round(float(r["sales_yi"]) / total * 100, 1)} for _, r in cur.head(4).iterrows()]}

    # ---- 分红 ----
    dv = sync.load_code("dividend", code)
    if dv is not None and len(dv):
        dv = dv.copy()
        dv["ann_date"] = dv["ann_date"].astype(str)
        dv = dv[dv["ann_date"] <= ymd].sort_values("ann_date")
        if len(dv):
            r = dv.iloc[-1]
            out["dividend"] = {"ann_date": str(r["ann_date"]), "proc": str(r.get("div_proc") or ""),
                               "cash": float(r.get("cash_div") or 0), "ex_date": str(r.get("ex_date") or "")[:10]}
    return out
