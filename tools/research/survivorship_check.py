# -*- coding: utf-8 -*-
"""幸存者偏差评估：当前股票池 vs 含退市股票的全历史代码池（2019-2026）。

方法：
  U_cur = current stock_basic（ST 过滤 + list_date PIT + 价≥2 + 排除BJ）
  U_all = 2019-2026 所有出现过的代码（首次出现日作为上市日代理；无名称→不做 ST 过滤；价≥2；排除BJ）
策略：mom_120（-120日收益）top20 等权月频；成本 0.15% 单边×换手。
注意：U_all 缺 ST 过滤（保守方向），上市日为首日交易代理——差异为「幸存者+过滤口径」合并效应，方向上界。
输出：research/reports/survivorship_<tag>.{json,md}
"""
from __future__ import annotations

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
COST = 0.0015
K = 20


def load_adj(dts: list[str]):
    parts = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / ("d_" + dt + ".parquet"), columns=["ts_code", "close"]).to_pandas()
        af = DATA / "adj_factor" / ("d_" + dt + ".parquet")
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        parts.append(d[["ts_code", "trade_date", "adj"]])
        if len(parts) >= 400:
            ch = pd.concat(parts, ignore_index=True); parts = []
            piv = ch.pivot_table(index="trade_date", columns="ts_code", values="adj")
            if "acc" not in locals():
                acc = piv
            else:
                acc = pd.concat([acc, piv], axis=0)
    if parts:
        ch = pd.concat(parts, ignore_index=True)
        piv = ch.pivot_table(index="trade_date", columns="ts_code", values="adj")
        if "acc" not in locals():
            acc = piv
        else:
            acc = pd.concat([acc, piv], axis=0)
    acc.index = [str(x) for x in acc.index]
    acc = acc.loc[:, ~acc.columns.duplicated()]
    return acc.sort_index().astype("float32")


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-150:]
    dts = pre + [d for d in ds if d >= "20190101"]
    adj = load_adj(dts)
    dates = adj.index.tolist()
    adj19 = adj.loc[adj.index >= "20190101"]
    first_seen = {}
    for dt in adj19.index:
        row = adj19.loc[dt].dropna()
        for code in row.index:
            first_seen.setdefault(code, dt)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    cur_codes = set(sb.index)
    all_codes = set(first_seen)
    delisted = all_codes - cur_codes
    print("codes: all", len(all_codes), "current", len(cur_codes), "delisted/absent", len(delisted), flush=True)
    s = pd.Series(adj19.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]

    def run(universe: str) -> dict:
        prev = set(); rets, mkts = [], []
        for j in range(len(mends) - 1):
            dt, nxt = mends[j], mends[j + 1]
            if dt not in adj.index or nxt not in adj.index:
                continue
            c = adj19.loc[dt]
            px = c.dropna()
            if universe == "cur":
                ok = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                     ((pd.Timestamp(dt) - pd.to_datetime(sb["list_date"].reindex(px.index), format="%Y%m%d", errors="coerce")).dt.days >= 250)
            else:
                fs_dt = pd.to_datetime(pd.Series({k: first_seen[k] for k in px.index}), format="%Y%m%d")
                ok = ((pd.Timestamp(dt) - fs_dt).dt.days >= 250)
            ok = ok & (px >= 2) & ~px.index.str.endswith(".BJ")
            cc = px.index[ok.fillna(False)]
            base_i = dates.index(dt) - 120
            if base_i < 0:
                continue
            score = (-(px / adj.loc[dates[base_i]] - 1)).reindex(cc).dropna()
            picks = score.sort_values(ascending=False).head(K).index
            if len(picks) < 5:
                continue
            r = adj19.loc[nxt] / px - 1
            pr = float(r.reindex(picks).mean())
            pr -= len(set(picks) - prev) / len(picks) * COST * 2
            rets.append(pr); mkts.append(float(r.reindex(cc).mean()))
            prev = set(picks)
        rets = np.array(rets); mkts = np.array(mkts)
        nav = np.cumprod(1 + rets); yrs = len(rets) / 12
        ex = rets - mkts
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0
        return {"universe": universe, "n_codes": int(len(all_codes if universe == "all" else cur_codes)),
                "cagr_pct": round(float(nav[-1] ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 2),
                "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(t, 2),
                "months": len(rets)}

    cur = run("cur"); allr = run("all")
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "strategy": "mom_120 top20 等权 月频",
                    "note": "幸存者偏差评估；U_all 无ST过滤、上市日=首日交易代理"},
           "counts": {"all": len(all_codes), "current": len(cur_codes), "delisted_or_absent": len(delisted)},
           "results": [cur, allr]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"survivorship_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 幸存者偏差评估 · {rep['meta']['window']}", "",
         f"- 代码池：2019+ 出现 {len(all_codes)} 个；当前 stock_basic {len(cur_codes)} 个；**退市/缺失 {len(delisted)} 个（{len(delisted)/len(all_codes)*100:.1f}%）**",
         "- 策略：mom_120 top20 等权月频；成本 0.15% 单边×换手", "",
         "| 代码池 | 股票数 | CAGR | MDD | 月均超额 | t |", "|---|---|---|---|---|---|",
         f"| 当前 stock_basic（幸存） | {cur['n_codes']} | {cur['cagr_pct']}% | {cur['mdd_pct']}% | {cur['excess_monthly_pct']}% | {cur['excess_t']} |",
         f"| 含退市全代码池 | {allr['n_codes']} | {allr['cagr_pct']}% | {allr['mdd_pct']}% | {allr['excess_monthly_pct']}% | {allr['excess_t']} |",
         "", "## 读法", "- 两池差异 = 幸存者偏差+ST口径+上市日代理的合并效应（方向上界）；",
         "- 若全池明显更差 → 此前所有策略收益偏乐观；若差异小 → 策略结论对幸存者不敏感。",
         "- 注意：U_all 缺 ST 历史过滤，会**低估**全池表现（保守方向），因此差异应理解为「偏差上界」。"]
    (OUT / f"survivorship_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"counts": rep["counts"], "cur": cur, "all": allr}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
