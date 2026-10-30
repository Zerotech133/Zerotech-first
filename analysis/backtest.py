# -*- coding: utf-8 -*-
"""P0O 评级回测：月频五维打分 v2 → 评级 → 20/60 日超额收益/胜率/IC。

样本：最近市值前 N（默认 300）且 2018-06 前已有行情的 A 股（剔除 BJ），
      事件维度无历史数据 → 固定中性 50；财报用 ann_date 截止（防未来函数）。
输出：JSON + Markdown；并按总分分位给出 SCORE_CUTS 校准建议。
用法：
  python -m analysis.backtest --universe 300 --out C:\\项目\\deliverables\\2026-10-30_评级回测
"""
import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import config, scoring

CUR = config.CURATED
START, END = "2019-01-01", "2026-08-31"


def _read(name, cols, filters=None):
    p = os.path.join(CUR, name + ".parquet")
    t = pq.read_table(p, columns=cols, filters=filters or None)
    return t.to_pandas()


def build_universe(n=300):
    db = _read("daily_basic", ["ts_code", "trade_date", "total_mv"])
    db["trade_date"] = db["trade_date"].astype(str)
    last = db["trade_date"].max()
    snap = db[db["trade_date"] == last].drop_duplicates("ts_code")
    snap = snap[~snap["ts_code"].str.endswith(".BJ")]
    snap = snap.sort_values("total_mv", ascending=False).head(int(n * 1.6))
    return snap["ts_code"].tolist(), last


def load_prices(codes):
    filters = [("ts_code", "in", codes)]
    d = _read("daily", ["ts_code", "trade_date", "close"], filters=filters)
    d["trade_date"] = d["trade_date"].astype(str)
    d = d[d["trade_date"] >= "20180601"].sort_values(["ts_code", "trade_date"])
    # 只保留 2018-12 前已有足够历史的票（回测起点可用）
    early = d[d["trade_date"] <= "20181231"].groupby("ts_code").size()
    codes = [c for c in codes if early.get(c, 0) >= 120]
    d = d[d["ts_code"].isin(codes)].copy()
    g = d.groupby("ts_code")["close"]
    d["ma20"] = g.transform(lambda s: s.rolling(20, min_periods=20).mean())
    d["ma60"] = g.transform(lambda s: s.rolling(60, min_periods=60).mean())
    d["ma250"] = g.transform(lambda s: s.rolling(250, min_periods=120).mean())
    d["ret20"] = g.transform(lambda s: s / s.shift(20) - 1.0) * 100
    d["rank250"] = g.transform(lambda s: s.rolling(250, min_periods=120).apply(
        lambda x: float(pd.Series(x).rank(pct=True).iloc[-1]) * 100, raw=False))
    return d, codes


def month_ends(d):
    dates = sorted(d["trade_date"].unique())
    df = pd.DataFrame({"d": dates})
    df["ym"] = df["d"].str[:6]
    me = df.groupby("ym")["d"].max().reset_index()
    me = me[(me["d"] >= START.replace("-", "")) & (me["d"] <= END.replace("-", ""))]
    return me["d"].tolist()


def load_valuation(codes, ends):
    filters = [("ts_code", "in", codes)]
    db = _read("daily_basic", ["ts_code", "trade_date", "pe_ttm", "pb", "ps_ttm", "total_mv"], filters=filters)
    db["trade_date"] = db["trade_date"].astype(str)
    db = db.drop_duplicates(["ts_code", "trade_date"], keep="last")
    piv = db.pivot(index="trade_date", columns="ts_code", values="ps_ttm").sort_index()
    # 自身历史分位（近 5 年 ≈1250 交易日，最少 120 个样本）
    hist_pct = piv.rolling(1250, min_periods=120).rank(pct=True) * 100
    return db, hist_pct


def load_fina(codes):
    filters = [("ts_code", "in", codes)]
    fi = _read("fina_indicator", ["ts_code", "ann_date", "end_date", "roe", "grossprofit_margin",
                                  "debt_to_assets", "profit_dedt", "extra_item", "tr_yoy",
                                  "netprofit_yoy", "dt_netprofit_yoy"], filters=filters)
    fi["ann_date"] = fi["ann_date"].astype(str)
    fi = fi.dropna(subset=["ann_date"]).sort_values(["ts_code", "ann_date"])
    fi = fi[fi["ann_date"] <= "20261231"]
    return fi


def fina_at(fi, date):
    sub = fi[fi["ann_date"] <= date]
    if not len(sub):
        return {}
    sub = sub.drop_duplicates("ts_code", keep="last")
    return sub.set_index("ts_code").to_dict("index")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=300)
    ap.add_argument("--out", default=r"C:\项目\deliverables\2026-10-30_评级回测")
    ap.add_argument("--no-variants", action="store_true", help="只跑基线，不跑 P0P 实验网格")
    a = ap.parse_args()
    t0 = time.time()

    cand, last_db = build_universe(a.universe)
    print("[data] daily_basic last =", last_db, "candidates =", len(cand), flush=True)
    d, codes = load_prices(cand)
    print("[data] universe after history filter =", len(codes), "bars =", len(d), flush=True)
    ends = month_ends(d)
    print("[data] month ends =", len(ends), ends[0], "→", ends[-1], flush=True)

    db, hist_pct = load_valuation(codes, ends)
    fi = load_fina(codes)

    # 月末截面
    me = d[d["trade_date"].isin(ends)].copy()
    close_piv = d.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    bench = _read("index_daily", ["ts_code", "trade_date", "close"],
                  filters=[("ts_code", "=", "000300.SH")])
    bench["trade_date"] = bench["trade_date"].astype(str)
    bench = bench.set_index("trade_date")["close"].sort_index()

    rows = []
    for dt in ends:
        cross = me[me["trade_date"] == dt].set_index("ts_code")
        if not len(cross):
            continue
        ffin = fina_at(fi, dt)
        dbd = db[db["trade_date"] == dt].drop_duplicates("ts_code").set_index("ts_code")
        hp = hist_pct.loc[dt] if dt in hist_pct.index else pd.Series(dtype=float)
        ps_cross = dbd["ps_ttm"].dropna()
        # 基准前后收益（20/60 交易日）
        b20 = b60 = None
        if dt in bench.index:
            i = list(close_piv.index).index(dt)
            dates = list(close_piv.index)
            if i + 20 < len(dates):
                b20 = bench.get(dates[i + 20]) / bench[dt] - 1 if bench.get(dates[i + 20]) else None
            if i + 60 < len(dates):
                b60 = bench.get(dates[i + 60]) / bench[dt] - 1 if bench.get(dates[i + 60]) else None
        i = list(close_piv.index).index(dt)
        dates = list(close_piv.index)
        for code, r in cross.iterrows():
            fin = ffin.get(code) or {}
            np_dedt = fin.get("profit_dedt")
            extra = fin.get("extra_item")
            np_total = (np_dedt + extra) if (np_dedt is not None and extra is not None) else None
            ratio = (extra / abs(np_total)) if (extra is not None and np_total not in (None, 0)) else None
            fnd = {
                "roe": fin.get("roe"), "gross_margin": fin.get("grossprofit_margin"),
                "debt_ratio": fin.get("debt_to_assets"),
                "revenue_yoy": fin.get("tr_yoy"),
                "net_profit_yoy": fin.get("dt_netprofit_yoy") if (ratio or 0) >= 0.5 else fin.get("netprofit_yoy"),
                "net_profit_latest_yi": (np_total / 1e8) if np_total else None,
                "nonrecurring": ({"ratio": ratio, "dt_yoy": fin.get("dt_netprofit_yoy"),
                                  "extra_item_yi": extra / 1e8, "profit_dedt_yi": np_dedt / 1e8}
                                 if (ratio is not None and ratio >= 0.5) else {}),
            }
            fnd = {k: v for k, v in fnd.items() if v is not None and not (isinstance(v, float) and np.isnan(v))}
            if "nonrecurring" in fnd and not fnd["nonrecurring"]:
                fnd.pop("nonrecurring")
            ps = dbd["ps_ttm"].get(code)
            val = {
                "ps_ttm": None if (ps is None or pd.isna(ps)) else float(ps),
                "peer_percentile": None,
                "ps_pct_hist": None if (code not in hp.index or pd.isna(hp.get(code))) else float(hp.get(code)),
            }
            if len(ps_cross) >= 20 and val["ps_ttm"]:
                val["peer_percentile"] = round(float((ps_cross <= ps).mean()) * 100, 1)
            tech = {
                "price": float(r["close"]), "ma20": _v(r["ma20"]), "ma60": _v(r["ma60"]),
                "pct_rank_250d": _v(r["rank250"]), "ret_20d": _v(r["ret20"]),
            }
            feat = {"technical": tech, "valuation": val, "fundamental": fnd,
                    "moneyflow": {}, "chips": {}, "extras": {}}
            sv = scoring.score_v2({}, feat)
            f20 = f60 = None
            if i + 20 < len(dates) and code in close_piv.columns:
                c1 = close_piv[code].get(dates[i + 20])
                c0 = close_piv[code].get(dt)
                if pd.notna(c1) and pd.notna(c0):
                    f20 = c1 / c0 - 1
            if i + 60 < len(dates) and code in close_piv.columns:
                c1 = close_piv[code].get(dates[i + 60])
                c0 = close_piv[code].get(dt)
                if pd.notna(c1) and pd.notna(c0):
                    f60 = c1 / c0 - 1
            rows.append({"date": dt, "code": code, "total": sv["total"],
                         "rating": sv["rating"], "quality": sv["factors"]["quality"],
                         "growth": sv["factors"]["growth"], "valuation": sv["factors"]["valuation"],
                         "trend": sv["factors"]["trend"],
                         "fwd20": f20, "fwd60": f60, "b20": b20, "b60": b60})
    df = pd.DataFrame(rows)
    df["ex20"] = df["fwd20"] - df["b20"]
    df["ex60"] = df["fwd60"] - df["b60"]
    print("[score] evaluated rows =", len(df), "elapsed=%.0fs" % (time.time() - t0), flush=True)
    res = analyze(df)
    outp = write_out(res, df, a.out)
    if not a.no_variants:
        vres = evaluate_variants(df)
        vout = write_variants(vres, a.out)
        outp["variants"] = vout
        print("[variants] verdict:", json.dumps(vres["verdict"], ensure_ascii=False), flush=True)
    print(json.dumps({"out": outp, "summary": res["summary"]}, ensure_ascii=False, indent=1)[:2000], flush=True)


VARIANTS = {
    "base": {"quality": .30, "growth": .20, "valuation": .20, "trend": .15, "event": .15},
    "val_heavy": {"quality": .15, "growth": .15, "valuation": .45, "trend": .10, "event": .15},
    "val_only": {"valuation": 1.0},
    "equal": {"quality": .2, "growth": .2, "valuation": .2, "trend": .2, "event": .2},
    "qg_only": {"quality": .5, "growth": .5},
    "trend_rev": {"quality": .30, "growth": .20, "valuation": .20, "trend": .15,
                  "event": .15, "_trend_rev": True},
}


def _variant_total(df, w):
    f = df.copy()
    if w.get("_trend_rev"):
        f["trend"] = 100.0 - pd.to_numeric(f["trend"], errors="coerce").fillna(50.0)
    tot = 0.0
    wsum = 0.0
    for k, wk in w.items():
        if k.startswith("_") or k not in f.columns:
            continue
        tot = tot + pd.to_numeric(f[k], errors="coerce").fillna(50.0) * wk
        wsum += wk
    return tot / wsum if wsum else pd.Series([50.0] * len(df), index=df.index)


def _period_stats(df):
    valid = df.dropna(subset=["ex20"])
    ics, spreads = [], []
    for dt, sub in valid.groupby("date"):
        if len(sub) < 30:
            continue
        ic = sub[["total", "ex20"]].corr(method="spearman").iloc[0, 1]
        if pd.notna(ic):
            ics.append(float(ic))
        q = sub["total"].quantile([0.9, 0.1])
        top = sub[sub["total"] >= q.iloc[0]]["ex20"].mean()
        bot = sub[sub["total"] <= q.iloc[1]]["ex20"].mean()
        if pd.notna(top) and pd.notna(bot):
            spreads.append(float(top - bot))
    return {"n": int(len(valid)),
            "ic": round(float(np.mean(ics)), 4) if ics else None,
            "ic_win": round(float(np.mean([1 if x > 0 else 0 for x in ics])) * 100, 1) if ics else None,
            "top_bottom": round(float(np.mean(spreads)) * 100, 2) if spreads else None}


def evaluate_variants(df):
    df = df.copy()
    df["period"] = np.where(df["date"] < "20240101", "train", "val")
    out = {}
    for name, w in VARIANTS.items():
        d = df.copy()
        d["total"] = _variant_total(d, w)
        row = {}
        for p in ("train", "val"):
            sub = d[d["period"] == p].copy()
            st = _period_stats(sub)
            sub2 = sub.dropna(subset=["ex20"])
            sub2["rating"] = sub2["total"].map(
                lambda t: "买入" if t >= config.SCORE_CUTS["buy"]
                else ("观察偏多" if t >= config.SCORE_CUTS["observe_up"]
                      else ("观察" if t >= config.SCORE_CUTS["avoid"] else "回避")))
            g = sub2.groupby("rating")["ex20"].agg(["count", "mean"])
            st["by_rating"] = {k: {"n": int(v["count"]), "ex20": round(float(v["mean"]) * 100, 2)}
                               for k, v in g.iterrows()}
            row[p] = st
        dq = d[d["date"].str[4:6].isin(["03", "06", "09", "12"])].dropna(subset=["ex60"])
        ics = []
        for dt, sub in dq.groupby("date"):
            if len(sub) < 30:
                continue
            ic = sub[["total", "ex60"]].corr(method="spearman").iloc[0, 1]
            if pd.notna(ic):
                ics.append(float(ic))
        row["quarterly_ic60"] = round(float(np.mean(ics)), 4) if ics else None
        out[name] = row
    best, best_ic = None, -9
    for name, r in out.items():
        v = r.get("val") or {}
        if v.get("ic") is not None and v["ic"] > best_ic:
            best_ic, best = v["ic"], name
    val = (out.get(best) or {}).get("val") or {}
    by = val.get("by_rating") or {}
    buy = max([by.get(k, {}).get("ex20") for k in ("买入", "观察偏多") if by.get(k, {}).get("ex20") is not None]
              or [-9])
    passed = bool((val.get("ic") or 0) > 0.02 and (val.get("top_bottom") or 0) > 0 and buy > 0)
    return {"variants": out, "best": best,
            "verdict": {"best_variant": best, "val_ic": val.get("ic"),
                        "val_top_bottom": val.get("top_bottom"), "val_buy_ex20": buy,
                        "criterion": "val IC>0.02 且 top_bottom>0 且 买入/观察偏多 val 超额>0",
                        "passed": passed},
            "note": "事件维度无历史→固定中性50；样本=当前市值前N；不含成本；季频=3/6/9/12月末60日收益"}


def write_variants(vres, outdir):
    os.makedirs(outdir, exist_ok=True)
    day = time.strftime("%Y%m%d")
    jp = os.path.join(outdir, "variants_%s.json" % day)
    json.dump(vres, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    md = ["# P0P 策略实验（权重/因子/频率）· %s" % time.strftime("%F %T"), "",
          "> " + vres["note"], "",
          "## 实验对比（训练 2019-2023 / 验证 2024-2026）", "",
          "| 变体 | 训练IC | 验证IC | 验证IC胜率% | 验证Top-Bottom% | 季频IC(60d) |",
          "|---|---|---|---|---|---|"]
    for name, r in vres["variants"].items():
        tr, va = r.get("train") or {}, r.get("val") or {}
        md.append("| %s | %s | %s | %s | %s | %s |" % (
            name, tr.get("ic"), va.get("ic"), va.get("ic_win"),
            va.get("top_bottom"), r.get("quarterly_ic60")))
    v = vres["verdict"]
    md += ["", "## 判定", "", "```", json.dumps(v, ensure_ascii=False), "```"]
    md += ["", "## 各变体验证段评级组超额（20日）", ""]
    for name, r in vres["variants"].items():
        by = ((r.get("val") or {}).get("by_rating")) or {}
        md.append("- **%s**: %s" % (name, ", ".join("%s n=%s ex20=%s%%" % (k, x["n"], x["ex20"])
                                                    for k, x in by.items())))
    mp = os.path.join(outdir, "variants_%s.md" % day)
    open(mp, "w", encoding="utf-8").write("\n".join(md))
    return {"json": jp, "md": mp}


def _v(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def analyze(df):
    valid = df.dropna(subset=["ex20"])
    # P0N 固定分档
    g = valid.groupby("rating")
    by_rating = {}
    for k, sub in g:
        by_rating[k] = {"n": int(len(sub)),
                        "ex20": round(float(sub["ex20"].mean()) * 100, 2) if len(sub) else None,
                        "win20": round(float((sub["ex20"] > 0).mean()) * 100, 1) if len(sub) else None,
                        "ex60": round(float(sub.dropna(subset=["ex60"])["ex60"].mean()) * 100, 2) if sub["ex60"].notna().any() else None,
                        "win60": round(float((sub["ex60"].dropna() > 0).mean()) * 100, 1) if sub["ex60"].notna().any() else None}
    # 分位映射（当月五分位）
    parts = []
    ics = []
    for dt, sub in valid.groupby("date"):
        if len(sub) < 30:
            continue
        ranks = sub["total"].rank(pct=True)
        q = pd.cut(ranks, [0, 0.05, 0.25, 0.75, 0.95, 1.0],
                   labels=["回避", "观察", "观察偏多", "买入", "买入+"], include_lowest=True)
        parts.append(pd.DataFrame({"q": q.astype(str), "ex20": sub["ex20"].values}))
        ic = sub[["total", "ex20"]].corr(method="spearman").iloc[0, 1]
        if pd.notna(ic):
            ics.append(float(ic))
    by_quantile = {}
    if parts:
        qdf = pd.concat(parts)
        for k, sub in qdf.groupby("q"):
            by_quantile[k] = {"n": int(len(sub)), "ex20": round(float(sub["ex20"].mean()) * 100, 2),
                              "win20": round(float((sub["ex20"] > 0).mean()) * 100, 1)}
    # 分因子 IC（看是哪一维在起作用）
    factor_ic = {}
    for col in ("quality", "growth", "valuation", "trend", "total"):
        ics_f = []
        for dt, sub in valid.groupby("date"):
            if len(sub) < 30:
                continue
            ic_f = sub[[col, "ex20"]].corr(method="spearman").iloc[0, 1]
            if pd.notna(ic_f):
                ics_f.append(float(ic_f))
        factor_ic[col] = round(float(np.mean(ics_f)), 4) if ics_f else None
    q = df["total"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).round(1).to_dict()
    summary = {"rows": int(len(df)), "dates": int(df["date"].nunique()),
               "ic_mean": round(float(np.mean(ics)), 4) if ics else None,
               "ic_std": round(float(np.std(ics)), 4) if ics else None,
               "ic_win": round(float(np.mean([1 if x > 0 else 0 for x in ics])) * 100, 1) if ics else None,
               "factor_ic": factor_ic,
               "score_quantiles": {str(k): v for k, v in q.items()},
               "suggested_cuts": {"buy": q.get(0.95), "observe_up": q.get(0.75),
                                  "observe": q.get(0.25), "avoid": q.get(0.05)},
               "score_range": [round(float(df["total"].min()), 1), round(float(df["total"].max()), 1)]}
    return {"summary": summary, "by_rating": by_rating, "by_quantile": by_quantile}


def write_out(res, df, outdir):
    os.makedirs(outdir, exist_ok=True)
    day = time.strftime("%Y%m%d")
    jp = os.path.join(outdir, "rating_backtest_%s.json" % day)
    json.dump(res, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    md = ["# 评级回测（P0O）· %s" % time.strftime("%F %T"), "",
          "> 月频、五维打分 v2（事件维度无历史→中性 50）、财报按 ann_date 截止；样本=市值前 %d 且 2018 前有行情；不含手续费。"
          % 300, "",
          "## 汇总", "", "| 项 | 值 |", "|---|---|"]
    for k, v in res["summary"].items():
        md.append("| %s | %s |" % (k, v))
    md += ["", "## 按固定分档（config.SCORE_CUTS）", "", "| 评级 | n | 20日超额% | 20日胜率% | 60日超额% | 60日胜率% |", "|---|---|---|---|---|---|"]
    for k in ("买入", "观察偏多", "观察", "事件观察", "回避"):
        if k in res["by_rating"]:
            b = res["by_rating"][k]
            md.append("| %s | %s | %s | %s | %s | %s |" % (k, b["n"], b["ex20"], b["win20"], b["ex60"], b["win60"]))
    md += ["", "## 按当月分位映射", "", "| 分位组 | n | 20日超额% | 20日胜率% |", "|---|---|---|---|"]
    for k, b in sorted(res["by_quantile"].items()):
        md.append("| %s | %s | %s | %s |" % (k, b["n"], b["ex20"], b["win20"]))
    md += ["", "## 建议分档（按总分分位校准）", "", "```", json.dumps(res["summary"]["suggested_cuts"], ensure_ascii=False), "```"]
    mp = os.path.join(outdir, "rating_backtest_%s.md" % day)
    open(mp, "w", encoding="utf-8").write("\n".join(md))
    return {"json": jp, "md": mp}


if __name__ == "__main__":
    main()
