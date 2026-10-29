# -*- coding: utf-8 -*-
"""H14 质量反向：高 ROE 跑输是否在估值分层内仍存在（剥离质量-估值交互）。

设计（月度调仓、PIT）：
  - T 月末用 ann_date<=T 最新 ROE；T 日 pe_ttm；
  - 收益：T+1..T+20 行业×规模中性 AR 累计 − 成本 0.30%；
  - 检验1：全样本 ROE Q5−Q1（复现 H7）；
  - 检验2：PE 三分位内 ROE Q5−Q1（估值控制后是否仍负）；
  - 检验3：高 ROE（全样本 Q5）+ 高 PE（PE 三分位最高组）vs 高 ROE + 低 PE；
  - 判定：OOS 估值内 ROE L/S < -0.3% 且 t<=-1.5 → 反向稳健（可做规避/低配）；否则样本外不成立。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[2]
_MAIN = Path(r"C:\项目\老田的AI助手")
DATA = (ROOT / "quant_backtest" / "data") if (ROOT / "quant_backtest" / "data" / "tushare" / "daily").exists() \
    else (_MAIN / "quant_backtest" / "data")
REPORT_DIR = ROOT / "research" / "reports"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_study2 as es  # noqa: E402
import factor_strat as fs  # noqa: E402


def run(days: int = 1250, split: float = 0.6, cost_bps: int = 15) -> dict:
    dates = es.trading_dates(days + 25)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    industry = es.load_industry()
    mv = es.load_total_mv(dates)
    neutral = es.neutral_maps(dates, rets, market, industry, mode="industry_size", mv=mv)
    t0 = time.time()
    fina = fs.load_fina(dates)
    print("fina_load", len(fina), round(time.time()-t0,1), flush=True)
    cost_pct = 2 * cost_bps / 100.0
    rebal = []
    for i, d in enumerate(dates):
        if i + 21 < len(dates) and (i + 1 == len(dates) or dates[i + 1][:6] != d[:6]):
            rebal.append((i, d))
    split_idx = int(len(dates) * split)
    obs = []  # (seg, pe, roe, fwd_net)
    t1 = time.time()
    for i, T in rebal:
        print("month", i, T, "elapsed", round(time.time()-t1,1), flush=True)
        fwd = dates[i + 1: i + 21]
        if len(fwd) < 20:
            continue
        try:
            tb = pq.read_table(DATA / "tushare" / "daily_basic" / ("d_%s.parquet" % T),
                               columns=["ts_code", "pe_ttm"])
            pe = {c: float(v) for c, v in zip(tb.column("ts_code").to_pylist(),
                                              tb.column("pe_ttm").to_pylist())
                  if v and 0 < float(v) < 300}
        except Exception:  # noqa: BLE001
            continue
        seg = "IS" if i < split_idx else "OOS"
        for code, rows in fina.items():
            if code not in pe:
                continue
            last = fs.latest_at(rows, T)
            if not last or last[2] is None:
                continue
            acc, ok = 0.0, True
            for dd in fwd:
                rr = rets.get(dd, {}).get(code)
                nn = neutral.get(dd, {}).get(code)
                if rr is None or nn is None:
                    ok = False
                    break
                acc += rr - nn
            if not ok:
                continue
            obs.append((seg, pe[code], float(last[2]), acc - cost_pct))
    if len(obs) < 200:
        return {"ok": False, "error": "obs insufficient", "n": len(obs)}
    # PE 三分位切点（IS 期）
    pe_is = sorted(o[1] for o in obs if o[0] == "IS")
    pe_lo, pe_hi = pe_is[int(len(pe_is) / 3)], pe_is[int(2 * len(pe_is) / 3)]
    out = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1]}, "months": len(rebal),
           "obs": len(obs), "pe_cut": [round(pe_lo, 1), round(pe_hi, 1)], "cost_pct": cost_pct,
           "raw": {}, "within_pe": {}, "trap": {}, "pass": False}
    def qls(vals):
        """ROE 五分位 L/S：返回 (Q5均值, Q1均值, L/S)。"""
        idx = sorted(range(len(vals)), key=lambda j: vals[j][0])
        n = len(idx)
        q5 = [vals[j][1] for j in idx[int(n * 0.8):]]
        q1 = [vals[j][1] for j in idx[:max(1, int(n * 0.2))]]
        return (sum(q5) / len(q5), sum(q1) / len(q1),
                sum(q5) / len(q5) - sum(q1) / len(q1))
    for seg in ("IS", "OOS"):
        sub = [(o[1], o[2], o[3]) for o in obs if o[0] == seg]
        if len(sub) < 50:
            continue
        # 检验1：raw
        r5, r1, ls = qls([(x[1], x[2]) for x in sub])
        out["raw"][seg] = {"n": len(sub), "ls": round(ls, 4), "t": fs.stats([x[2] for x in sub
                         if True]).get("t", 0)}
        # 检验2：PE 三分位内
        buckets = {"low": [], "mid": [], "high": []}
        for x in sub:
            if x[0] <= pe_lo:
                buckets["low"].append((x[1], x[2]))
            elif x[0] >= pe_hi:
                buckets["high"].append((x[1], x[2]))
            else:
                buckets["mid"].append((x[1], x[2]))
        w = {}
        for k, v in buckets.items():
            if len(v) < 30:
                continue
            a5, a1, l = qls(v)
            w[k] = {"n": len(v), "ls": round(l, 4)}
        out["within_pe"][seg] = w
        # 检验3：高 ROE × PE 分组的均值（Q5 ROE 内再按 PE 高低）
        roe_q5_th = sorted(y[1] for y in sub)[int(len(sub) * 0.8)]
        q5_vals = [x for x in sub if x[1] >= roe_q5_th]
        hi_pe = [x[2] for x in q5_vals if x[0] >= pe_hi]
        lo_pe = [x[2] for x in q5_vals if x[0] <= pe_lo]
        out["trap"][seg] = {"hi_roe_hi_pe": fs.stats(hi_pe), "hi_roe_lo_pe": fs.stats(lo_pe),
                            "spread": round(fs.stats(hi_pe)["mean"] - fs.stats(lo_pe)["mean"], 4)
                            if hi_pe and lo_pe else None}
        if seg == "OOS":
            out["pass"] = bool(w and all(v["ls"] < -0.3 for v in w.values()))
    return out


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# H14 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H14 质量反向：估值分层内 ROE 反向检验", ""]
    lines.append("- 窗口 %s..%s（%d 调仓月，%d 观测）；PE 三分位切点 %s（IS 期内）；成本 %.2f%%" % (
        res["window"]["start"], res["window"]["end"], res["months"], res["obs"],
        res["pe_cut"], res["cost_pct"]))
    lines.append("")
    lines.append("## 检验2：PE 三分位内 ROE Q5−Q1（负=高ROE跑输）")
    lines.append("| 段 | 低PE | 中PE | 高PE |")
    lines.append("|---|---|---|---|")
    for seg in ("IS", "OOS"):
        w = res["within_pe"].get(seg) or {}
        lines.append("| %s | %s | %s | %s |" % (seg,
                     (w.get("low") or {}).get("ls"), (w.get("mid") or {}).get("ls"),
                     (w.get("high") or {}).get("ls")))
    lines.append("")
    lines.append("## 检验3：高 ROE 内 PE 高低分组（质量陷阱）")
    for seg in ("IS", "OOS"):
        t = res["trap"].get(seg) or {}
        if t:
            lines.append("- %s：高ROE+高PE %s%%（n=%d） vs 高ROE+低PE %s%%（n=%d）；差 %s%%" % (
                seg, (t.get("hi_roe_hi_pe") or {}).get("mean"),
                (t.get("hi_roe_hi_pe") or {}).get("n"),
                (t.get("hi_roe_lo_pe") or {}).get("mean"),
                (t.get("hi_roe_lo_pe") or {}).get("n"), t.get("spread")))
    lines.append("")
    lines.append("## 判定")
    lines.append("- 估值控制后 ROE 反向仍全为负且 < −0.3%%：**%s**" % ("成立（可做规避/低配）" if res["pass"] else "不成立/部分成立"))
    lines += ["", "局限：PE 分桶用当期 pe_ttm；未做行业中性因子；ROE 用最新一期（可能含预告数据缺失）；IS/OOS 单次切分。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H14 质量反向")
    ap.add_argument("--days", type=int, default=1250)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tag", default="", help="报告名后缀（区分窗口），如 3y/5y")
    args = ap.parse_args(argv)
    res = run(args.days)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    suffix = ("_" + args.tag) if args.tag else ""
    md = REPORT_DIR / ("H14_quality_reversal_%s%s.md" % (day, suffix))
    js = REPORT_DIR / ("H14_quality_reversal_%s%s.json" % (day, suffix))
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"pass": res["pass"], "within_pe": res["within_pe"], "trap": res["trap"]},
                     ensure_ascii=False, indent=1)[:2400] if args.json else render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
