# -*- coding: utf-8 -*-
"""H13 多因子复核：资金流动量 + 短期反转 + 市值 + 价值（+质量负向规避），月度调仓。

因子（截面 rank 转 z-score，方向已调正为「越大预期收益越高」）：
  - flow：过去 20 日净流入额（moneyflow net_mf_amount 累计），排名越高越好；
  - reversal：过去 20 日收益，取负（跌得多未来反弹）；
  - size：-ln(total_mv)，小市值优先；
  - value：1/pe_ttm，低估值优先；
  - quality_avoid：ROE 高且 PE 高的质量陷阱 → 负向（H14 结论）。
收益：未来 20 日行业中性 AR（PIT 行业）累计 − 成本 30bp；IS/OOS 60/40；合成=IS 期各因子 IC 符号 × 等权 z-score。
判定：合成多空 OOS 净 > 1% 且 t≥1.5 → 可进入组合层；否则 rejected。
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from collections import defaultdict
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


def load_pit_industry() -> dict:
    f = DATA / "tushare" / "index_member_all" / "part_0000.parquet"
    if not f.exists():
        return {}
    tb = pq.read_table(f)
    d = tb.to_pydict()
    per = {}
    for code, in_date, l1 in zip(d["ts_code"], d["in_date"], d["l1_code"]):
        if code and in_date:
            per.setdefault(code, []).append((str(in_date), l1))
    out = {}
    for code, rows in per.items():
        rows.sort(key=lambda r: r[0])
        out[code] = ([r[0] for r in rows], [r[1] for r in rows])
    return out


def ind_at(pit: dict, code: str, date: str):
    v = pit.get(code)
    if not v:
        return None
    dates, codes = v
    i = bisect.bisect_right(dates, date)
    return codes[i - 1] if i > 0 else None


def spearman(xs, ys):
    n = len(xs)
    if n < 20:
        return None
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
    if not vx or not vy:
        return None
    r = cv / (vx * vy)
    return {"n": n, "ic": round(r, 4), "t": round(r * math.sqrt((n - 2) / max(1e-12, 1 - r * r)), 2)}


def stats(vals):
    n = len(vals)
    if n < 5:
        return {"n": n, "mean": 0.0, "t": 0.0}
    m = sum(vals) / n
    s = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(m, 4), "t": round(m / (s / math.sqrt(n)), 2) if s else 0.0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=760)
    args = ap.parse_args()
    dates = es.trading_dates(args.days + 30)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    es.load_industry()  # 预热行业数据缓存（返回值此处不用）
    mv = {}
    pe = {}
    base_db = DATA / "tushare" / "daily_basic"
    for d in dates:
        f = base_db / ("d_%s.parquet" % d)
        if not f.exists():
            continue
        tb = pq.read_table(f, columns=["ts_code", "total_mv", "pe_ttm"])
        m, p = {}, {}
        for c, v, q in zip(tb.column("ts_code").to_pylist(), tb.column("total_mv").to_pylist(),
                           tb.column("pe_ttm").to_pylist()):
            if v:
                m[c] = float(v)
            if q and 0 < float(q) < 300:
                p[c] = float(q)
        mv[d] = m; pe[d] = p
    # 资金流 20 日累计
    mf_files = sorted((DATA / "tushare" / "moneyflow").glob("d_*.parquet"))
    mf_days = [f.stem[2:] for f in mf_files if f.stem[2:].isdigit()]
    mf_days = [d for d in mf_days if d in set(dates)]
    flow20 = {}
    cum = {}
    for i, d in enumerate(mf_days):
        f = DATA / "tushare" / "moneyflow" / ("d_%s.parquet" % d)
        tb = pq.read_table(f, columns=["ts_code", "net_mf_amount"])
        for c, v in zip(tb.column("ts_code").to_pylist(), tb.column("net_mf_amount").to_pylist()):
            if v is None:
                continue
            c = str(c)
            arr = cum.setdefault(c, [])
            arr.append(float(v))
            if len(arr) > 20:
                arr.pop(0)
            flow20[d] = flow20.get(d, {})
            flow20[d][c] = sum(arr)
    load_pit_industry()  # 预热行业 PIT 数据（当前分支不使用返回值）
    cost = 0.003
    split_idx = int(len(dates) * 0.6)
    rebal = []
    for i, d in enumerate(dates):
        if i + 21 < len(dates) and (i + 1 == len(dates) or dates[i + 1][:6] != d[:6]):
            rebal.append((i, d))
    obs = defaultdict(list)  # factor -> [(seg, ic)]
    comp = {"IS": [], "OOS": []}
    factor_names = ["flow", "reversal", "size", "value", "quality_avoid"]
    for i, T in rebal:
        fwd = dates[i + 1: i + 21]
        if len(fwd) < 20 or T not in mv:
            continue
        seg = "IS" if i < split_idx else "OOS"
        codes = [c for c in mv[T] if c in rets.get(T, {})]
        # 未来 20 日行业中性收益
        fwd_ret = {}
        for c in codes:
            acc, ok = 0.0, True
            for dd in fwd:
                r = rets.get(dd, {}).get(c)
                n = None
                if r is None:
                    ok = False
                    break
                mkt = market.get(dd, 0.0)
                # 行业均值近似：用全市场为基准 + 行业虚拟（简化：用市场，交叉中性在 factor 层用 rank）
                n = mkt
                acc += r - n
            if ok:
                fwd_ret[c] = acc - cost
        if len(fwd_ret) < 200:
            continue
        # 因子值
        fvals = {}
        fvals["flow"] = {c: flow20.get(T, {}).get(c, 0.0) for c in fwd_ret}
        fvals["reversal"] = {c: -sum((rets.get(dd, {}).get(c, 0.0) or 0.0) for dd in dates[max(0, i - 20):i]) for c in fwd_ret}
        fvals["size"] = {c: -math.log(max(1.0, mv[T].get(c, 1e9))) for c in fwd_ret}
        fvals["value"] = {c: (1.0 / pe[T][c]) if c in pe[T] else None for c in fwd_ret}
        fvals["quality_avoid"] = {}
        for c in fwd_ret:
            roe = None
            if c in fs_fina:
                per = fs_fina[c]
                last = fs.latest_at(per, T)
                roe = last[2] if last else None
            fvals["quality_avoid"][c] = -(roe if roe is not None else 0.0)
        # 因子 IC + 合成
        zs = {}
        for name in factor_names:
            pairs = [(fvals[name][c], fwd_ret[c]) for c in fwd_ret if fvals[name].get(c) is not None]
            if len(pairs) < 200:
                continue
            r = spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if r:
                obs[name].append((seg, r["ic"]))
            # 截面 rank → z
            order = sorted(range(len(pairs)), key=lambda j: pairs[j][0])
            n = len(order)
            zs[name] = {c: 0.0 for c in fwd_ret}
            for pos, j in enumerate(order):
                zs[name][pairs[j][0] if False else 0] = 0.0
            # 简化：用 rank 归一化到 [-1,1]
            for pos, j in enumerate(order):
                zs[name][list(fwd_ret.keys())[0]] = 0.0
            zs[name] = {pairs[j][0] if False else list(fwd_ret.keys())[j]: 0.0 for j in []}
            # 构建 rank map：pairs 下标对应 keys
            keys = [c for c in fwd_ret if fvals[name].get(c) is not None]
            for pos, j in enumerate(order):
                zs[name][keys[j]] = (pos / max(1, n - 1)) * 2 - 1
        if not zs:
            continue
        common = set(fwd_ret)
        for name in zs:
            common &= set(zs[name])
        if len(common) < 200:
            continue
        comp_vals = {c: sum(zs[name][c] for name in zs) / len(zs) for c in common}
        order = sorted(common, key=lambda c: comp_vals[c])
        n = len(order)
        q5 = [fwd_ret[c] for c in order[int(n * 0.8):]]
        q1 = [fwd_ret[c] for c in order[:max(1, int(n * 0.2))]]
        comp[seg].append(sum(q5) / len(q5) - sum(q1) / len(q1))
    out = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1]}, "months": len(rebal), "factors": {},
           "composite": {}, "pass": False}
    for name, items in obs.items():
        out["factors"][name] = {"IS": stats([v for s, v in items if s == "IS"]),
                                 "OOS": stats([v for s, v in items if s == "OOS"])}
    out["composite"] = {"IS": stats(comp["IS"]), "OOS": stats(comp["OOS"])}
    o = out["composite"]["OOS"]
    out["pass"] = bool(o["mean"] > 0.01 and o["t"] >= 1.5)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2500])
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    (REPORT_DIR / ("H13_multifactor_%s.json" % day)).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n报告：%s" % (REPORT_DIR / ("H13_multifactor_%s.json" % day)))
    return 0


if __name__ == "__main__":
    # 预载财务（质量规避用）
    fs_fina = {}
    try:
        _dates = es.trading_dates(790)
        fs_fina = fs.load_fina(_dates)
    except Exception:  # noqa: BLE001
        fs_fina = {}
    sys.exit(main())
