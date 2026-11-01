# -*- coding: utf-8 -*-
"""H14 影子盘（A 方案）：质量组合 + 质量陷阱组权重上限 5%。

口径（草案）：
- 股票池：A 股全市场，剔除 ST/退市、上市<60 交易日、缺 ROE/PE 的股票；
- 质量分：PIT ROE（ann_date<=t 的最新一期），截面五分位，Q5=高ROE；
- 估值分：PE_TTM（>0），截面三分位，T3=高PE；
- 陷阱组：ROE Q5 且 PE T3；
- 基准组合：质量 Q5 等权（无约束）；
- 约束组合：同持仓，但陷阱组总权重 ≤5%，超出部分按其余持仓等权分配；
- 月度调仓（月末信号，次月首日执行）；成本 30bp/次换手（用月换手估计）。

输出：
- research/reports/H14_shadow_<stamp>.{md,json}：回测对比 + 最新影子持仓
- work/research/h14_shadow/shadow_<yyyymm>.json：首月影子盘（初始持仓）
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO = Path(r"C:\项目\老田的AI助手")
DATA = REPO / "quant_backtest" / "data" / "tushare"
OUT = REPO / "research" / "reports"
SHADOW_DIR = REPO / "work" / "research" / "h14_shadow"
START = "20210722"
END = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")  # 动态：每日运行都能覆盖到最新交易日
COST = 0.003          # 30bp/次换手（按单边换手近似）
TRAP_CAP = 0.05
MIN_LIST_DAYS = 60


def load_basic() -> pd.DataFrame:
    f = sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[0]
    b = pq.read_table(f, columns=["ts_code", "name", "list_date"]).to_pandas()
    b["list_date"] = b["list_date"].astype(str)
    return b


def month_end_dates() -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily_basic" / "d_*.parquet")))
    days = [Path(f).stem[2:] for f in fs if START <= Path(f).stem[2:] <= END]
    by_month = {}
    for d in days:
        by_month[d[:6]] = max(by_month.get(d[:6], ""), d)
    return sorted(by_month.values())


def load_monthly_basic(dates: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for d in dates:
        f = DATA / "daily_basic" / ("d_%s.parquet" % d)
        if not f.exists():
            continue
        tb = pq.read_table(f, columns=["ts_code", "trade_date", "close", "pe_ttm"]).to_pandas()
        out[d] = tb
    return out


def load_pit_roe() -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for f in sorted(glob.glob(str(DATA / "fina_indicator" / "*.parquet"))):
        try:
            tb = pq.read_table(f, columns=["ts_code", "ann_date", "roe"]).to_pydict()
        except Exception:  # noqa: BLE001
            continue
        for code, ann, roe in zip(tb["ts_code"], tb["ann_date"], tb["roe"]):
            if code is None or not ann or roe is None:
                continue
            out.setdefault(code, []).append((str(ann), float(roe)))
    for k in out:
        out[k].sort()
    return out


def roe_asof(series: list[tuple[str, float]], date: str) -> float | None:
    lo, hi = 0, len(series) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= date:
            best = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def build_portfolio(df: pd.DataFrame, roe: float | None) -> pd.DataFrame:
    """df: ts_code, close, pe_ttm, name, list_date, date；返回带权重（无约束/约束）的组合。"""
    d = df.copy()
    d = d[(d["pe_ttm"].notna()) & (d["pe_ttm"] > 0) & (d["close"].notna()) & (d["close"] > 0)]
    d = d[~d["name"].astype(str).str.contains("ST|退", na=False)]
    d["list_ok"] = pd.to_datetime(d["list_date"], format="%Y%m%d", errors="coerce") <= (
        pd.to_datetime(d["date"], format="%Y%m%d") - pd.Timedelta(days=MIN_LIST_DAYS))
    d = d[d["list_ok"]]
    d["roe_pit"] = d["ts_code"].map(lambda c: roe_asof(roe_map.get(c, []), d["date"].iloc[0]) if len(roe_map.get(c, [])) else np.nan)
    d = d[d["roe_pit"].notna()].copy()
    if len(d) < 50:
        return pd.DataFrame()
    d["roe_q"] = pd.qcut(d["roe_pit"], 5, labels=False, duplicates="drop") + 1
    d["pe_t"] = pd.qcut(d["pe_ttm"], 3, labels=False, duplicates="drop") + 1
    d["quality"] = d["roe_q"] >= d["roe_q"].max()
    d["trap"] = d["quality"] & (d["pe_t"] >= d["pe_t"].max())
    base = d[d["quality"]].copy()
    if len(base) < 10:
        return pd.DataFrame()
    base["w_unconstrained"] = 1.0 / len(base)
    trap_w = base["w_unconstrained"][base["trap"]].sum()
    w = base["w_unconstrained"].copy()
    if trap_w > TRAP_CAP:
        scale = TRAP_CAP / trap_w
        w[base["trap"]] = w[base["trap"]] * scale
        excess = 1.0 - w.sum()
        nontrap = ~base["trap"]
        if nontrap.sum() > 0:
            w[nontrap] = w[nontrap] + excess / nontrap.sum()
    base["w_constrained"] = w
    return base


def metrics(nav: pd.Series) -> dict:
    rets = nav.pct_change().dropna()
    if len(rets) == 0:
        return {}
    ann = (nav.iloc[-1] / nav.iloc[0]) ** (12 / len(nav)) - 1
    vol = rets.std() * np.sqrt(12)
    mdd = float((nav / nav.cummax() - 1).min())
    sharpe = ann / vol if vol else 0
    return {"months": int(len(nav)), "total_return_pct": round((nav.iloc[-1] / nav.iloc[0] - 1) * 100, 2),
            "ann_return_pct": round(ann * 100, 2), "vol_pct": round(vol * 100, 2),
            "max_drawdown_pct": round(mdd * 100, 2), "sharpe": round(sharpe, 2)}


def main() -> int:
    global roe_map
    basic = load_basic()
    info = basic.set_index("ts_code")[["name", "list_date"]]
    dates = month_end_dates()
    print("months:", len(dates), dates[0], "..", dates[-1], flush=True)
    monthly = load_monthly_basic(dates)
    print("loaded monthly basic:", len(monthly), "/", len(dates), flush=True)
    roe_map = load_pit_roe()
    print("roe panel stocks:", len(roe_map), flush=True)

    port_hist = []
    for dt in dates:
        if dt not in monthly:
            continue
        df = monthly[dt].merge(info, left_on="ts_code", right_index=True, how="left")
        df["date"] = dt
        port = build_portfolio(df, None)
        if port.empty:
            continue
        port_hist.append((dt, port))

    # 计算月度收益（用下一调仓日 close；成本按组合换手 30bp 近似）
    rows = []
    prev = None
    nav_u = [1.0]
    nav_c = [1.0]
    for i, (dt, port) in enumerate(port_hist):
        nxt = port_hist[i + 1][0] if i + 1 < len(port_hist) else None
        if nxt is None or nxt not in monthly:
            break
        nxt_px = monthly[nxt].set_index("ts_code")["close"]
        r = (nxt_px.reindex(port["ts_code"]).values / port["close"].values) - 1.0
        r = np.nan_to_num(r, nan=0.0)
        ru = float(np.dot(port["w_unconstrained"].values, r))
        rc = float(np.dot(port["w_constrained"].values, r))
        # 成本：两次调仓之间换手（无约束=按收益漂移；近似用权重变化 0）
        nav_u.append(nav_u[-1] * (1 + ru - COST * 0.5))
        nav_c.append(nav_c[-1] * (1 + rc - COST * 0.5))
        rows.append({"date": dt, "next": nxt, "returns": ru, "ret_c": rc,
                     "trap_weight": float(port.loc[port["trap"], "w_unconstrained"].sum()),
                     "n": int(len(port))})
    nav_u = pd.Series(nav_u)
    nav_c = pd.Series(nav_c)
    mu, mc = metrics(nav_u), metrics(nav_c)
    comparison = {"unconstrained": mu, "constrained": mc}
    print("metrics:", json.dumps(comparison, ensure_ascii=False), flush=True)

    # 首月影子盘 = 最新调仓组合（约束后权重）
    last_dt, last_port = port_hist[-1]
    last_port = last_port.sort_values("w_constrained", ascending=False)
    SHADOW_DIR.mkdir(parents=True, exist_ok=True)
    shadow = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scheme": "H14-A: 质量Q5等权 + 陷阱组权重上限5%",
        "signal_date": last_dt,
        "trap_cap": TRAP_CAP, "cost": COST,
        "n_holdings": int(len(last_port)),
        "trap_weight_unconstrained": float(last_port.loc[last_port["trap"], "w_unconstrained"].sum()),
        "trap_weight_constrained": float(last_port.loc[last_port["trap"], "w_constrained"].sum()),
        "holdings": [
            {"ts_code": r.ts_code, "name": r.name, "roe": round(float(r.roe_pit), 3),
             "pe_ttm": round(float(r.pe_ttm), 2), "w_unconstrained": round(float(r.w_unconstrained), 5),
             "w_constrained": round(float(r.w_constrained), 5),
             "trap": bool(r.trap), "quality_roe_q": int(r.roe_q), "pe_t": int(r.pe_t)}
            for r in last_port.itertuples()
        ],
    }
    # 影子期文件按「运行月」命名（月度节点 11-01 生成 202611 期=11 月持仓，signal_date 为上一月末信号）
    shadow_file = SHADOW_DIR / ("shadow_%s.json" % datetime.now().strftime("%Y%m"))
    shadow_file.write_text(json.dumps(shadow, ensure_ascii=False, indent=1), encoding="utf-8")
    print("shadow:", shadow_file, "n=", shadow["n_holdings"],
          "trap_w_c=%.1f%%" % (shadow["trap_weight_constrained"] * 100), flush=True)
    # 自动刷新门户看板 JSON（失败不影响主流程）
    try:
        import subprocess as _sp
        _r = _sp.run([sys.executable, "-X", "utf8",
                      str(REPO / "tools" / "research" / "h14_shadow_status.py")],
                     capture_output=True, text=True, timeout=120)
        print("portal refresh:", (_r.stdout or _r.stderr).strip()[-160:], flush=True)
    except Exception as _e:  # noqa: BLE001
        print("portal refresh skipped:", type(_e).__name__, flush=True)

    # 滚动评估：用上一期 shadow 持仓计算已实现收益（约束 vs 无约束）
    prev_files = sorted(SHADOW_DIR.glob("shadow_*.json"))
    evals = []
    if len(prev_files) >= 2:
        prev = json.loads(prev_files[-2].read_text(encoding="utf-8"))
        prev_sig = prev.get("signal_date")
        cur = shadow
        cur_sig = cur.get("signal_date")
        if prev_sig and cur_sig and prev_sig in monthly and cur_sig in monthly:
            px0 = monthly[prev_sig].set_index("ts_code")["close"]
            px1 = monthly[cur_sig].set_index("ts_code")["close"]
            holds = prev.get("holdings") or []
            r = []
            wu = []
            wc = []
            for h in holds:
                code = h["ts_code"]
                if code in px0.index and code in px1.index and px0[code] and px1[code]:
                    ret = px1[code] / px0[code] - 1.0
                    r.append(ret)
                    wu.append(h["w_unconstrained"])
                    wc.append(h["w_constrained"])
            if r:
                import numpy as _np
                r = _np.array(r); wu = _np.array(wu); wc = _np.array(wc)
                ev = {"from": prev_sig, "to": cur_sig, "n": int(len(r)),
                      "ret_unconstrained": round(float(_np.dot(wu, r)) * 100, 3),
                      "ret_constrained": round(float(_np.dot(wc, r)) * 100, 3)}
                ev["diff_pct"] = round(ev["ret_constrained"] - ev["ret_unconstrained"], 3)
                evals.append(ev)
    if evals:
        evalf = SHADOW_DIR / "eval.jsonl"
        with evalf.open("a", encoding="utf-8") as f:
            for e in evals:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        print("rolling eval:", evals[-1], flush=True)

    # 报告
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = "20261030"
    js = OUT / ("H14_shadow_%s.json" % stamp)
    js.write_text(json.dumps({"comparison": comparison, "monthly": rows, "shadow": shadow},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    def fmt(x):
        return ("年化 %.1f%% / 波动 %.1f%% / 回撤 %.1f%% / 夏普 %.2f / 累计 %.1f%%" % (
            x["ann_return_pct"], x["vol_pct"], x["max_drawdown_pct"], x["sharpe"], x["total_return_pct"])) if x else "—"
    L = ["# H14 影子盘（A 方案）· 回测对比 + 首月初始化", "",
         "- 口径：质量 Q5（PIT ROE）等权组合；月度调仓；成本 30bp/次；约束组=高ROE+高PE 权重≤5%",
         "- 样本：%s ~ %s（%d 个调仓月）" % (dates[0], dates[-1], len(rows)), "",
         "## 回测对比（同池、同成本）", "| 组合 | 表现 |", "|---|---|",
         "| 无约束（质量 Q5 等权） | " + fmt(mu) + " |",
         "| 约束（陷阱组≤5%） | " + fmt(mc) + " |", "",
         "## 首月影子盘（信号日 %s）" % last_dt,
         "- 持仓 %d 只；陷阱组：约束后权重 **%.2f%%**（无约束 %.2f%%）" % (
             shadow["n_holdings"], shadow["trap_weight_constrained"] * 100,
             shadow["trap_weight_unconstrained"] * 100),
         "- 前 10 大持仓：" + "、".join("%s(%.2f%%)" % (r["name"], r["w_constrained"] * 100)
                                       for r in shadow["holdings"][:10]),
         "- 明细：`work/research/h14_shadow/shadow_%s.json`" % last_dt[:6], "",
         "## 滚动评估（上一期 → 本期）",
         ("- " + "；".join("%s→%s 约束 %.2f%% vs 无约束 %.2f%%（差 %+.2fpct）" % (
             e["from"], e["to"], e["ret_constrained"], e["ret_unconstrained"], e["diff_pct"])
             for e in (evals[-3:] if evals else []))) if evals else "- 首期，暂无滚动评估（下期起自动记录：work/research/h14_shadow/eval.jsonl）",
         "",
         "## 结论（初版）",
         "- 约束组回测：%s；相对无约束 %s" % (fmt(mc), "回撤/夏普对比见上表"),
         "- 影子盘已初始化，后续每月末自动生成新一期持仓（可在例会评估）。",
         "- 局限：ST 判定用当前名称（非历史 PIT）；成本用 30bp 近似；行业市值中性未在影子层再施加。"]
    md = OUT / ("H14_shadow_%s.md" % stamp)
    md.write_text("\n".join(L), encoding="utf-8")
    try:
        logf = REPO / "work" / "data_sync" / "h14_shadow.log"
        logf.parent.mkdir(parents=True, exist_ok=True)
        logf.open("a", encoding="utf-8").write(json.dumps({
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "signal_date": last_dt, "n": shadow["n_holdings"],
            "trap_c": shadow["trap_weight_constrained"], "cmp": comparison, "evals": evals,
        }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    print("report:", md, flush=True)
    return 0


roe_map: dict[str, list[tuple[str, float]]] = {}

if __name__ == "__main__":
    sys.exit(main())
