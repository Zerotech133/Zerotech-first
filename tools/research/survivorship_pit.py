# -*- coding: utf-8 -*-
"""PIT 股票池重建（含退市股 + 历史 ST 名称）并重算 mom_120。

数据：
  - daily/adj：行情（2019+）
  - bak_basic：每代码日度 name/list_date 历史（2015-12 ~ 今）→ PIT 名称与上市日
  - stock_basic：当前池（对照基线）

三个口径：
  U_cur : current stock_basic（当前名称/当前 list_date）——此前所有回测口径
  U_pit : 全历史代码（daily 出现过）+ bak_basic PIT 名称/list_date → 含退市股、按当日 ST 过滤
  U_delist_only : U_pit 但只保留「不在当前池」的代码（退市/缺失组），用于分解偏差来源

输出：research/reports/survivorship_pit_<tag>.{json,md}
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


def load_adj(dts: list[str]) -> pd.DataFrame:
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


def load_bak_monthends(mends: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """名称面板（月末，index=月末日，columns=ts_code）+ list_date 映射。"""
    ms = set(mends)
    fs = sorted(glob.glob(str(DATA / "bak_basic" / "*.parquet")))
    rows = []
    list_dates = {}
    for f in fs:
        code = Path(f).stem.replace("_", ".")
        try:
            t = pq.read_table(f, columns=["trade_date", "name", "list_date"]).to_pandas()
        except Exception:  # noqa: BLE001
            continue
        ld = t["list_date"].dropna()
        if len(ld):
            list_dates[code] = str(ld.iloc[0])
        t = t[t["trade_date"].isin(ms)]
        if len(t):
            t = t.assign(ts_code=code)
            rows.append(t[["trade_date", "ts_code", "name"]])
    names = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["trade_date", "ts_code", "name"])
    npanel = names.pivot_table(index="trade_date", columns="ts_code", values="name", aggfunc="last")
    return npanel, pd.Series(list_dates)


def main() -> int:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d < "20190101"][-150:]
    dts = pre + [d for d in ds if d >= "20190101"]
    adj = load_adj(dts)
    adj19 = adj.loc[adj.index >= "20190101"]
    dates = adj.index.tolist()
    s = pd.Series(adj19.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]
    print("panel", adj.shape, "months", len(mends), flush=True)
    npanel, bak_list = load_bak_monthends(mends)
    print("name panel", npanel.shape, "bak list_date", len(bak_list), flush=True)
    sb = pd.read_parquet(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).set_index("ts_code")
    cur_dates = pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")
    cur_codes = set(sb.index)

    def universe(dt: str, mode: str) -> pd.Index:
        px = adj19.loc[dt].dropna()
        px = px[px >= 2]
        if mode == "cur":
            ok = (~sb["name"].reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
                 ((pd.Timestamp(dt) - cur_dates.reindex(px.index)).dt.days >= 250)
        else:
            nm = npanel.loc[dt].reindex(px.index) if dt in npanel.index else pd.Series("", index=px.index)
            ok = (~nm.fillna("").str.contains(r"ST|\*ST|退", regex=True))
            ld = pd.to_datetime(bak_list.reindex(px.index), format="%Y%m%d", errors="coerce")
            ld = ld.fillna(cur_dates.reindex(px.index))
            ok = ok & ((pd.Timestamp(dt) - ld).dt.days >= 250)
            if mode == "delist":  # 只看非当前池代码
                ok = ok & ~px.index.isin(cur_codes)
        ok = ok & ~px.index.str.endswith(".BJ")
        return px.index[ok.fillna(False)]

    def run(mode: str) -> dict:
        prev = set(); rets, mkts = [], []
        for j in range(len(mends) - 1):
            dt, nxt = mends[j], mends[j + 1]
            if dt not in adj19.index or nxt not in adj19.index:
                continue
            i = dates.index(dt)
            if i < 121:
                continue
            cc = universe(dt, mode)
            min_n = 5 if mode == "delist" else 50
            if len(cc) < min_n:
                continue
            px = adj19.loc[dt]
            score = (-(px / adj.loc[dates[i - 120]] - 1)).reindex(cc).dropna()
            picks = score.sort_values(ascending=False).head(K).index
            if len(picks) < 5:
                continue
            r = adj19.loc[nxt] / px - 1
            pr = float(r.reindex(picks).mean()) - len(set(picks) - prev) / len(picks) * COST * 2
            rets.append(pr); mkts.append(float(r.reindex(cc).mean()))
            prev = set(picks)
        rets = np.array(rets); mkts = np.array(mkts)
        if len(rets) < 6:
            return {"mode": mode, "months": len(rets), "cagr_pct": None, "mdd_pct": None, "excess_monthly_pct": None, "excess_t": None}
        nav = np.cumprod(1 + rets); yrs = len(rets) / 12
        ex = rets - mkts
        t = float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if ex.std(ddof=1) > 0 else 0
        return {"mode": mode, "months": len(rets), "cagr_pct": round(float(nav[-1] ** (1 / yrs) - 1) * 100, 2),
                "mdd_pct": round(float((nav / np.maximum.accumulate(nav) - 1).min()) * 100, 2),
                "excess_monthly_pct": round(float(ex.mean()) * 100, 3), "excess_t": round(t, 2)}

    res = [run("cur"), run("pit"), run("delist")]
    tag = datetime.now().strftime("%Y%m%d")
    rep = {"meta": {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "window": f"{mends[0]} .. {mends[-2]}", "strategy": "mom_120 top20 等权 月频",
                    "data": "bak_basic 历史名称/list_date（PIT）+ daily/adj"},
           "counts": {"bak_codes": int(npanel.shape[1]), "current_codes": len(cur_codes)},
           "results": res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"survivorship_pit_{tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    cur, pit, dl = res
    L = [f"# PIT 股票池重建与重算 · {rep['meta']['window']}", "",
         "- 口径：U_cur=当前 stock_basic；U_pit=全历史代码+bak_basic 历史名称/list_date（含退市股）；U_delist=仅非当前池代码",
         "- 策略：mom_120 top20 等权月频；成本 0.15% 单边×换手", "",
         "| 口径 | 月数 | CAGR | MDD | 月均超额 | t |", "|---|---|---|---|---|---|"]
    for r in res:
        L.append(f"| {r['mode']} | {r['months']} | {r['cagr_pct']}% | {r['mdd_pct']}% | {r['excess_monthly_pct']}% | {r['excess_t']} |")
    L += ["", "## 结论",
          f"- 当前池（旧口径）：CAGR {cur['cagr_pct']}%、t={cur['excess_t']}；",
          f"- **PIT 全池（含退市+历史ST）：CAGR {pit['cagr_pct']}%、t={pit['excess_t']}**；",
          f"- 仅退市/缺失组：CAGR {dl['cagr_pct']}%、t={dl['excess_t']}（{dl['months']} 月）——说明缺失股拖累；", 
          "- 以 U_pit 作为策略的诚实估计；后续所有策略重算均应采用此口径。"]
    (OUT / f"survivorship_pit_{tag}.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
