# -*- coding: utf-8 -*-
"""H4 资金流背离：moneyflow 净流入 × 当日涨跌 → 未来 5/10 日行业×规模中性收益。

假设：高净流入但当日下跌（背离）→ 未来反转上涨；高净流出但当日上涨 → 未来下跌。
三关：样本内/外 60/40、行业×规模双中性、双边 15bps。

用法：
  python tools/research/flow_divergence.py --days 760
  python tools/research/flow_divergence.py --json
退出码：0=ok / 2=数据不足。
"""
from __future__ import annotations

import argparse
import json
import math
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


def load_moneyflow(dates: list[str]) -> dict:
    """{date: {code: net_mf_amount}}（单位：万元，tushare moneyflow）。"""
    import pyarrow.parquet as pq
    base = DATA / "tushare" / "moneyflow"
    out = {}
    for d in dates:
        f = base / ("d_%s.parquet" % d)
        try:
            tb = pq.read_table(f, columns=["ts_code", "net_mf_amount"])
        except Exception:  # noqa: BLE001
            continue
        m = {}
        for c, v in zip(tb.column("ts_code").to_pylist(), tb.column("net_mf_amount").to_pylist()):
            if v is not None:
                m[c] = float(v)
        if m:
            out[d] = m
    return out


def _ranks(vals: dict) -> dict:
    """截面百分位排名（0~1）。"""
    codes = sorted(vals, key=lambda c: vals[c])
    n = len(codes)
    if n < 2:
        return {}
    return {c: i / (n - 1) for i, c in enumerate(codes)}


def _stats(vals: list[float]) -> dict:
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": round(sum(vals) / n, 4) if n else 0, "t": 0}
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(mean, 4), "win_rate": round(sum(1 for v in vals if v > 0) / n, 4),
            "t": round(mean / (std / math.sqrt(n)), 2) if std > 0 else 0}


def run(days: int = 760, horizon: int = 5, split: float = 0.6, cost_bps: int = 15) -> dict:
    dates = es.trading_dates(days + horizon + 3)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    if len(dates) < 80:
        return {"ok": False, "error": "dates insufficient"}
    industry = es.load_industry()
    mv = es.load_total_mv(dates)
    neutral = es.neutral_maps(dates, rets, market, industry, mode="industry_size", mv=mv)
    mf = load_moneyflow(dates)
    cost_pct = 2 * cost_bps / 100.0
    split_idx = int(len(dates) * split)
    # 每日截面：净流入分位 × 当日收益符号 → 未来 h 日中性累计收益
    obs = {"all": [], "inflow_down": [], "outflow_up": [], "inflow_all": [], "outflow_all": []}
    ics = {"IS": [], "OOS": []}
    for i in range(0, len(dates) - horizon - 1):
        d = dates[i]
        day_mf = mf.get(d) or {}
        day_ret = rets.get(d) or {}
        if len(day_mf) < 100:
            continue
        ranks = _ranks({c: v for c, v in day_mf.items() if c in day_ret})
        if not ranks:
            continue
        seg = "IS" if i < split_idx else "OOS"
        xs, ys = [], []
        for code, rk in ranks.items():
            r = day_ret[code]
            fwd = 0.0
            ok = True
            for k in range(1, horizon + 1):
                dd = dates[i + k]
                rr = rets.get(dd, {}).get(code)
                nn = neutral.get(dd, {}).get(code)
                if rr is None or nn is None:
                    ok = False
                    break
                fwd += rr - nn
            if not ok:
                continue
            fwd_net = fwd - cost_pct
            xs.append(rk)
            ys.append(fwd_net)
            obs["all"].append((seg, fwd_net))
            if rk >= 0.8:
                obs["inflow_all"].append((seg, fwd_net))
                if r < -2.0:
                    obs["inflow_down"].append((seg, fwd_net))
            elif rk <= 0.2:
                obs["outflow_all"].append((seg, fwd_net))
                if r > 2.0:
                    obs["outflow_up"].append((seg, fwd_net))
        if len(xs) > 30:
            mx = sum(xs) / len(xs)
            my = sum(ys) / len(ys)
            cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
            vx = math.sqrt(sum((a - mx) ** 2 for a in xs))
            vy = math.sqrt(sum((b - my) ** 2 for b in ys))
            if vx > 0 and vy > 0:
                ics[seg].append(cov / (vx * vy))
    res = {"ok": True, "window": {"start": dates[0], "end": dates[-1], "trading_days": len(dates),
                                 "split_date": dates[split_idx]},
           "horizon": horizon, "cost_bps_per_side": cost_bps, "cost_pct": cost_pct,
           "neutral": "industry_size",
           "groups": {}, "ic": {}, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    for name, items in obs.items():
        is_v = [v for seg, v in items if seg == "IS"]
        oos_v = [v for seg, v in items if seg == "OOS"]
        si, so = _stats(is_v), _stats(oos_v)
        res["groups"][name] = {"is": si, "oos": so}
    for seg in ("IS", "OOS"):
        vals = ics[seg]
        res["ic"][seg] = _stats(vals)
    # 判定：背离组（高流入+跌）OOS 净收益 >0.3% 且 t>=1.5
    idg = res["groups"]["inflow_down"]["oos"]
    res["pass"] = bool(idg.get("n", 0) >= 30 and idg.get("mean", -9) > 0.3 and idg.get("t", 0) >= 1.5)
    return res


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H4 资金流背离（moneyflow × 价格，行业+规模中性）", ""]
    lines.append("- 生成：%s；窗口 %s..%s（%d 交易日；IS 截止 %s）" % (
        res["generated_at"], res["window"]["start"], res["window"]["end"],
        res["window"]["trading_days"], res["window"]["split_date"]))
    lines.append("- 未来收益：%d 日；成本双边 %d bps（%.2f 百分点）" % (
        res["horizon"], res["cost_bps_per_side"], res["cost_pct"]))
    lines.append("")
    lines.append("| 组 | IS n | IS 净均值 | OOS n | OOS 净均值 | OOS t |")
    lines.append("|---|---|---|---|---|---|")
    names = {"all": "全样本", "inflow_all": "高净流入组(q≥0.8)", "outflow_all": "高净流出组(q≤0.2)",
             "inflow_down": "背离：高流入+当日跌>2%", "outflow_up": "背离：高流出+当日涨>2%"}
    for k, label in names.items():
        g = res["groups"][k]
        lines.append("| %s | %s | %s%% | %s | %s%% | %s |" % (
            label, g["is"]["n"], g["is"].get("mean"), g["oos"]["n"], g["oos"].get("mean"),
            g["oos"].get("t")))
    lines.append("")
    lines.append("## 截面 IC（净流入分位 vs 未来净收益）")
    for seg in ("IS", "OOS"):
        s = res["ic"][seg]
        lines.append("- %s：mean IC=%s，n(days)=%s，t=%s" % (seg, s.get("mean"), s.get("n"), s.get("t")))
    lines.append("")
    lines.append("## 判定")
    lines.append("- 背离组（高流入+跌）OOS 净收益 = %s%%，t=%s → **%s**" % (
        res["groups"]["inflow_down"]["oos"].get("mean"),
        res["groups"]["inflow_down"]["oos"].get("t"),
        "通过" if res["pass"] else "未通过"))
    lines += ["", "## 局限",
              "1. net_mf_amount 用 tushare 原始口径（未按成交额归一；用截面分位缓解）；",
              "2. 行业映射用当前 stock_basic.industry；",
              "3. 未考虑涨跌停/停牌可交易性；",
              "4. 样本外只切一次（60/40）。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H4 资金流背离")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.days, args.horizon)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md = REPORT_DIR / ("H4_flow_divergence_%s.md" % day)
    js = REPORT_DIR / ("H4_flow_divergence_%s.json" % day)
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({"ok": True, "groups": res["groups"], "ic": res["ic"], "pass": res["pass"]},
                         ensure_ascii=False, indent=1))
    else:
        print(render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
