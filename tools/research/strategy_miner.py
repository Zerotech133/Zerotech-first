# -*- coding: utf-8 -*-
"""策略自挖一轮 v2：窗口扩到 2019+（价格），修复 report_rc 特征稀疏；月频 IC + 多空分层。

边界（与 v1 同）：
  - 挖掘轮（mining）非验证：无四关/无多重检验修正/无容量细节；供挑 2–3 个进入四关；
  - daily_basic 仅 2023+ → 换手/市值/PE/目标价空间特征从 2023 起；
  - forecast <18 个月 → 按 H34 不单独检验；
  - 使用 current stock_basic 过滤 ST/上市日 → 存在 ST 历史状态与幸存者偏差，报告标注。

用法：python tools/research/strategy_miner.py [--start 20190101]
输出：research/reports/strategy_mining_<lastmonth>.{json,md}
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from pit_utils import listed_days  # noqa: E402

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine as se  # noqa: E402

RATING_MAP = {"买入": 5, "强烈推荐": 5, "强推": 5, "推荐": 4, "增持": 4, "优于大市": 4, "跑赢行业": 4,
              "谨慎推荐": 3, "谨慎买入": 3, "中性": 3, "持有": 3, "减持": 2, "弱于大市": 2, "卖出": 1, "无": np.nan}


def window_dates(start: str, buffer_days: int = 150) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    s = pd.Series(ds)
    return s[s < start].tail(buffer_days).tolist() + s[s >= start].tolist()


def build_adj_panel(dts: list[str]) -> pd.DataFrame:
    parts = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=["ts_code", "close"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        parts.append(d[["ts_code", "trade_date", "adj"]])
        if len(parts) >= 300:
            chunk = pd.concat(parts, ignore_index=True)
            parts = []
            piv = chunk.pivot_table(index="trade_date", columns="ts_code", values="adj")
            dts_acc = piv
            if "acc" not in locals():
                acc = dts_acc
            else:
                acc = pd.concat([acc, dts_acc], axis=0)
    if parts:
        chunk = pd.concat(parts, ignore_index=True)
        piv = chunk.pivot_table(index="trade_date", columns="ts_code", values="adj")
        acc = pd.concat([acc, piv], axis=0) if "acc" in locals() else piv
    acc.index = [str(x) for x in acc.index]
    return acc.sort_index()


def build_daily_basic(dts: list[str]) -> dict[str, pd.DataFrame]:
    turn, mcap, pe = {}, {}, {}
    for dt in dts:
        f = DATA / "daily_basic" / f"d_{dt}.parquet"
        if not f.exists():
            continue
        b = pq.read_table(f, columns=["ts_code", "turnover_rate", "circ_mv", "pe_ttm"]).to_pandas().set_index("ts_code")
        turn[dt], mcap[dt], pe[dt] = b["turnover_rate"], b["circ_mv"], b["pe_ttm"]

    def frame(dd):
        return pd.DataFrame(dd).T if dd else pd.DataFrame()
    return {"turn": frame(turn), "mcap": frame(mcap), "pe": frame(pe)}


def load_rc() -> pd.DataFrame:
    fs = sorted(glob.glob(str(DATA / "report_rc" / "*.parquet")))
    parts = []
    for f in fs:
        try:
            parts.append(pq.read_table(f, columns=["ts_code", "report_date", "org_name", "rating", "max_price"]).to_pandas())
        except Exception:  # noqa: BLE001
            continue
    if not parts:
        return pd.DataFrame(columns=["ts_code", "report_date", "org_name", "rating", "max_price", "rd", "score"])
    rc = pd.concat(parts, ignore_index=True)
    rc["report_date"] = rc["report_date"].astype(str)
    rc["rd"] = pd.to_datetime(rc["report_date"], format="%Y%m%d", errors="coerce")
    rc = rc.dropna(subset=["rd"])
    rc["score"] = rc["rating"].map(lambda x: RATING_MAP.get(str(x).strip(), np.nan))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20190101")
    args = ap.parse_args(argv)
    dts = window_dates(args.start)
    print("trade days:", len(dts), dts[0], "..", dts[-1], flush=True)
    adj = build_adj_panel(dts)
    print("adj panel:", adj.shape, flush=True)
    db = build_daily_basic([d for d in dts if d >= "20230101"])
    print("daily_basic:", {k: v.shape for k, v in db.items()}, flush=True)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    def listed_at(dt_):
        return listed_days(sb, dt_)
    rc = load_rc()
    print("report_rc rows:", len(rc), flush=True)

    trade_days = adj.index.tolist()
    s = pd.Series(trade_days)
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]
    mends = [m for m in mends if m >= args.start]
    print("month ends:", len(mends), mends[0], "..", mends[-1], flush=True)

    feat_names = ["mom_20", "mom_120", "rev_5", "vol_20", "turnover_20", "size_neg", "value_ep",
                  "quality_roe", "coverage_90d", "org_breadth", "rating_mean", "rating_up_ratio",
                  "target_upside", "coverage_growth", "new_coverage", "rating_change"]
    rows = []
    for i, dt in enumerate(mends[:-1]):
        nxt = mends[i + 1]
        if dt not in adj.index or nxt not in adj.index:
            continue
        hist = adj.loc[:dt]
        px = hist.iloc[-1]
        fwd = adj.loc[nxt] / px - 1.0
        uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed_at(dt).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
        if dt in db["mcap"].index:
            uni &= (db["mcap"].loc[dt] >= 3e5).reindex(px.index).fillna(False)
        uni = uni.fillna(False)
        cc = px.index[uni]
        if len(cc) < 200:
            continue
        f = {}
        f["mom_20"] = (px / hist.iloc[-21] - 1).reindex(cc) if len(hist) > 21 else np.nan
        f["mom_120"] = (px / hist.iloc[-121] - 1).reindex(cc) if len(hist) > 121 else np.nan
        f["rev_5"] = -(px / hist.iloc[-6] - 1).reindex(cc) if len(hist) > 6 else np.nan
        f["vol_20"] = hist.tail(21).pct_change().std().reindex(cc) if len(hist) > 21 else np.nan
        if dt in db["turn"].index:
            f["turnover_20"] = db["turn"].loc[:dt].tail(20).mean().reindex(cc)
            f["size_neg"] = -np.log(db["mcap"].loc[dt].reindex(cc).clip(lower=1))
            f["value_ep"] = (1.0 / db["pe"].loc[dt].replace(0, np.nan)).reindex(cc)
        else:
            for k in ("turnover_20", "size_neg", "value_ep"):
                f[k] = pd.Series(np.nan, index=cc)
        fin = se.load_latest_fina(dt)
        f["quality_roe"] = (fin.set_index("ts_code")["roe"] if len(fin) else pd.Series(dtype=float)).reindex(cc)

        cur = pd.to_datetime(dt)
        r90 = rc[(rc["rd"] > cur - pd.Timedelta(days=90)) & (rc["rd"] <= cur)]
        rprev = rc[(rc["rd"] > cur - pd.Timedelta(days=180)) & (rc["rd"] <= cur - pd.Timedelta(days=90))]
        if not r90.empty:
            g = r90[r90["ts_code"].isin(cc)]
            f["coverage_90d"] = g.groupby("ts_code").size().reindex(cc)
            f["org_breadth"] = g.groupby("ts_code")["org_name"].nunique().reindex(cc)
            f["rating_mean"] = g.groupby("ts_code")["score"].mean().reindex(cc)

            def upr(sr):
                sr = sr.dropna()
                if sr.empty:
                    return np.nan
                return float((sr >= 4).sum() - (sr <= 2).sum()) / len(sr)
            f["rating_up_ratio"] = g.groupby("ts_code")["score"].apply(upr).reindex(cc)
            if dt in db["mcap"].index:
                mcap_now = db["mcap"].loc[dt]
                gg = g.assign(up=g["max_price"].values / mcap_now.reindex(g["ts_code"]).values - 1.0)
                f["target_upside"] = gg.groupby("ts_code")["up"].median().reindex(cc)
            else:
                f["target_upside"] = pd.Series(np.nan, index=cc)
            prev_cnt = rprev[rprev["ts_code"].isin(cc)].groupby("ts_code").size()
            f["coverage_growth"] = f["coverage_90d"] / prev_cnt.reindex(cc).clip(lower=1)
            covered_180 = set(rprev["ts_code"].unique())
            cnt90 = f["coverage_90d"]
            f["new_coverage"] = pd.Series({c: (1.0 if (c in cnt90.index and not np.isnan(cnt90.get(c, np.nan)) and c not in covered_180) else 0.0) for c in cc})
            cur_map = g.dropna(subset=["org_name", "score"]).sort_values("rd").groupby(["ts_code", "org_name"])["score"].last()
            prev_map = rprev.dropna(subset=["org_name", "score"]).sort_values("rd").groupby(["ts_code", "org_name"])["score"].last()
            chg = (cur_map - prev_map.reindex(cur_map.index)).dropna()
            f["rating_change"] = chg.groupby(level=0).mean().reindex(cc)
        else:
            for k in ("coverage_90d", "org_breadth", "rating_mean", "rating_up_ratio", "target_upside",
                      "coverage_growth", "new_coverage", "rating_change"):
                f[k] = pd.Series(np.nan, index=cc)
        fw = fwd.reindex(cc)
        rec = {"date": nxt}
        for k in feat_names:
            v = f.get(k)
            if v is None:
                continue
            s2 = pd.DataFrame({"x": v, "y": fw}).dropna()
            if len(s2) < 100:
                continue
            ic = s2["x"].corr(s2["y"], method="spearman")
            q = pd.qcut(s2["x"].rank(method="first"), 10, labels=False, duplicates="drop")
            rec[k] = {"ic": round(float(ic), 5), "n": int(len(s2)),
                      "ls": round(float(s2["y"][q == q.max()].mean() - s2["y"][q == q.min()].mean()), 5)}
        rows.append(rec)
        if (i + 1) % 12 == 0:
            print("processed", i + 1, "months", flush=True)
    print("monthly rows:", len(rows), flush=True)

    summary = {}
    for k in feat_names:
        ics = [r[k]["ic"] for r in rows if isinstance(r.get(k), dict)]
        lss = [r[k]["ls"] for r in rows if isinstance(r.get(k), dict)]
        if len(ics) < 12:
            continue
        ics = np.array(ics)
        lss = np.array(lss)
        t = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else 0.0
        summary[k] = {"ic_mean": round(float(ics.mean()), 5), "ic_std": round(float(ics.std(ddof=1)), 5),
                      "t_stat": round(t, 2), "ic_pos_rate": round(float((ics > 0).mean()) * 100, 1),
                      "ls_mean": round(float(lss.mean()), 5),
                      "ls_ann_approx_pct": round(float((1 + lss.mean()) ** 12 - 1) * 100, 1),
                      "months": int(len(ics))}
    ranked = sorted(summary.items(), key=lambda kv: -abs(kv[1]["ic_mean"]))
    tag = mends[-2] if len(mends) > 1 else mends[-1]
    report = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "start": args.start, "window": f"{mends[0]} .. {mends[-2]}" if len(mends) > 1 else "",
                        "months": len(rows), "universe": "非ST/上市≥250日/价≥2；2023+ 另需流通市值≥30亿",
                        "caveats": [
                            "current stock_basic 过滤 ST/上市日 → ST 历史状态与幸存者偏差；",
                            "daily_basic 仅 2023+ → 换手/市值/PE/目标价空间仅 2023 起；",
                            "report_rc 2019+；评级缺失值记 NaN；",
                            "forecast <18 月，按 H34 未纳入；",
                            "挖掘轮非验证，无四关/无多重检验修正。"],
                        "note": "mining round v2"},
              "ranked": [{"feature": k, **v} for k, v in ranked],
              "monthly": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"strategy_mining_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    lines = [f"# 策略自挖一轮 v2 · {report['meta']['window']}（{len(rows)} 个月）", "",
             "> 挖掘轮（mining）非验证：无四关、无多重检验修正；2019 起价格特征、2023 起估值特征。", "",
             "| 排名 | 假设 | IC 均值 | t | IC>0 比例 | 多空月均 | 多空年化≈ | 有效月数 |", "|---|---|---|---|---|---|---|---|"]
    for i, (k, v) in enumerate(ranked, 1):
        lines.append(f"| {i} | {k} | {v['ic_mean']} | {v['t_stat']} | {v['ic_pos_rate']}% | {v['ls_mean']} | {v['ls_ann_approx_pct']}% | {v['months']} |")
    lines += ["", "## 口径与边界", "- IC=月频 Spearman；多空=十分位 top-bottom 等权（未计成本/换手）；",
              "- 宇宙：非ST/上市≥250日/价≥2（2023+ 另需流通市值≥30亿）；current stock_basic → 幸存者/ST 偏差；",
              "- report_rc 特征：coverage/评级均值/上调比例/目标价空间/覆盖增长/新覆盖/评级变化（PIT 用 report_date）；",
              "- forecast <18 月按 H34 不单独检验；", "- 候选须进入四关后才可讨论入库。"]
    (OUT / f"strategy_mining_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "features": len(summary),
                      "top": [(k, v["ic_mean"], v["t_stat"], v["months"]) for k, v in ranked[:8]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
