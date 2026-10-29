# -*- coding: utf-8 -*-
"""H8 行业资金轮动（PIT 行业版）：行业净流入周度 z-score → 未来 20 日行业超额收益。

改进点（相对 2026-10-29 可用性检查）：
  - 行业归属用 tushare index_member_all（SW 一级，含 in_date，PIT），不再用当前 stock_basic 快照；
  - 收益为行业等权「相对全市场」超额（去市场 beta），周度信号、未来 20 交易日；
  - 报告截面 IC（周）、五分位多空（成本后）、OOS 60/40。
判定：OOS IC 显著（|t|>2 且 |IC|>0.02）或 OOS Q5−Q1 净>0.5% 且 t≥1.5 → 可立项；否则 rejected。
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
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


def load_pit_industry() -> dict:
    """{ts_code: (sorted in_dates, l1_codes)}。"""
    f = DATA / "tushare" / "index_member_all" / "part_0000.parquet"
    tb = pq.read_table(f)
    d = tb.to_pydict()
    per = {}
    for code, in_date, l1 in zip(d["ts_code"], d["in_date"], d["l1_code"]):
        if not code or not in_date:
            continue
        per.setdefault(code, []).append((str(in_date), l1))
    out = {}
    for code, rows in per.items():
        rows.sort(key=lambda r: r[0])
        out[code] = ([r[0] for r in rows], [r[1] for r in rows])
    return out


def ind_at(pit: dict, code: str, date: str) -> str | None:
    v = pit.get(code)
    if not v:
        return None
    dates, codes = v
    i = bisect.bisect_right(dates, date)
    return codes[i - 1] if i > 0 else None


def spearman(xs, ys) -> float:
    n = len(xs)
    if n < 8:
        return 0.0

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx)); vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else 0.0


def stats(vals):
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": 0.0, "t": 0.0, "win_rate": 0.0}
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(mean, 4), "t": round(mean / (std / math.sqrt(n)), 2) if std else 0.0,
            "win_rate": round(sum(1 for v in vals if v > 0) / n, 4)}


def run(days: int = 760, split: float = 0.6, cost_bps: int = 15) -> dict:
    dates = es.trading_dates(days + 30)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    if len(dates) < 120:
        return {"ok": False, "error": "dates insufficient"}
    pit = load_pit_industry()
    cost_pct = 2 * cost_bps / 100.0
    split_idx = int(len(dates) * split)
    # 周度（5 交易日）信号
    ic_list, rows_qls = [], {}
    weeks = 0
    ind_hist: dict[str, list[float]] = {}
    for wi in range(0, len(dates) - 26, 5):
        wk = dates[wi:wi + 5]
        fwd = dates[wi + 5: wi + 25]
        if len(wk) < 5 or len(fwd) < 20:
            continue
        # 行业净流入（按 PIT 归属）
        flow = {}
        for d in wk:
            f = DATA / "tushare" / "moneyflow" / ("d_%s.parquet" % d)
            if not f.exists():
                continue
            tb = pq.read_table(f, columns=["ts_code", "net_mf_amount"])
            for code, v in zip(tb.column("ts_code").to_pylist(), tb.column("net_mf_amount").to_pylist()):
                if v is None:
                    continue
                ind = ind_at(pit, code, d)
                if ind:
                    flow[ind] = flow.get(ind, 0.0) + float(v)
        if len(flow) < 10:
            continue
        # 行业未来 20 日超额收益（等权，去市场）
        fwd_ret: dict[str, list[float]] = {}
        for d in fwd:
            day = rets.get(d) or {}
            m = market.get(d, 0.0)
            for code, r in day.items():
                ind = ind_at(pit, code, d)
                if ind:
                    fwd_ret.setdefault(ind, []).append(r - m)
        inds = [i for i in flow if len(fwd_ret.get(i, [])) >= 5]
        if len(inds) < 10:
            continue
        xs, ys = [], []
        for i in inds:
            hist = ind_hist.setdefault(i, [])
            hist.append(flow[i])
            if len(hist) < 4:
                continue
            mu = sum(hist) / len(hist)
            sd = math.sqrt(sum((h - mu) ** 2 for h in hist) / max(1, len(hist) - 1))
            if sd <= 0:
                continue
            z = (sum(hist[-4:]) / 4 - mu) / sd
            fr = sum(fwd_ret[i]) / len(fwd_ret[i])
            xs.append((z, i)); ys.append(fr)
        if len(xs) >= 10:
            ic = spearman([x[0] for x in xs], ys)
            seg = "IS" if wi < split_idx else "OOS"
            ic_list.append((seg, ic))
            weeks += 1
            # 五分位 L/S（按 z 排序，成本后）
            order = sorted(range(len(xs)), key=lambda j: xs[j][0])
            def qmean(lo, hi):
                sel = [ys[order[j]] for j in range(int(len(order) * lo), int(len(order) * hi))]
                return (sum(sel) / len(sel) - cost_pct) if sel else None
            q5 = qmean(0.8, 1.0); q1 = qmean(0.0, 0.2)
            if q5 is not None and q1 is not None:
                rows_qls.setdefault(seg, []).append(q5 - q1)
    if weeks < 10:
        return {"ok": False, "error": "weeks insufficient", "weeks": weeks}
    out = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1]}, "weeks": weeks,
           "cost_pct": cost_pct, "ic": {}, "qls": {}, "pass": False}
    for seg in ("IS", "OOS"):
        vals = [v for s, v in ic_list if s == seg]
        out["ic"][seg] = stats(vals)
        out["qls"][seg] = stats(rows_qls.get(seg, []))
    o = out["ic"]["OOS"]
    lsc = out["qls"]["OOS"]
    out["pass"] = bool((abs(o["mean"]) > 0.02 and abs(o["t"]) > 2)
                       or (lsc["mean"] > 0.5 and lsc["t"] >= 1.5))
    return out


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# H8 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H8 行业资金轮动（PIT 行业归属，周度信号）", ""]
    lines.append("- 窗口 %s..%s；可评分周数 %d；成本 %.2f%%（五分位）" % (
        res["window"]["start"], res["window"]["end"], res["weeks"], res["cost_pct"]))
    lines.append("")
    lines.append("| 段 | IC n 周 | IC 均值 | IC t | Q5−Q1 n | Q5−Q1 净均值 | t |")
    lines.append("|---|---|---|---|---|---|---|")
    for seg in ("IS", "OOS"):
        ic, q = res["ic"][seg], res["qls"][seg]
        lines.append("| %s | %s | %s | %s | %s | %s%% | %s |" % (
            seg, ic["n"], ic["mean"], ic["t"], q["n"], q["mean"], q["t"]))
    lines.append("")
    lines.append("## 判定：%s" % ("通过（可立项）" if res["pass"] else "未通过（不建议立项）"))
    lines += ["", "局限：①行业净流入按个股 net_mf_amount 加总，未按市值归一；②行业成分 PIT 用 index_member_all(in_date)，"
              "无 out_date 时以最新 in_date 归属；③未考虑行业 ETF 资金流；④IS/OOS 单次切分。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H8 行业资金轮动（PIT）")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.days)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md = REPORT_DIR / ("H8_industry_rotation_pit_%s.md" % day)
    js = REPORT_DIR / ("H8_industry_rotation_pit_%s.json" % day)
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:1800] if args.json else render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
