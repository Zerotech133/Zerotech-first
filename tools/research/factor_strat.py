# -*- coding: utf-8 -*-
"""H7 财务因子分层：PIT 财务指标（价值/质量/成长）月度 IC 与五分位多空。

数据：tushare fina_indicator（按股票文件，ann_date 时点）+ daily_basic（估值）+ daily（收益）。
方法：
  - 月末 T 取 T 前已公告（ann_date<=T）最新一期指标；
  - 因子：value=1/pe_ttm；quality=roe；growth=netprofit_yoy；
  - 收益：T+1..T+20 行业×规模中性 AR 累计 − 成本 0.30%；
  - 月度截面 Spearman IC（IS/OOS 60/40）+ 五分位多空（Q5−Q1）。
判定：任一因子 OOS IC>0.02 且 t>2，或 OOS Q5−Q1 净>0.3% 且 t≥1.5。
"""
from __future__ import annotations

import argparse
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

FACTORS = {"value": "1/pe_ttm", "quality": "roe", "growth": "netprofit_yoy"}


def load_fina(dates: list[str]) -> dict:
    """{code: [(ann_date, end_date, roe, netprofit_yoy)]}（仅需要的股票数据）。"""
    out = {}
    base = DATA / "tushare" / "fina_indicator"
    for f in base.glob("*.parquet"):
        try:
            tb = pq.read_table(f, columns=["ts_code", "ann_date", "end_date", "roe", "netprofit_yoy"])
        except Exception:  # noqa: BLE001
            continue
        if tb.num_rows == 0:
            continue
        code = tb.column("ts_code")[0].as_py() or f.stem.replace("_", ".")
        rows = []
        for ad, ed, roe, yoy in zip(tb.column("ann_date").to_pylist(), tb.column("end_date").to_pylist(),
                                    tb.column("roe").to_pylist(), tb.column("netprofit_yoy").to_pylist()):
            if ad:
                rows.append((str(ad), str(ed or ""), roe, yoy))
        if rows:
            rows.sort(key=lambda r: r[0])
            out[code if code.endswith(("SH", "SZ", "BJ")) else f.stem] = rows
    return out


def latest_at(rows: list[tuple], date: str) -> tuple | None:
    latest = None
    for r in rows:
        if r[0] <= date:
            latest = r
        else:
            break
    return latest


def spearman(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 10:
        return 0.0

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else 0.0


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": round(sum(vals) / n, 4) if n else 0.0, "t": 0.0}
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(mean, 4), "t": round(mean / (std / math.sqrt(n)), 2) if std else 0.0}


def run(days: int = 760, split: float = 0.6, cost_bps: int = 15) -> dict:
    dates = es.trading_dates(days + 25)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    if len(dates) < 100:
        return {"ok": False, "error": "dates insufficient"}
    industry = es.load_industry()
    mv = es.load_total_mv(dates)
    neutral = es.neutral_maps(dates, rets, market, industry, mode="industry_size", mv=mv)
    fina = load_fina(dates)
    cost_pct = 2 * cost_bps / 100.0
    # 月末调仓日（每月最后一个交易日）
    rebal = []
    for i, d in enumerate(dates):
        if i + 21 < len(dates) and (i + 1 == len(dates) or dates[i + 1][:6] != d[:6]):
            rebal.append((i, d))
    split_idx = int(len(dates) * split)
    ic = {k: {"IS": [], "OOS": []} for k in FACTORS}
    q_ret = {k: {"IS": {q: [] for q in "12345"}, "OOS": {q: [] for q in "12345"}} for k in FACTORS}
    ls_series = {k: {"IS": [], "OOS": []} for k in FACTORS}
    for i, T in rebal:
        fwd = dates[i + 1: i + 21]
        if len(fwd) < 20:
            continue
        seg = "IS" if i < split_idx else "OOS"
        vals = {k: {} for k in FACTORS}
        fwd_ret = {}
        for code, rows in fina.items():
            last = latest_at(rows, T)
            if not last:
                continue
            r = rets.get(T, {}).get(code)
            if r is None:
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
            fwd_ret[code] = acc
            if last[2] is not None:
                vals["quality"][code] = last[2]  # roe
            if last[3] is not None:
                vals["growth"][code] = last[3]
        # 价值因子：T 日 daily_basic pe_ttm
        db = DATA / "tushare" / "daily_basic" / ("d_%s.parquet" % T)
        try:
            tb = pq.read_table(db, columns=["ts_code", "pe_ttm"])
            for c, v in zip(tb.column("ts_code").to_pylist(), tb.column("pe_ttm").to_pylist()):
                if v and float(v) > 0 and c in fwd_ret:
                    vals["value"][c] = 1.0 / float(v)
        except Exception as exc:  # noqa: BLE001
            print(f"[factor_strat] value 因子在 {T} 不可用: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
        codes = [c for c in fwd_ret if c in vals["value"] or c in vals["quality"] or c in vals["growth"]]
        if len(codes) < 50:
            continue
        for k, m in vals.items():
            sub = [(c, m[c]) for c in codes if c in m and fwd_ret.get(c) is not None]
            if len(sub) < 50:
                continue
            xs = [v for _, v in sub]
            ys = [fwd_ret[c] for c, _ in sub]
            ic[k][seg].append(spearman(xs, ys))
            order = sorted(range(len(sub)), key=lambda j: xs[j])
            q_means = {}
            for q in range(5):
                lo = int(len(order) * q / 5)
                hi = int(len(order) * (q + 1) / 5)
                vals = [ys[j] for j in order[lo:hi]]
                q_ret[k][seg][str(q + 1)].extend(vals)
                if vals:
                    q_means[str(q + 1)] = sum(vals) / len(vals)
            if "5" in q_means and "1" in q_means:
                ls_series[k][seg].append(q_means["5"] - q_means["1"])
    res = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1], "trading_days": len(dates)},
           "rebalance_months": len(rebal), "cost_pct": cost_pct, "factors": {},
           "pass": False}
    for k in FACTORS:
        r = {"ic": {}, "q5_minus_q1": {}}
        for seg in ("IS", "OOS"):
            s = stats(ic[k][seg])
            r["ic"][seg] = {"n_months": len(ic[k][seg]), "mean": s["mean"], "t": s["t"]}
            for q in "12345":
                r["q5_minus_q1"].setdefault(seg, {})["q" + q] = stats(q_ret[k][seg][q])["mean"]
            ls_stat = stats(ls_series[k][seg])
            ls = ls_stat["mean"]
            ls_t = ls_stat["t"]
            r["q5_minus_q1"][seg]["ls"] = round(ls, 4)
            r["q5_minus_q1"][seg]["ls_net"] = round(ls - cost_pct, 4)
            r["q5_minus_q1"][seg]["ls_t"] = ls_t
        # 成本只在组合层扣一次；此前从每只股票扣成本会被多空相减抵消。
        oos_ls_net = r["q5_minus_q1"]["OOS"]["ls_net"]
        oos_ls_t = r["q5_minus_q1"]["OOS"]["ls_t"]
        passes = (r["ic"]["OOS"]["mean"] > 0.02 and r["ic"]["OOS"]["t"] > 2) or \
                 (oos_ls_net > 0.3 and oos_ls_t > 1.5)
        r["pass"] = passes
        res["pass"] = res["pass"] or passes
        res["factors"][k] = r
    return res


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# H7 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H7 财务因子分层（PIT，月度调仓）", ""]
    lines.append("- 窗口 %s..%s（%d 交易日，%d 个调仓月）；成本 %.2f%%" % (
        res["window"]["start"], res["window"]["end"], res["window"]["trading_days"],
        res["rebalance_months"], res["cost_pct"]))
    lines.append("")
    lines.append("| 因子 | IS IC | IS t | OOS IC | OOS t | IS Q5−Q1(毛) | OOS Q5−Q1(毛) | OOS 净(30bp) | OOS LS t | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for k, v in res["factors"].items():
        lines.append("| %s | %s | %s | %s | %s | %s%% | %s%% | %s%% | %s | %s |" % (
            k, v["ic"]["IS"]["mean"], v["ic"]["IS"]["t"], v["ic"]["OOS"]["mean"],
            v["ic"]["OOS"]["t"], v["q5_minus_q1"]["IS"]["ls"], v["q5_minus_q1"]["OOS"]["ls"],
            v["q5_minus_q1"]["OOS"].get("ls_net"), v["q5_minus_q1"]["OOS"].get("ls_t"),
            "✅" if v["pass"] else "❌"))
    lines += ["", "## 口径",
              "- 时点：月末 T 取 ann_date≤T 的最新一期财务指标（PIT）；",
              "- 收益：T+1..T+20 行业×规模中性 AR 累计；Q5−Q1 毛值，净收益在组合层扣双边成本 30bp；",
              "- 判定：IC 分支看 OOS IC/t；多空分支看 OOS 净 Q5−Q1>0.3% 且 LS 月度 t>1.5（不再用 IC 的 t 代替 LS t）；",
              "- 因子：价值=1/pe_ttm，质量=roe，成长=netprofit_yoy；",
              "- 局限：未做行业中性化因子（收益已中性）、未剔除 ST/停牌；IS/OOS 仅一次切分。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H7 财务因子分层")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.days)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md = REPORT_DIR / ("H7_factor_strat_%sv_5y_%s.md" % (day, day))
    js = REPORT_DIR / ("H7_factor_strat_%sv_5y_%s.json" % (day, day))
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({k: {"ic_oos": v["ic"]["OOS"], "ls_oos": v["q5_minus_q1"]["OOS"]["ls"],
                              "pass": v["pass"]} for k, v in res["factors"].items()},
                         ensure_ascii=False, indent=1))
    else:
        print(render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
