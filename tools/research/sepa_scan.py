# -*- coding: utf-8 -*-
"""SEPA 全市场扫描（A股）：TT 8 条 + PIT 基本面成长 + VCP 设置/突破状态。

输出分层：
  A 已突破（TT+基本面+VCP+放量突破当日）→ 次日可关注的买入候选
  B 接近枢轴（TT+基本面+VCP，收盘距枢轴≤5%）
  C 设置中（TT+基本面+VCP 但尚未接近枢轴）
  D 仅 TT+基本面（无 VCP 形态）
产出：research/reports/sepa_scan_<date>.{json,md} + portal/static/sepa_scan.json
用法：python tools/research/sepa_scan.py [--date 20261030]
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
STATIC = REPO / "portal" / "static"

# 复用回测中的 VCP 检测
sys.path.insert(0, str(REPO / "tools" / "research"))
from sepa_backtest import vcp_setup  # noqa: E402


def load_panel(dts: list[str]):
    panel = {}
    parts = []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet",
                          columns=["ts_code", "open", "high", "low", "close", "vol"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            f = d["adj_factor"].fillna(1.0)
        else:
            f = 1.0
        for c in ("open", "high", "low", "close"):
            d["a_" + c] = d[c] * f
        d["trade_date"] = dt
        parts.append(d[["ts_code", "trade_date", "a_open", "a_high", "a_low", "a_close", "vol"]])
        if len(parts) >= 300:
            ch = pd.concat(parts, ignore_index=True); parts = []
            for col in ("a_open", "a_high", "a_low", "a_close", "vol"):
                p = ch.pivot_table(index="trade_date", columns="ts_code", values=col)
                panel[col] = p if col not in panel else pd.concat([panel[col], p], axis=0)
    if parts:
        ch = pd.concat(parts, ignore_index=True)
        for col in ("a_open", "a_high", "a_low", "a_close", "vol"):
            p = ch.pivot_table(index="trade_date", columns="ts_code", values=col)
            panel[col] = p if col not in panel else pd.concat([panel[col], p], axis=0)
    for k in panel:
        panel[k].index = [str(x) for x in panel[k].index]
        panel[k] = panel[k].sort_index().astype("float32")
    return panel


def vcp_lite(c: np.ndarray, h: np.ndarray, l: np.ndarray, v: np.ndarray) -> dict | None:
    """VCP 宽松形态：ATR收缩+窄幅+缩量+接近40日高点。"""
    if len(c) < 60 or np.isnan(c[-1]):
        return None
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    atr10 = float(np.nanmean(tr[-10:]))
    atr50 = float(np.nanmean(tr[-50:]))
    if not np.isfinite(atr10) or not np.isfinite(atr50) or atr50 <= 0:
        return None
    atr_ratio = atr10 / atr50
    recent = c[-10:]
    rng = float((np.nanmax(recent) - np.nanmin(recent)) / max(1e-9, c[-1]))
    vol_dry = float(np.nanmean(v[-10:]) / max(1e-9, np.nanmean(v[-50:])))
    pivot = float(np.nanmax(c[-41:-1]))
    dist = float(c[-1] / pivot - 1)
    dd = float((np.nanmax(c[-60:]) - c[-1]) / max(1e-9, np.nanmax(c[-60:])))
    if not (atr_ratio <= 0.75 and rng <= 0.12 and vol_dry <= 0.90 and -0.10 <= dist <= 0.05 and dd <= 0.30):
        return None
    return {"pivot": pivot, "atr_ratio": round(atr_ratio, 2), "range10": round(rng, 3),
            "vol_dry": round(vol_dry, 2), "dist_to_pivot": round(dist, 3), "dd60": round(dd, 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--fund", choices=("growth", "none"), default="growth")
    args = ap.parse_args(argv)
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    pre = [d for d in ds if d <= (args.date or ds[-1])][-320:]
    P = load_panel(pre)
    dates = P["a_close"].index.tolist()
    i = len(dates) - 1
    dt = dates[i]
    print("scan date:", dt, "panel", P["a_close"].shape, flush=True)
    c = P["a_close"].iloc[i]
    csum = P["a_close"].cumsum()
    vsum = P["vol"].cumsum()
    s50 = (csum.iloc[i] - csum.iloc[i - 50]) / 50
    s150 = (csum.iloc[i] - csum.iloc[i - 150]) / 150
    s200 = (csum.iloc[i] - csum.iloc[i - 200]) / 200
    s200p = (csum.iloc[i - 21] - csum.iloc[i - 221]) / 200
    win = P["a_close"].iloc[i - 249: i + 1]
    hi, lo = win.max(), win.min()
    r252 = c / P["a_close"].iloc[i - 252] - 1
    rs = r252.rank(pct=True) * 98 + 1
    v50 = (vsum.iloc[i] - vsum.iloc[i - 50]) / 50

    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names, industry = sb["name"], sb["industry"].fillna("未知")
    listed = (pd.Timestamp(dt) - pd.to_datetime(sb["list_date"], format="%Y%m%d", errors="coerce")).dt.days
    uni = (~names.reindex(c.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
          (listed.reindex(c.index).fillna(0) >= 250) & (c >= 2) & c.notna()
    tt = (c > s150) & (c > s200) & (s150 > s200) & (s200 > s200p) & \
         (s50 > s150) & (s50 > s200) & (c > s50) & (c >= 1.30 * lo) & (c >= 0.75 * hi)
    base = tt & uni
    print("universe:", int(uni.sum()), "TT pass:", int(base.sum()), flush=True)

    # 基本面 PIT
    rows = []
    for f in sorted(glob.glob(str(DATA / "fina_indicator" / "*.parquet"))):
        try:
            d = pq.read_table(f, columns=["ts_code", "ann_date", "end_date", "netprofit_yoy",
                                          "tr_yoy", "or_yoy", "roe"]).to_pandas()
        except Exception:
            continue
        d = d[d["ann_date"].notna()]
        if not d.empty:
            rows.append(d)
    fd = pd.concat(rows, ignore_index=True)
    fd["ann_date"] = fd["ann_date"].astype(str)
    fd = fd.sort_values(["ts_code", "ann_date", "end_date"])
    fund = {}
    for code, g in fd.groupby("ts_code"):
        fund[code] = (g["ann_date"].values.astype(str), g["netprofit_yoy"].values.astype(float),
                      g["tr_yoy"].values.astype(float), g["or_yoy"].values.astype(float),
                      g["roe"].values.astype(float))

    def fmetrics(code):
        a = fund.get(code)
        if not a:
            return None
        ann, npy, tryy, oryy, roe = a
        j = int(np.searchsorted(ann, dt, side="right")) - 1
        if j < 0 or np.isnan(npy[j]):
            return None
        rev = tryy[j] if not np.isnan(tryy[j]) else oryy[j]
        if np.isnan(rev) or np.isnan(roe[j]):
            return None
        return {"netprofit_yoy": round(float(npy[j]), 1), "revenue_yoy": round(float(rev), 1),
                "roe": round(float(roe[j]), 1)}

    def growth_ok(m):
        return bool(m and m["netprofit_yoy"] >= 15 and m["revenue_yoy"] >= 10 and m["roe"] >= 8)

    base_codes = list(c.index[base.fillna(False)])
    passed_fund, with_vcp, near, breakout, setup_only, d_list, lite = [], [], [], [], [], [], []
    for code in base_codes:
        ci = c.index.get_loc(code)
        m = fmetrics(code)
        if args.fund == "growth" and not growth_ok(m):
            continue
        passed_fund.append(code)
        cc = P["a_close"].iloc[max(0, i - 79): i + 1, ci].values
        hh = P["a_high"].iloc[max(0, i - 79): i + 1, ci].values
        ll = P["a_low"].iloc[max(0, i - 79): i + 1, ci].values
        vv = P["vol"].iloc[max(0, i - 79): i + 1, ci].values
        if len(cc) < 60 or np.isnan(cc[-1]):
            continue
        st = vcp_setup(cc, hh, ll, vv, require_near=False)
        rec = {"code": code, "name": names.get(code), "industry": industry.get(code),
               "close": round(float(c[code]), 2), "rs": int(rs.get(code, 0)),
               "fund": m or {}, "pivot": round(st["pivot"], 2) if st else None,
               "last_low": round(st["last_low"], 2) if st else None,
               "vol_dry": round(st["vol_dry"], 3) if st else None}
        if st is None:
            d_list.append(rec)
            lt = vcp_lite(cc, hh, ll, vv)
            if lt is not None:
                rec2 = dict(rec)
                rec2["vcp_lite"] = lt
                lite.append(rec2)
        if st:
            with_vcp.append(rec)
            pivot = st["pivot"]
            if c[code] > pivot and float(P["vol"].iloc[i, ci]) > 1.4 * float(v50.iloc[ci]):
                breakout.append(rec)
            elif c[code] >= pivot * 0.95:
                near.append(rec)
            else:
                setup_only.append(rec)
    for lst in (with_vcp, breakout, near, setup_only, d_list, lite):
        lst.sort(key=lambda x: -x["rs"])
    # 仅 TT（不过基本面）Top20，供宽口径观察
    tt_top = []
    for code in sorted(c.index[base.fillna(False)], key=lambda x: -float(rs.get(x, 0)))[:20]:
        tt_top.append({"code": code, "name": names.get(code), "industry": industry.get(code),
                       "close": round(float(c[code]), 2), "rs": int(rs.get(code, 0))})
    print("fund pass:", len(passed_fund), "vcp:", len(with_vcp), "breakout:", len(breakout),
          "near:", len(near), "setup:", len(setup_only), flush=True)

    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "scan_date": dt, "universe": int(uni.sum()), "tt_pass": int(base.sum()),
               "fund_pass": len(passed_fund), "vcp": len(with_vcp),
               "funnel": {"A_已突破": len(breakout), "B_接近枢轴": len(near),
                           "C_设置中": len(setup_only),
                           "D_仅TT+基本面": len(passed_fund) - len(with_vcp)},
               "breakout": breakout[:40], "near": near[:40], "setup": setup_only[:30],
               "only_tt_fund": d_list[:40], "tt_top": tt_top, "vcp_lite": lite[:40],
               "rule": ("TT 8条 + PIT 基本面(净利yoy≥15/营收yoy≥10/ROE≥8) + VCP" if args.fund == "growth" else "TT 8条 + VCP（不加基本面）"),
               "disclaimer": "研究扫描，不构成投资建议；次日交易需自行复核"}
    OUT.mkdir(parents=True, exist_ok=True); STATIC.mkdir(parents=True, exist_ok=True)
    tag = dt
    (OUT / f"sepa_scan_{tag}_{args.fund}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (STATIC / ("sepa_scan.json" if args.fund == "growth" else "sepa_scan_nofund.json")).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# SEPA 全市场扫描 · {dt}", "",
          f"- 全市场 {payload['universe']} 只 → TT 通过 {payload['tt_pass']} → +基本面 {payload['fund_pass']} → "
          f"VCP 设置 {payload['vcp']} → 已突破 {len(breakout)} / 接近枢轴 {len(near)} / 设置中 {len(setup_only)}", "",
          "## A. 已突破（放量站上枢轴，次日候选）", "",
          "| # | 代码 | 名称 | 行业 | 收盘 | RS | 枢轴 | 净利yoy | 营收yoy | ROE |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(breakout[:30], 1):
        md.append(f"| {k} | {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['rs']} | {r['pivot']} | "
                  f"{r['fund']['netprofit_yoy']} | {r['fund']['revenue_yoy']} | {r['fund']['roe']} |")
    md += ["", "## B. 接近枢轴（≤5%，关注）", "", "| # | 代码 | 名称 | 行业 | 收盘 | RS | 枢轴 | 净利yoy | ROE |", "|---|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(near[:30], 1):
        md.append(f"| {k} | {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['rs']} | {r['pivot']} | {r['fund']['netprofit_yoy']} | {r['fund']['roe']} |")
    md += ["", "## C. 设置中（VCP 但未接近枢轴，观察池）", "", "| 代码 | 名称 | 行业 | RS | 枢轴 | 净利yoy |", "|---|---|---|---|---|---|"]
    for r in setup_only[:20]:
        md.append(f"| {r['code']} | {r['name']} | {r['industry']} | {r['rs']} | {r['pivot']} | {r['fund']['netprofit_yoy']} |")
    md += ["", ("## D. 仅 TT+基本面（无 VCP 形态）" if args.fund == "growth" else "## D. 仅 TT（无 VCP 形态）"), "", "| 代码 | 名称 | 行业 | 收盘 | RS | 净利yoy | 营收yoy | ROE |", "|---|---|---|---|---|---|---|---|"]
    for r in d_list[:30]:
        fm = r["fund"] or {}
        md.append(f"| {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['rs']} | "
                  f"{fm.get('netprofit_yoy', '')} | {fm.get('revenue_yoy', '')} | {fm.get('roe', '')} |")
    md += ["", "## E. 仅 TT 通过（不要求基本面）Top20", "", "| 代码 | 名称 | 行业 | 收盘 | RS |", "|---|---|---|---|---|"]
    for r in tt_top:
        md.append(f"| {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['rs']} |")
    md += ["", "## F. VCP 宽松形态（ATR 收缩/窄幅/缩量/接近高点，观察池）", "",
           "| 代码 | 名称 | 行业 | 收盘 | RS | 枢轴 | 距枢轴% | ATR比 | 10日振幅 | 缩量比 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in lite[:30]:
        lt = r["vcp_lite"]
        md.append(f"| {r['code']} | {r['name']} | {r['industry']} | {r['close']} | {r['rs']} | {r['pivot']} | "
                  f"{lt['dist_to_pivot']*100:.1f} | {lt['atr_ratio']} | {lt['range10']*100:.1f} | {lt['vol_dry']} |")
    md += ["", "> 研究扫描，不构成投资建议；入场/止损/仓位须按 SEPA 规则自行复核。"]
    (OUT / f"sepa_scan_{tag}_{args.fund}.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"date": dt, "funnel": payload["funnel"], "A": [r["code"] for r in breakout[:10]],
                      "B": [r["code"] for r in near[:10]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
