# -*- coding: utf-8 -*-
"""H2 v2：真实盈余惊喜 → PEAD（公告后 5/10/20 日漂移）。

数据：tushare forecast_vip 3 年（type + p_change_min/max）、daily、stock_basic/daily_basic。
方法：
  - 惊喜 = 预告类型方向 × p_change 中值（百分点）；类型：预增/略增/扭亏=正，预减/略减/首亏/续亏=负；
  - 事件日 = 公告后下一交易日（盘后公告假设）；
  - 事件后 CAR[h] = Σ_{k=1..h} (个股收益 − 行业×市值组均值) − 双边成本 0.30%；
  - 分组阈值：仅用 IS 期（前 60% 交易日）的惊喜三分位切点，应用到 OOS（无前视）；
  - 判定：OOS 高惊喜组 CAR>0.3% 且 t≥1.5，且 高−低 多空差>0（成本后）。
退出码 0=ok / 2=数据不足。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from bisect import bisect_right
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

POS_TYPES = {"预增", "略增", "扭亏", "续盈"}
NEG_TYPES = {"预减", "略减", "首亏", "续亏"}


def load_forecast() -> list[dict]:
    rows = []
    for f in sorted((DATA / "tushare" / "forecast").glob("part_*.parquet")):
        tb = pq.read_table(f, columns=["ts_code", "ann_date", "end_date", "type",
                                       "p_change_min", "p_change_max", "summary"])
        cols = {c: tb.column(c).to_pylist() for c in tb.column_names}
        for i in range(tb.num_rows):
            typ = (cols["type"][i] or "").strip()
            if typ not in POS_TYPES | NEG_TYPES:
                continue
            lo, hi = cols["p_change_min"][i], cols["p_change_max"][i]
            vals = [v for v in (lo, hi) if v is not None]
            mid = sum(vals) / len(vals) if vals else None
            if mid is None:
                sign = 1.0 if typ in POS_TYPES else -1.0
                surprise = 10.0 * sign
            else:
                sign = 1.0 if typ in POS_TYPES else -1.0
                surprise = sign * abs(mid)
            rows.append({"ts_code": cols["ts_code"][i], "ann_date": str(cols["ann_date"][i]),
                         "type": typ, "surprise": surprise})
    return rows


def terciles(vals: list[float]) -> tuple[float, float]:
    s = sorted(vals)
    return s[int(len(s) / 3)], s[int(2 * len(s) / 3)]


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": round(sum(vals) / n, 4) if n else 0.0, "t": 0.0, "win_rate": 0.0}
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(mean, 4), "t": round(mean / (std / math.sqrt(n)), 2) if std else 0.0,
            "win_rate": round(sum(1 for v in vals if v > 0) / n, 4)}


def run(days: int = 760, split: float = 0.6, cost_bps: int = 15) -> dict:
    dates = es.trading_dates(days + 25)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    if len(dates) < 100:
        return {"ok": False, "error": "dates insufficient"}
    industry = es.load_industry()
    mv = es.load_total_mv(dates)
    neutral = es.neutral_maps(dates, rets, market, industry, mode="industry_size", mv=mv)
    split_idx = int(len(dates) * split)
    cost_pct = 2 * cost_bps / 100.0
    events = load_forecast()
    rows = []
    for ev in events:
        ad = ev["ann_date"]
        ymd = ad.replace("-", "")
        idx = bisect_right(dates, ymd)
        if idx <= 0 or idx + 20 >= len(dates):
            continue
        ts = ev["ts_code"]
        ars, ok = [], True
        for k in range(0, 21):
            d = dates[idx + k]
            r = rets.get(d, {}).get(ts)
            nm = neutral.get(d, {}).get(ts)
            if r is None or nm is None:
                ok = False
                break
            ars.append(r - nm)
        if not ok:
            continue
        seg = "IS" if idx < split_idx else "OOS"
        car = {h: sum(ars[1:h + 1]) - cost_pct for h in (5, 10, 20)}
        rows.append({"seg": seg, "surprise": ev["surprise"], "type": ev["type"], "car": car,
                     "ar0": ars[0]})
    if len(rows) < 100:
        return {"ok": False, "error": "events insufficient", "n": len(rows)}
    is_sur = [r["surprise"] for r in rows if r["seg"] == "IS"]
    lo_th, hi_th = terciles(is_sur)
    out = {"ok": True,
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1], "trading_days": len(dates),
                      "split_date": dates[split_idx]},
           "events": len(rows), "events_is": len(is_sur), "events_oos": len(rows) - len(is_sur),
           "cost_pct": cost_pct, "thresholds": {"lo": round(lo_th, 2), "hi": round(hi_th, 2)},
           "by_horizon": {}, "by_type": {}, "sample_types": {}}
    for r in rows:
        out["sample_types"][r["type"]] = out["sample_types"].get(r["type"], 0) + 1
    # 分组（IS 阈值应用到 OOS）
    for h in (5, 10, 20):
        res = {}
        for seg in ("IS", "OOS"):
            seg_rows = [r for r in rows if r["seg"] == seg]
            top = [r["car"][h] for r in seg_rows if r["surprise"] >= hi_th]
            bot = [r["car"][h] for r in seg_rows if r["surprise"] <= lo_th]
            res[seg] = {"high": stats(top), "low": stats(bot),
                        "ls": round(stats(top)["mean"] - stats(bot)["mean"], 4)
                        if top and bot else None}
        o = res["OOS"]
        passed = bool(o["high"]["n"] >= 30 and o["high"]["mean"] > 0.3 and o["high"]["t"] >= 1.5
                      and o["ls"] is not None and o["ls"] > 0)
        res["pass"] = passed
        out["by_horizon"][str(h)] = res
    # 按类型
    for typ in sorted(out["sample_types"]):
        sub = [r for r in rows if r["type"] == typ]
        out["by_type"][typ] = {"n": len(sub),
                               "is": stats([r["car"][10] for r in sub if r["seg"] == "IS"]),
                               "oos": stats([r["car"][10] for r in sub if r["seg"] == "OOS"])}
    return out


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# H2 v2 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H2 v2 PEAD：真实盈余惊喜（forecast type × p_change）", ""]
    lines.append("- 生成：%s；窗口 %s..%s（%d 交易日；IS 截止 %s）" % (
        res["generated_at"], res["window"]["start"], res["window"]["end"],
        res["window"]["trading_days"], res["window"]["split_date"]))
    lines.append("- 事件：%d 条（IS %d / OOS %d）；惊喜三分位切点（IS）：低≤%.1f / 高≥%.1f；成本 %.2f%%" % (
        res["events"], res["events_is"], res["events_oos"],
        res["thresholds"]["lo"], res["thresholds"]["hi"], res["cost_pct"]))
    lines.append("")
    lines.append("| 持有期 | OOS 高惊喜组 n | OOS 高净CAR | OOS t | OOS 低组净CAR | 多空差 | 判定 |")
    lines.append("|---|---|---|---|---|---|---|")
    for h in ("5", "10", "20"):
        o = res["by_horizon"][h]["OOS"]
        lines.append("| %s 日 | %s | %s%% | %s | %s%% | %s%% | %s |" % (
            h, o["high"]["n"], o["high"]["mean"], o["high"]["t"], o["low"]["mean"],
            o["ls"], "✅" if res["by_horizon"][h]["pass"] else "❌"))
    lines.append("")
    lines.append("## 按预告类型（10 日净 CAR）")
    lines.append("| 类型 | n | IS | OOS |")
    lines.append("|---|---|---|---|")
    for typ, v in res["by_type"].items():
        lines.append("| %s | %d | %s%% | %s%% |" % (typ, v["n"], v["is"]["mean"], v["oos"]["mean"]))
    lines += ["", "## 口径与局限",
              "1. 惊喜 = 预告类型方向 × p_change 中值；无分析师一致预期，用「盈余变化幅度」代理；",
              "2. 三分位切点仅用 IS 期，避免前视；",
              "3. 事件后 CAR 排除 AR0，减少公告日反转干扰；",
              "4. 未处理停牌/涨跌停可交易性；行业映射用当前 stock_basic.industry。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H2 v2 PEAD（真实盈余惊喜）")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.days)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md = REPORT_DIR / ("H2v2_earnings_surprise_pead_%s.md" % day)
    js = REPORT_DIR / ("H2v2_earnings_surprise_pead_%s.json" % day)
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({"events": res["events"], "by_horizon": res["by_horizon"]},
                         ensure_ascii=False, indent=1)[:3000])
    else:
        print(render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
