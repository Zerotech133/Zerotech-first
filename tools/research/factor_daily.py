# -*- coding: utf-8 -*-
"""factor_daily.py — 因子挖掘师（FACTOR1/蒋琬）每日因子挖掘与策略优化轮。

职责（数据智能部，张松团队）：
  1. 每日计算候选因子在最近 12 个月的月频 IC/分层，监控衰减与符号翻转；
  2. 输出新候选假设（不直接入库）与衰减告警，供策略团队（司马微）复核；
  3. 落日报/台账/门户 JSON，作为 daily_work 的证据（file 新鲜度）。

用法：python tools/research/factor_daily.py [--months 12]
产出：
  research/reports/factor_daily_<yyyymmdd>.json/.md
  research/ledger/factor_daily.jsonl（每日一行）
  portal/static/factor_daily.json
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
LEDGER = REPO / "research" / "ledger"
STATIC = REPO / "portal" / "static"

sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))
from safe_json import SafeJsonError, backup_then_atomic_write  # noqa: E402,F401
# 已过四关/已证伪的已知因子（用于衰减监控）
KNOWN = {
    "vol_20": {"verdict": "四关 PASS（LS）", "dir": "低波", "expect_ic": -0.09},
    "mom_20": {"verdict": "四关 PASS（LS）/ long-only 弱", "dir": "短期反转", "expect_ic": -0.06},
    "mom_120": {"verdict": "四关 PASS（LS）/ S08 观察", "dir": "长期反转", "expect_ic": -0.06},
    "rev_5": {"verdict": "四关 FAIL", "dir": "5日反转", "expect_ic": 0.04},
}
CANDIDATE = {
    "liq_neg": {"desc": "低流动性（-log 20日均额）", "note": "新候选：未过四关"},
    "amihud": {"desc": "Amihud 非流动性（|ret|/成交额 20日均）", "note": "新候选：未过四关"},
}


def window_dates(start_years: int = 3) -> list[str]:
    fs = sorted(glob.glob(str(DATA / "daily" / "d_*.parquet")))
    ds = [Path(f).stem[2:] for f in fs]
    return ds[-260 * start_years:]


def load(dts: list[str]):
    adj_rows, amt_rows = [], []
    for dt in dts:
        d = pq.read_table(DATA / "daily" / f"d_{dt}.parquet", columns=["ts_code", "close", "amount"]).to_pandas()
        af = DATA / "adj_factor" / f"d_{dt}.parquet"
        if af.exists():
            a = pq.read_table(af, columns=["ts_code", "adj_factor"]).to_pandas()
            d = d.merge(a, on="ts_code", how="left")
            d["adj"] = d["close"] * d["adj_factor"].fillna(1.0)
        else:
            d["adj"] = d["close"]
        d["trade_date"] = dt
        adj_rows.append(d[["ts_code", "trade_date", "adj"]])
        amt_rows.append(d[["ts_code", "trade_date", "amount"]])
        if len(adj_rows) >= 300:
            ca, cm = pd.concat(adj_rows, ignore_index=True), pd.concat(amt_rows, ignore_index=True)
            adj_rows, amt_rows = [], []
            adj = ca.pivot_table(index="trade_date", columns="ts_code", values="adj")
            amt = cm.pivot_table(index="trade_date", columns="ts_code", values="amount")
            if "adj_acc" not in locals():
                adj_acc = adj
            else:
                adj_acc = pd.concat([adj_acc, adj], axis=0)
            if "amt_acc" not in locals():
                amt_acc = amt
            else:
                amt_acc = pd.concat([amt_acc, amt], axis=0)
    if adj_rows:
        ca, cm = pd.concat(adj_rows, ignore_index=True), pd.concat(amt_rows, ignore_index=True)
        adj = ca.pivot_table(index="trade_date", columns="ts_code", values="adj")
        amt = cm.pivot_table(index="trade_date", columns="ts_code", values="amount")
        if "adj_acc" not in locals():
            adj_acc = adj
        else:
            adj_acc = pd.concat([adj_acc, adj], axis=0)
        if "amt_acc" not in locals():
            amt_acc = amt
        else:
            amt_acc = pd.concat([amt_acc, amt], axis=0)
    adj_acc.index = [str(x) for x in adj_acc.index]
    amt_acc.index = [str(x) for x in amt_acc.index]
    return adj_acc.sort_index(), amt_acc.sort_index()


def export_payload(payload: dict, static_dir: Path | str | None = None) -> dict:
    """门户静态 JSON：写前备份 + 原子写 + 写后校验（safe_json）；返回报告。"""
    target = Path(static_dir or STATIC) / "factor_daily.json"
    return backup_then_atomic_write(target, payload)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="因子每日挖掘/衰减监控")
    ap.add_argument("--months", type=int, default=12, help="回看月数（完整月，默认 12）")
    args = ap.parse_args(argv)
    if args.months < 2:
        ap.error("--months 至少为 2")
    years = max(3, (args.months + 11) // 12 + 1)
    dts = window_dates(years)
    adj, amt = load(dts)
    sb = pq.read_table(sorted(glob.glob(str(DATA / "stock_basic" / "*.parquet")))[-1]).to_pandas().set_index("ts_code")
    names = sb["name"]
    def listed_at(dt_):
        return listed_days(sb, dt_)
    s = pd.Series(adj.index.tolist())
    mends = [g.iloc[-1] for _, g in s.groupby(s.str[:6])]
    # 最新月若尚未走完，不能把 5 日收益混进 12 个完整月；先剔除，再向前取所需端点。
    if len(mends) >= 2 and len(adj.loc[mends[-2]:mends[-1]]) < 15:
        mends = mends[:-1]
    months = mends[-(args.months + 1):]  # N 个完整月 → N+1 个月末端点
    if len(months) < args.months + 1:
        print(json.dumps({"error": f"完整月不足：需要 {args.months + 1} 个端点，实际 {len(months)}"}, ensure_ascii=False))
        return 2

    per_factor = {}
    for k, f in enumerate(months[:-1]):
        dt, nxt = f, months[k + 1]
        hist = adj.loc[:dt]
        px = hist.iloc[-1]
        fwd = adj.loc[nxt] / px - 1.0
        uni = (~names.reindex(px.index).fillna("").str.contains(r"ST|\*ST|退", regex=True)) & \
              (listed_at(dt).reindex(px.index).fillna(0) >= 250) & (px >= 2) & px.notna()
        cc = px.index[uni.fillna(False)]
        if len(cc) < 300:
            continue
        amt20 = amt.loc[:dt].iloc[-20:].mean().reindex(cc)
        hist.pct_change().iloc[-1].reindex(cc)
        # 口径与 mining/四关一致（不带方向翻转，expect_ic 符号才可比）：
        # vol_20=20日波动(期望负IC)；mom_20/mom_120=历史收益(期望负IC=反转)；rev_5=-5日收益(期望正IC)
        factors = {
            "vol_20": hist.tail(21).pct_change().std().reindex(cc),
            "mom_20": (px / hist.iloc[-21] - 1).reindex(cc),
            "mom_120": (px / hist.iloc[-121] - 1).reindex(cc),
            "rev_5": -(px / hist.iloc[-6] - 1).reindex(cc),
            "liq_neg": -np.log(amt20.clip(lower=1)),
            "amihud": hist.tail(20).pct_change().abs().mean().reindex(cc) / amt20.clip(lower=1),
        }
        for key, x in factors.items():
            d = pd.DataFrame({"x": x, "y": fwd.reindex(cc)}).dropna()
            if len(d) < 200:
                continue
            ic = float(d["x"].corr(d["y"], method="spearman"))
            per_factor.setdefault(key, []).append({"date": nxt, "ic": round(ic, 4), "n": int(len(d))})
    # 汇总
    rows = []
    for key, arr in per_factor.items():
        ics = np.array([a["ic"] for a in arr])
        if len(ics) < 6:
            continue
        t = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if ics.std(ddof=1) > 0 else 0.0
        last3 = float(ics[-3:].mean())
        prev3 = float(ics[-6:-3].mean()) if len(ics) >= 6 else float("nan")
        flip = bool(np.sign(last3) != np.sign(prev3) and abs(last3 - prev3) > 0.05) if not np.isnan(prev3) else False
        rows.append({"factor": key, "ic_mean12": round(float(ics.mean()), 4), "t": round(t, 2),
                     "ic_pos_rate": round(float((ics > 0).mean()) * 100, 1),
                     "last3_mean": round(last3, 4), "prev3_mean": round(prev3, 4) if not np.isnan(prev3) else None,
                     "sign_flip": flip, "months": int(len(ics)),
                     "type": "known" if key in KNOWN else "candidate_new",
                     "verdict": KNOWN.get(key, {}).get("verdict", CANDIDATE.get(key, {}).get("note", ""))})
    rows.sort(key=lambda r: -abs(r["ic_mean12"]))
    # 告警/提案
    decay = [r for r in rows if r["sign_flip"] or (r["type"] == "known" and abs(r["last3_mean"]) < 0.02)]
    proposals = [r for r in rows if r["type"] == "candidate_new" and abs(r["ic_mean12"]) >= 0.03 and abs(r["t"]) >= 2]
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "latest_date": adj.index[-1], "window": f"{months[0]} .. {months[-1]}", "months": len(months) - 1,
        "owner": "FACTOR1/蒋琬（数据智能部·张松团队）",
        "factors": rows,
        "decay_alerts": decay,
        "proposals": [{"factor": r["factor"], "ic_mean12": r["ic_mean12"], "t": r["t"],
                        "desc": CANDIDATE.get(r["factor"], {}).get("desc", ""),
                        "action": "提议入 hypotheses.db（in_progress）→ 四关"} for r in proposals],
        "note": "每日挖掘；只出假设/告警，不直接改策略库；候选须走四关+复核",
    }
    OUT.mkdir(parents=True, exist_ok=True); LEDGER.mkdir(parents=True, exist_ok=True); STATIC.mkdir(parents=True, exist_ok=True)
    tag = datetime.now().strftime("%Y%m%d")
    (OUT / f"factor_daily_{tag}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# 因子日报 · {payload['latest_date']}（{payload['window']}）", "",
          f"> 负责人：{payload['owner']}｜挖掘轮，非验证；只出提案/告警", "",
          "| 因子 | 类型 | IC均值12m | t | IC>0% | 近3m均值 | 符号翻转 | 结论 |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['factor']} | {r['type']} | {r['ic_mean12']} | {r['t']} | {r['ic_pos_rate']}% | {r['last3_mean']} | {'⚠️' if r['sign_flip'] else ''} | {r['verdict']} |")
    md += ["", "## 衰减告警"] + ([f"- {r['factor']}：last3={r['last3_mean']} / prev3={r['prev3_mean']}（翻转={'是' if r['sign_flip'] else '否'}）" for r in decay] or ["- 无"])
    md += ["", "## 新候选提案（待四关）"] + ([f"- {p['factor']}（{p['desc']}）：IC12={p['ic_mean12']} t={p['t']}" for p in payload["proposals"]] or ["- 无"])
    (OUT / f"factor_daily_{tag}.md").write_text("\n".join(md), encoding="utf-8")
    with (LEDGER / "factor_daily.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": payload["latest_date"], "generated_at": payload["generated_at"],
                            "top": rows[:3], "decay": [r["factor"] for r in decay],
                            "proposals": [p["factor"] for p in payload["proposals"]]}, ensure_ascii=False) + "\n")
    export_payload(payload)          # 备份+原子写+写后校验（safe_json）
    print(json.dumps({"latest": payload["latest_date"], "factors": len(rows),
                      "top3": [(r["factor"], r["ic_mean12"], r["t"]) for r in rows[:3]],
                      "decay": [r["factor"] for r in decay],
                      "proposals": [p["factor"] for p in payload["proposals"]]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
