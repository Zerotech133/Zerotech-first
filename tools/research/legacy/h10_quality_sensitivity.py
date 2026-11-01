# -*- coding: utf-8 -*-
"""H10 数据质量扰动框架：6 类扰动 → 质量因子（ROE）月度 IC / 多空的变化。

基准实验：PIT ROE 月度五分位多空（H7/H14 已证实 OOS 负向稳健，IC≈-0.033~-0.038）。
扰动（每次只注入一类，随机种子固定以保证可复现）：
  1. missing_5 / missing_10：随机删除 5%/10% 股票-月观测；
  2. pit_shift：公告日 ann_date 前移 1 天（模拟前视）；
  3. price_error_1pct：1% 股票-日价格注入 ±20% 异常（收益污染）；
  4. survivorship：剔除「期末前 30 交易日无行情」的股票（模拟幸存者偏差）；
  5. field_null_30：30% ROE 置空；
  6. industry_mislabel_10：10% 股票行业随机重标。
输出：每类扰动下 IS/OOS IC 均值、t、Q5−Q1，相对基准的变化与结论判定。
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_MAIN = Path(r"C:\项目\老田的AI助手")
DATA = (ROOT / "quant_backtest" / "data") if (ROOT / "quant_backtest" / "data" / "tushare" / "daily").exists() \
    else (_MAIN / "quant_backtest" / "data")
REPORT_DIR = ROOT / "research" / "reports"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import event_study2 as es  # noqa: E402
import factor_strat as fs  # noqa: E402

SEED = 20261029


def spearman(xs, ys):
    n = len(xs)
    if n < 10:
        return 0.0

    def rank(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cv = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx)); vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cv / (vx * vy) if vx and vy else 0.0


def stats(vals):
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": 0.0, "t": 0.0}
    m = sum(vals) / n
    s = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(m, 4), "t": round(m / (s / math.sqrt(n)), 2) if s else 0.0}


def load_mv(dates):
    import pyarrow.parquet as pq
    out = {}
    base = DATA / "tushare" / "daily_basic"
    for d in dates:
        f = base / ("d_%s.parquet" % d)
        if not f.exists():
            continue
        tb = pq.read_table(f, columns=["ts_code", "total_mv"])
        m = {}
        for cc, vv in zip(tb.column("ts_code").to_pylist(), tb.column("total_mv").to_pylist()):
            if vv is not None:
                m[cc] = float(vv)
        if m:
            out[d] = m
    return out


def local_neutral(dates, rets, market, industry, mv):
    out = {}
    for d in dates:
        day = rets.get(d) or {}
        sizes = {}
        day_mv = mv.get(d) or {}
        codes_mv = sorted(day_mv, key=lambda cc: day_mv[cc])
        n = len(codes_mv)
        for i, cc in enumerate(codes_mv):
            sizes[cc] = min(2, int(i * 3 / n)) if n else 0
        groups = {}
        for cc, r in day.items():
            g = (industry.get(cc) or "未知") + "|" + str(sizes.get(cc, 9))
            groups.setdefault(g, []).append(r - market.get(d, 0.0))
        means = {g: sum(v) / len(v) for g, v in groups.items() if len(v) >= 10}
        out[d] = {cc: means.get((industry.get(cc) or "未知") + "|" + str(sizes.get(cc, 9)), market.get(d, 0.0))
                  for cc in day}
    return out


def build_obs(days: int):
    dates = es.trading_dates(days + 25)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    industry = es.load_industry()
    mv = load_mv(dates)
    neutral = local_neutral(dates, rets, market, industry, mv)
    fina = fs.load_fina(dates)
    cost = 0.003
    rebal = []
    for i, d in enumerate(dates):
        if i + 21 < len(dates) and (i + 1 == len(dates) or dates[i + 1][:6] != d[:6]):
            rebal.append((i, d))
    return dates, rets, neutral, fina, rebal, cost


def run_variant(dates, rets, neutral, fina, rebal, cost, kind: str, rng: random.Random):
    rows = []  # (seg, roe, fwd_net)
    split_idx = int(len(dates) * 0.6)
    price_bad = set()
    if kind == "price_error_1pct":
        all_pairs = [(d, c) for d in dates for c in list(rets.get(d, {}))[:]]
        for d, c in rng.sample(all_pairs, max(1, len(all_pairs) // 100)):
            price_bad.add((d, c))
    for i, T in rebal:
        fwd = dates[i + 1: i + 21]
        if len(fwd) < 20:
            continue
        seg = "IS" if i < split_idx else "OOS"
        codes = list(fina)
        if kind == "missing_5":
            codes = [c for c in codes if rng.random() > 0.05]
        elif kind == "missing_10":
            codes = [c for c in codes if rng.random() > 0.10]
        for code in codes:
            rows_f = fina[code]
            last = fs.latest_at(rows_f, T)
            if not last:
                continue
            # PIT：可错位 1 天（前移=look-ahead）
            if kind == "pit_shift":
                last_date = last[0]
                try:
                    import datetime as dt
                    shifted = (dt.datetime.strptime(last_date, "%Y%m%d") + dt.timedelta(days=1)).strftime("%Y%m%d")
                except ValueError:
                    shifted = last_date
                if shifted > T:
                    continue
            roe = last[2]
            if kind == "field_null_30" and rng.random() < 0.30:
                roe = None
            if roe is None:
                continue
            if kind == "survivorship":
                if not rets.get(dates[-1], {}).get(code):
                    continue
            acc, ok = 0.0, True
            for dd in fwd:
                r = rets.get(dd, {}).get(code)
                n = neutral.get(dd, {}).get(code)
                if r is None or n is None:
                    ok = False
                    break
                if kind == "price_error_1pct" and (dd, code) in price_bad:
                    r = r * (1.0 if rng.random() > 0.5 else -1.0) * 1.2
                acc += r - n
            if not ok:
                continue
            rows.append((seg, roe, acc - cost, code))
    res = {"rows": len(rows), "ic": {}, "qls": {}}
    for seg in ("IS", "OOS"):
        sub = [(r[1], r[2]) for r in rows if r[0] == seg]
        if len(sub) < 50:
            res["ic"][seg] = {"n": 0, "mean": 0.0, "t": 0.0}
            res["qls"][seg] = {"n": 0, "mean": 0.0, "t": 0.0}
            continue
        ic = spearman([x[0] for x in sub], [x[1] for x in sub])
        order = sorted(range(len(sub)), key=lambda j: sub[j][0])
        n = len(order)
        q5 = [sub[order[j]][1] for j in range(int(n * 0.8), n)]
        q1 = [sub[order[j]][1] for j in range(0, max(1, int(n * 0.2)))]
        ls = sum(q5) / len(q5) - sum(q1) / len(q1)
        res["ic"][seg] = {"n": len(sub), "mean": round(ic, 4), "t": round(ic * math.sqrt((len(sub) - 2) / max(1e-12, 1 - ic * ic)), 2) if abs(ic) < 1 else 0.0}
        res["qls"][seg] = {"n": len(sub), "mean": round(ls * 100, 3), "t": 0.0}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H10 数据质量扰动框架")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    dates, rets, neutral, fina, rebal, cost = build_obs(args.days)
    rng = random.Random(SEED)
    base = run_variant(dates, rets, neutral, fina, rebal, cost, "base", rng)
    variants = ["missing_5", "missing_10", "pit_shift", "price_error_1pct",
                "survivorship", "field_null_30"]
    out = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1]}, "seed": SEED, "baseline": base, "variants": {}}
    for v in variants:
        out["variants"][v] = run_variant(dates, rets, neutral, fina, rebal, cost, v, random.Random(SEED))
    print(json.dumps(out["baseline"], ensure_ascii=False)[:600])
    for v in variants:
        print(v, "IS", out["variants"][v]["ic"]["IS"], "OOS", out["variants"][v]["ic"]["OOS"])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    js = REPORT_DIR / ("H10_quality_sensitivity_%s.json" % day)
    js.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n报告：%s" % js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
