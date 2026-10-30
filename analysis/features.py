# -*- coding: utf-8 -*-
"""S3 量化特征：技术 / 估值（含同业）/ 财务 / 资金 / 筹码 / 结构。"""
import json
import os
import urllib.request

import pandas as pd

from . import config
import logging


def _f(v, nd=2):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return round(float(v), nd)
    except Exception:
        return None


def _pct_rank(series, v):
    try:
        s = pd.Series(series).dropna()
        if not len(s) or v is None:
            return None
        return round(float((s < v).mean()) * 100, 1)
    except Exception:
        return None


def _yy(ts_code, end_date_field="end_date"):
    return None


def _api_json(url, timeout=6):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _ttm(inc, latest_end, annual_end, same_end):
    def rv(df, end):
        if df is None or not len(df):
            return None
        row = df[df["end_date"] == end]
        return float(row["revenue"].iloc[0]) if len(row) else None
    a, b, c = rv(inc, latest_end), rv(inc, annual_end), rv(inc, same_end)
    if None in (a, b, c):
        return None
    return a + b - c


def build_features(snap):
    raw = snap["raw"]
    daily = raw.get("quote")
    db = raw.get("valuation")
    fin = raw.get("financial") or {}
    inc, bs, cf, fi = fin.get("income"), fin.get("balancesheet"), fin.get("cashflow"), fin.get("fina")
    out = {"technical": {}, "valuation": {}, "fundamental": {}, "moneyflow": {}, "chips": {}, "peers": []}

    # ---- 技术面 ----
    tech = out["technical"]
    if daily is not None and len(daily):
        c = daily["close"].astype(float)
        close = float(c.iloc[-1])
        tech["price"] = round(close, 2)
        tech["pct_1d"] = _f(daily["pct_chg"].iloc[-1])
        for n, key in ((5, "ret_5d"), (20, "ret_20d"), (60, "ret_60d"), (120, "ret_120d"), (250, "ret_250d")):
            if len(c) > n:
                tech[key] = round((close / float(c.iloc[-1 - n]) - 1) * 100, 2)
        tech["pct_rank_250d"] = _pct_rank(c.tail(250), close)
        tech["high_250d"] = _f(daily["high"].tail(250).max())
        tech["low_250d"] = _f(daily["low"].tail(250).min())
        if len(c) >= 20:
            tech["ma20"] = _f(c.tail(20).mean())
        if len(c) >= 60:
            tech["ma60"] = _f(c.tail(60).mean())
        if len(c) >= 250:
            tech["ma250"] = _f(c.tail(250).mean())
        tech["vol_20d_wan"] = _f(daily["vol"].tail(20).mean() / 1e4, 1)
        tech["turnover_20d"] = _f(db["turnover_rate_f"].tail(20).mean(), 2) if db is not None and "turnover_rate_f" in db.columns else None
        # 关键位（结构近似）
        hi60 = float(daily["high"].tail(60).max())
        lo60 = float(daily["low"].tail(60).min())
        levels = [{"type": "pressure", "price": round(hi60, 2), "label": "60日高"},
                  {"type": "support", "price": round(lo60, 2), "label": "60日低"}]
        if tech.get("ma20"):
            levels.append({"type": "support", "price": tech["ma20"], "label": "MA20"})
        if tech.get("ma60"):
            levels.append({"type": "support", "price": tech["ma60"], "label": "MA60"})
        if tech.get("high_250d"):
            levels.append({"type": "pressure", "price": tech["high_250d"], "label": "250日高"})
        tech["levels"] = levels

    # 缠论/联动（门户 8091，可选）
    tech["chanlun"] = _api_json("http://127.0.0.1:8091/api/chanlun/structure?code=%s&period=1d&limit=250" % snap["code"], timeout=8)
    tech["linkage"] = _api_json("http://127.0.0.1:8091/bt-linkage?code=%s" % snap["code"], timeout=12)

    # ---- 估值 ----
    val = out["valuation"]
    if db is not None and len(db):
        row = db.iloc[-1]
        val["market_cap_yi"] = _f(float(row.get("total_mv", 0)) / 1e4)
        for k, src in (("pe_ttm", "pe_ttm"), ("pb", "pb"), ("ps_ttm", "ps_ttm")):
            val[k] = _f(row.get(src))
        if db["trade_date"].max() < snap["last_trade_date"]:
            val["stale_days_note"] = "daily_basic 落后于行情"
    # 同业（申万一级成分股：sw_l1_member.con_code + sw_classify_l1.industry_name）
    ind = snap.get("industry") or {}
    l1_name = ind.get("l1_name")
    val["industry"] = l1_name
    peer_codes = []
    if l1_name:
        try:
            cls = pd.read_parquet(os.path.join(config.CURATED, "sw_classify_l1.parquet"))
            mem = pd.read_parquet(os.path.join(config.CURATED, "sw_l1_member.parquet"))
            lv1 = cls[cls["level"].astype(str) == "L1"] if "level" in cls.columns else cls
            idx = lv1[lv1["industry_name"] == l1_name]["index_code"].tolist()
            if idx:
                mem = mem[mem["index_code"].isin(idx)]
                mem = mem[mem.get("is_new", "Y") == "Y"] if "is_new" in mem.columns else mem
                peer_codes = [c for c in mem["con_code"].tolist() if c != snap["code"]][:60]
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/features.py", exc)
    if peer_codes:
        try:
            dball = pd.read_parquet(os.path.join(config.CURATED, "daily_basic.parquet"),
                                    columns=["ts_code", "trade_date", "pb", "ps_ttm", "total_mv"])
            latest = dball["trade_date"].astype(str).max()
            cur = dball[dball["trade_date"].astype(str) == latest]
            cur = cur[cur["ts_code"].isin(peer_codes)].copy()
            cur["mv"] = cur["total_mv"] / 1e4
            peers = []
            for _, r in cur.iterrows():
                if pd.notna(r.get("ps_ttm")):
                    peers.append({"code": r["ts_code"], "ps_ttm": _f(r["ps_ttm"]),
                                  "pb": _f(r.get("pb")), "market_cap_yi": _f(r["mv"])})
            peers.sort(key=lambda x: (x["ps_ttm"] is None, x["ps_ttm"]))
            # 可比性过滤：市值在自身 0.2x~5x 区间内（至少保留 3 家）
            own_mv = val.get("market_cap_yi")
            if own_mv:
                filt = [p for p in peers if p.get("market_cap_yi") and 0.2 * own_mv <= p["market_cap_yi"] <= 5 * own_mv]
                if len(filt) >= 3:
                    peers = filt
            out["peers"] = peers[:10]
            if peers and val.get("ps_ttm"):
                ps_list = [p["ps_ttm"] for p in peers if p.get("ps_ttm")]
                val["peer_ps_median"] = round(float(pd.Series(ps_list).median()), 2)
                val["peer_percentile"] = _pct_rank(ps_list + [val["ps_ttm"]], val["ps_ttm"])
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/features.py", exc)

    # ---- 财务 ----
    fnd = out["fundamental"]
    if inc is not None and len(inc):
        latest = inc.iloc[-1]
        fnd["latest_end"] = latest["end_date"]
        fnd["revenue_latest_yi"] = _f(float(latest["revenue"]) / 1e8)
        fnd["net_profit_latest_yi"] = _f(float(latest.get("n_income_attr_p", 0)) / 1e8)
        if len(inc) >= 2:
            prev = inc.iloc[-2]
            fnd["revenue_prev_yi"] = _f(float(prev["revenue"]) / 1e8)
        # 同比：优先用同一报告期（上年同期），自算，避免 fina 字段口径差异
        try:
            same_row = inc[inc["end_date"] == str(int(latest["end_date"][:4]) - 1) + latest["end_date"][4:]]
            if len(same_row):
                sr = same_row.iloc[0]
                prev_rev = float(sr["revenue"])
                prev_np = float(sr.get("n_income_attr_p", 0))
                if prev_rev:
                    fnd["revenue_yoy"] = round((float(latest["revenue"]) / prev_rev - 1) * 100, 2)
                if prev_np:
                    fnd["net_profit_yoy"] = round((float(latest.get("n_income_attr_p", 0)) - prev_np) / abs(prev_np) * 100, 2)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/features.py", exc)
        # TTM（最近年报 + 最新中报 - 上年中报）
        annual = inc[inc["end_date"].str.endswith("1231")]
        ttm = None
        if len(annual) and len(inc):
            a_end = annual["end_date"].iloc[-1]
            same = str(int(latest["end_date"][:4]) - 1) + latest["end_date"][4:]
            ttm = _ttm(inc, latest["end_date"], a_end, same)
        fnd["revenue_ttm_yi"] = _f(ttm / 1e8) if ttm else None
        if fi is not None and len(fi):
            r = fi.iloc[-1]
            for k, src in (("gross_margin", "grossprofit_margin"), ("debt_ratio", "debt_to_assets"),
                           ("roe", "roe")):
                fnd[k] = _f(r.get(src))
            for k, src in (("revenue_yoy", "or_yoy"), ("net_profit_yoy", "netprofit_yoy")):
                if k not in fnd or fnd.get(k) is None:
                    fnd[k] = _f(r.get(src))
        # P0L：非经常性损益识别（扣非口径；一次性收益不得抬高评级）
        try:
            nr = {}
            if fi is not None and len(fi):
                fr = fi.iloc[-1]
                # 仅当 fina 最新期与 income 最新期一致才采信
                if str(fr.get("end_date")) == str(fnd.get("latest_end")):
                    for k, src in (("profit_dedt_yi", "profit_dedt"), ("extra_item_yi", "extra_item")):
                        v = fr.get(src)
                        if v is not None and pd.notna(v):
                            nr[k] = round(float(v) / 1e8, 2)
                    v = fr.get("dt_netprofit_yoy")
                    if v is not None and pd.notna(v):
                        nr["dt_yoy"] = _f(v)
            np_val = fnd.get("net_profit_latest_yi")
            if nr.get("extra_item_yi") is not None and np_val:
                nr["ratio"] = round(nr["extra_item_yi"] / max(abs(float(np_val)), 1e-9), 2)
            if nr.get("profit_dedt_yi") is not None and np_val:
                nr["dedt_ratio"] = round(nr["profit_dedt_yi"] / max(abs(float(np_val)), 1e-9), 2)
            if nr:
                fnd["nonrecurring"] = nr
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/features.py", exc)
    if bs is not None and len(bs):
        r = bs.iloc[-1]
        fnd["net_assets_yi"] = _f(float(r.get("total_hldr_eqy_exc_min_int", 0)) / 1e8)
        fnd["contract_liab_yi"] = _f(float(r.get("contract_liab", 0)) / 1e8) if "contract_liab" in r else None
        fnd["goodwill_yi"] = _f(float(r.get("goodwill", 0)) / 1e8) if "goodwill" in r else None
    if cf is not None and len(cf) and "n_cashflow_act" in cf.columns:
        # 最近完整年度 OCF
        annual = cf[cf["end_date"].str.endswith("1231")]
        if len(annual):
            fnd["ocf_annual_yi"] = _f(float(annual["n_cashflow_act"].iloc[-1]) / 1e8)
        fnd["ocf_latest_yi"] = _f(float(cf["n_cashflow_act"].iloc[-1]) / 1e8)

    # 特殊状态标签
    tags = []
    name = snap.get("name") or ""
    if "ST" in name:
        tags.append("ST")
    anns = raw.get("announcement")
    if anns is not None and len(anns):
        titles = " ".join(anns["title"].astype(str).head(200).tolist())
        if "退市风险" in titles or "终止上市" in titles:
            tags.append("退市风险")
        if "重整" in titles or "预重整" in titles:
            tags.append("重整")
    fnd["special_status"] = tags

    # ---- 资金 ----
    mf = raw.get("moneyflow")
    if mf is not None and len(mf):
        net = (mf["buy_lg_amount"].fillna(0) + mf["buy_elg_amount"].fillna(0)
               - mf["sell_lg_amount"].fillna(0) - mf["sell_elg_amount"].fillna(0))
        out["moneyflow"]["main_net_5d_wan"] = _f(net.tail(5).sum(), 1)
        out["moneyflow"]["main_net_20d_wan"] = _f(net.tail(20).sum(), 1)
        out["moneyflow"]["latest_date"] = str(mf["trade_date"].iloc[-1])
    mg = raw.get("margin")
    if mg is not None and len(mg) and "rzye" in mg.columns:
        out["moneyflow"]["margin_balance_yi"] = _f(float(mg["rzye"].iloc[-1]) / 1e8)

    # ---- 筹码 ----
    cyq = raw.get("chips")
    if cyq is not None and len(cyq):
        r = cyq.iloc[-1]
        out["chips"]["avg_cost"] = _f(r.get("cost_50pct"))
        out["chips"]["cost_15pct"] = _f(r.get("cost_15pct"))
        out["chips"]["cost_85pct"] = _f(r.get("cost_85pct"))
        out["chips"]["as_of"] = str(r.get("trade_date"))
    # 门户筹码（含获利盘）
    ch = _api_json("http://127.0.0.1:8091/api/chips?c=%s&d=%s&x=%s" % (
        snap["code"].split(".")[0], snap["last_trade_date"].replace("-", ""), tech.get("price", "")), timeout=8)
    if ch and ch.get("ok"):
        out["chips"]["winner_rate"] = ch.get("winner")
        if ch.get("avg"):
            out["chips"]["avg_cost"] = ch.get("avg")

    # ---- 事件（外部注入） ----
    out["events"] = snap.get("events", [])
    return out
