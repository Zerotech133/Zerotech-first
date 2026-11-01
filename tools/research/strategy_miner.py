# -*- coding: utf-8 -*-
"""策略自挖一轮：用新数据仓（report_rc/forecast/价格估值）批量筛假设，月频 IC + 多空分层。

边界：
  - 本脚本只做「挖掘轮」：批量假设的 IC/分层统计，**不是验证**（无 walk-forward 四关、无容量）；
  - 多重测试不做 p 值修正，仅按 IC/多空质量排序，供人工选 2-3 个进入四关；
  - forecast 修订数据仅 2026 年（<18 个月）→ 按 H34 纪律**拒绝单独检验**，只作辅助；
  - 所有产出标 research-only，不入策略库。

用法：python tools/research/strategy_miner.py [--months 34]
输出：research/reports/strategy_mining_<date>.{json,md}
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
sys.path.insert(0, str(REPO / "tools" / "research"))
import selection_engine as se  # noqa: E402


def trade_dates(start: str = "20231001") -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    return [Path(f).stem[2:] for f in fs if Path(f).stem[2:] >= start]


def month_ends(dts: list[str]) -> list[str]:
    s = pd.Series(dts)
    ym = s.str[:6]
    return [g.iloc[-1] for _, g in s.groupby(ym)]


def load_panel(dts: list[str]) -> dict[str, pd.DataFrame]:
    closes, turns, mcaps, pes = [], {}, {}, {}
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=["ts_code", "close", "pct_chg"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        closes.append(d[["ts_code", "trade_date", "adj"]])
        db = DATA / "daily_basic" / f"d_{dt}.parquet"
        if db.exists():
            b = pq.read_table(db, columns=["ts_code", "turnover_rate", "circ_mv", "pe_ttm"]).to_pandas()
            turns[dt] = b.set_index("ts_code")["turnover_rate"]
            mcaps[dt] = b.set_index("ts_code")["circ_mv"]
            pes[dt] = b.set_index("ts_code")["pe_ttm"]
    raw = pd.concat(closes, ignore_index=True)
    adj = raw.pivot_table(index="trade_date", columns="ts_code", values="adj")
    adj.index = [str(x) for x in adj.index]
    return {"adj": adj, "turn": pd.DataFrame(turns).T, "mcap": pd.DataFrame(mcaps).T, "pe": pd.DataFrame(pes).T}


def load_report_rc() -> pd.DataFrame:
    fs = sorted(glob.glob(str(DATA / "report_rc" / "*.parquet")))
    cols = ["ts_code", "report_date", "org_name", "rating", "max_price", "min_price"]
    parts = []
    for f in fs:
        try:
            d = pq.read_table(f, columns=cols).to_pandas()
        except Exception:  # noqa: BLE001
            continue
        parts.append(d)
    if not parts:
        return pd.DataFrame(columns=cols)
    raw = pd.concat(parts, ignore_index=True)
    raw["report_date"] = raw["report_date"].astype(str)
    return raw


RATING_MAP = {"买入": 5, "强烈推荐": 5, "推荐": 4, "增持": 4, "优于大市": 4,
              "中性": 3, "持有": 3, "谨慎推荐": 3, "减持": 2, "弱于大市": 2, "卖出": 1}


def report_features(rc: pd.DataFrame, days: int = 90) -> dict[str, pd.DataFrame]:
    """按 report_date 分桶，供每个月末取最近 days 天。"""
    out = {}
    if rc.empty:
        return out
    rc = rc.copy()
    rc["score"] = rc["rating"].map(lambda x: RATING_MAP.get(str(x).strip(), np.nan))
    rc["dt"] = pd.to_datetime(rc["report_date"], format="%Y%m%d", errors="coerce")
    rc["ym"] = rc["dt"].dt.strftime("%Y%m")
    for ym, g in rc.groupby("ym"):
        out[ym] = g
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=34)
    args = ap.parse_args(argv)
    dts = trade_dates()
    print("trade days:", len(dts), dts[0], "..", dts[-1], flush=True)
    panel = load_panel(dts)
    adj, turn, mcap, pe = panel["adj"], panel["turn"], panel["mcap"], panel["pe"]
    print("panels:", {k: v.shape for k, v in panel.items()}, flush=True)
    mends = month_ends(dts)[-args.months:]
    print("month ends:", len(mends), mends[0], "..", mends[-1], flush=True)

    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    rc = load_report_rc()
    print("report_rc rows:", len(rc), flush=True)
    if not rc.empty:
        rc["score"] = rc["rating"].map(lambda x: RATING_MAP.get(str(x).strip(), np.nan))
    rdt = pd.to_datetime(rc["report_date"], format="%Y%m%d", errors="coerce") if not rc.empty else pd.Series(dtype="datetime64[ns]")

    rows = []
    for i, dt in enumerate(mends[:-1]):
        nxt = mends[i + 1]
        if dt not in adj.index or nxt not in adj.index:
            continue
        px = adj.loc[dt]
        fwd = adj.loc[nxt] / px - 1.0
        # 基础宇宙
        listed = (pd.to_datetime(dt) - pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")).dt.days
        uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed.reindex(px.index).fillna(0) >= 250) & (px >= 2) & (mcap.loc[dt] >= 3e5)
        uni = uni.fillna(False)
        codes = px.index[uni]
        if len(codes) < 200:
            continue
        # 价格类特征（严格只用 dt 及之前的数据，避免前视）
        hist = adj.loc[:dt]
        px_hist = hist.iloc[-1]
        f = {}
        f["mom_20"] = (px_hist / hist.iloc[-21] - 1).reindex(codes) if len(hist) > 21 else np.nan
        f["mom_120"] = (px_hist / hist.iloc[-121] - 1).reindex(codes) if len(hist) > 121 else np.nan
        f["rev_5"] = -(px_hist / hist.iloc[-6] - 1).reindex(codes)
        ret_d = hist.tail(21).pct_change().std().reindex(codes)
        f["vol_20"] = ret_d
        f["turnover_20"] = turn.loc[:dt].tail(20).mean().reindex(codes)
        f["size_neg"] = -np.log(mcap.loc[dt].reindex(codes).clip(lower=1))
        f["value_ep"] = (1.0 / pe.loc[dt].replace(0, np.nan)).reindex(codes)
        # ROE（PIT，季度缓存）
        fin = se.load_latest_fina(dt).set_index("ts_code")["roe"] if hasattr(se.load_latest_fina(dt), "set_index") else None
        f["quality_roe"] = fin.reindex(codes) if fin is not None else np.nan
        # report_rc 特征（最近 90 天，按 report_date 过滤）
        start = pd.to_datetime(dt) - pd.Timedelta(days=90)
        mask = (rdt >= start) & (rdt <= pd.to_datetime(dt))
        rsub = rc[mask]
        if not rsub.empty:
            g = rsub[rsub["ts_code"].isin(codes)]
            cnt = g.groupby("ts_code").size().reindex(codes)
            f["coverage_90d"] = cnt
            f["org_breadth"] = g.groupby("ts_code")["org_name"].nunique().reindex(codes)
            f["rating_mean"] = g.groupby("ts_code")["score"].mean().reindex(codes)
            def upratio(s):
                if s.dropna().empty:
                    return np.nan
                return float((s >= 4).sum() - (s <= 2).sum()) / max(1, len(s.dropna()))
            f["rating_up_ratio"] = g.groupby("ts_code")["score"].apply(upratio).reindex(codes)
            mc = mcap.loc[dt]
            boost = g.assign(up=g["max_price"] / mc.reindex(g["ts_code"]).values - 1.0)
            f["target_upside"] = boost.groupby("ts_code")["up"].median().reindex(codes)
        else:
            for k in ("coverage_90d", "org_breadth", "rating_mean", "rating_up_ratio", "target_upside"):
                f[k] = pd.Series(np.nan, index=codes)
        # 计算 IC / 分层
        fw = fwd.reindex(codes)
        rec = {"date": nxt}
        for k, v in f.items():
            s = pd.DataFrame({"x": v, "y": fw}).dropna()
            if len(s) < 100:
                rec[k] = {"ic": np.nan, "n": int(len(s))}
                continue
            ic = s["x"].corr(s["y"], method="spearman")
            q = pd.qcut(s["x"].rank(method="first"), 10, labels=False, duplicates="drop")
            top = s["y"][q == q.max()].mean()
            bot = s["y"][q == q.min()].mean()
            rec[k] = {"ic": round(float(ic), 5), "n": int(len(s)),
                      "ls": round(float(top - bot), 5)}
        rows.append(rec)
    print("monthly rows:", len(rows), flush=True)

    # 汇总
    summary = {}
    feats = [k for k in rows[0] if k != "date"] if rows else []
    for k in feats:
        ics = [r[k]["ic"] for r in rows if isinstance(r.get(k), dict) and not np.isnan(r[k]["ic"])]
        lss = [r[k]["ls"] for r in rows if isinstance(r.get(k), dict) and not np.isnan(r[k].get("ls", np.nan))]
        if len(ics) < 10:
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
    report = {
        "meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "window": f"{rows[0]['date'] if rows else ''} .. {rows[-1]['date'] if rows else ''}",
                 "months": len(rows), "universe": "非ST/上市≥250日/价≥2/流通市值≥30亿",
                 "note": "挖掘轮（mining），非验证；无四关/无多重检验修正；供人工挑选后进入四关",
                 "forecast_data": "forecast 仅 2026 年 → 按 H34 纪律不单独检验，未纳入"},
        "ranked": [{"feature": k, **v} for k, v in ranked],
        "monthly": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    tag = mends[-2] if len(mends) > 1 else mends[0]
    (OUT / f"strategy_mining_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    lines = [f"# 策略自挖一轮 · {report['meta']['window']}（{len(rows)} 个月）", "",
             "> 挖掘轮（mining）非验证：无四关、无多重检验修正、无容量/成本细节；供人工挑选 2–3 个进入四关。", "",
             "| 排名 | 假设 | IC 均值 | t | IC>0 比例 | 多空月均 | 多空年化≈ | 月数 |", "|---|---|---|---|---|---|---|---|"]
    for i, (k, v) in enumerate(ranked, 1):
        lines.append(f"| {i} | {k} | {v['ic_mean']} | {v['t_stat']} | {v['ic_pos_rate']}% | {v['ls_mean']} | {v['ls_ann_approx_pct']}% | {v['months']} |")
    lines += ["", "## 说明", "- IC=月频 Spearman 秩相关；多空=十分位 top-bottom 等权（未计成本/换手）；",
              "- forecast 修订数据不足 18 个月，按 H34 不单独检验；",
              "- 候选需进入四关（前视/成本/容量/regime）后才可讨论入库。"]
    (OUT / f"strategy_mining_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "top": [(k, v["ic_mean"], v["t_stat"], v["ls_ann_approx_pct"]) for k, v in ranked[:5]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
