# -*- coding: utf-8 -*-
"""H3 龙虎榜机构席位跟单：机构净买入 → 未来 5/10 日收益（行业×规模中性 + OOS + 成本）。

数据：tushare top_list（上榜）+ top_inst（席位明细，机构专用=机构席位）3 年。
信号：当日该股「机构席位净买入额 = Σ买 − Σ卖」>0（净买）/ <0（净卖）/ 无机构席位。
收益：t+1..t+h 行业×规模中性 AR 累计 − 双边成本 0.30%。
判定：OOS 机构净买组 >0.3% 且 t≥1.5 且 优于净卖组。
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

INST_KEY = "机构专用"


def load_inst_net(dates: list[str]) -> dict:
    """{date: {ts_code: {"net": 机构净买入(万元), "n": 机构席位数}}}"""
    out = {}
    for d in dates:
        f = DATA / "tushare" / "top_inst" / ("d_%s.parquet" % d)
        if not f.exists():
            continue
        try:
            tb = pq.read_table(f, columns=["ts_code", "exalter", "side", "buy", "sell"])
        except Exception:  # noqa: BLE001
            continue
        per = {}
        for code, seat, side, buy, sell in zip(tb.column("ts_code").to_pylist(),
                                               tb.column("exalter").to_pylist(),
                                               tb.column("side").to_pylist(),
                                               tb.column("buy").to_pylist(),
                                               tb.column("sell").to_pylist()):
            if not seat or INST_KEY not in str(seat):
                continue
            v = per.setdefault(code, {"net": 0.0, "n": 0})
            v["net"] += (float(buy or 0) - float(sell or 0))
            v["n"] += 1
        if per:
            out[d] = per
    return out


def load_listed(dates: list[str]) -> dict:
    """{date: set(ts_code)} 上榜股票。"""
    out = {}
    for d in dates:
        f = DATA / "tushare" / "top_list" / ("d_%s.parquet" % d)
        if not f.exists():
            continue
        try:
            tb = pq.read_table(f, columns=["ts_code"])
        except Exception:  # noqa: BLE001
            continue
        out[d] = set(tb.column("ts_code").to_pylist())
    return out


def _stats(vals: list[float]) -> dict:
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": round(sum(vals) / n, 4) if n else 0.0, "t": 0.0}
    mean = sum(vals) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return {"n": n, "mean": round(mean, 4), "t": round(mean / (std / math.sqrt(n)), 2) if std else 0.0}


def run(days: int = 760, split: float = 0.6, cost_bps: int = 15) -> dict:
    dates = es.trading_dates(days + 15)
    rets, market = es.load_returns(dates)
    dates = [d for d in dates if d in rets]
    if len(dates) < 100:
        return {"ok": False, "error": "dates insufficient"}
    industry = es.load_industry()
    mv = es.load_total_mv(dates)
    neutral = es.neutral_maps(dates, rets, market, industry, mode="industry_size", mv=mv)
    split_idx = int(len(dates) * split)
    cost_pct = 2 * cost_bps / 100.0
    inst = load_inst_net(dates)
    listed = load_listed(dates)
    groups = {"inst_buy": [], "inst_sell": [], "inst_any": [], "no_inst": [], "listed_all": []}
    for i, d in enumerate(dates):
        lst = listed.get(d) or set()
        if not lst:
            continue
        day_inst = inst.get(d) or {}
        seg = "IS" if i < split_idx else "OOS"
        for code in lst:
            if code not in rets.get(d, {}):
                continue
            ars, ok = [], True
            for k in range(1, 11):
                if i + k >= len(dates):
                    ok = False
                    break
                dd = dates[i + k]
                r = rets.get(dd, {}).get(code)
                nm = neutral.get(dd, {}).get(code)
                if r is None or nm is None:
                    ok = False
                    break
                ars.append(r - nm)
            if not ok or len(ars) < 10:
                continue
            fwd5 = sum(ars[:5]) - cost_pct
            fwd10 = sum(ars) - cost_pct
            rec = (seg, code, fwd5, fwd10)
            groups["listed_all"].append(rec)
            info = day_inst.get(code)
            if not info:
                groups["no_inst"].append(rec)
                continue
            groups["inst_any"].append(rec)
            if info["net"] > 0:
                groups["inst_buy"].append(rec)
            elif info["net"] < 0:
                groups["inst_sell"].append(rec)
    res = {"ok": True, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "window": {"start": dates[0], "end": dates[-1], "trading_days": len(dates),
                      "split_date": dates[split_idx]},
           "cost_pct": cost_pct, "horizons": {}, "counts": {}}
    for g, recs in groups.items():
        res["counts"][g] = len(recs)
        res["horizons"][g] = {}
        for h, idx in (("5", 2), ("10", 3)):
            for seg in ("IS", "OOS"):
                vals = [r[idx] for r in recs if r[0] == seg]
                res["horizons"][g][seg + h] = _stats(vals)
    o = res["horizons"]
    res["ls_oos_5"] = round(o["inst_buy"]["OOS5"]["mean"] - o["inst_sell"]["OOS5"]["mean"], 4)
    b = o["inst_buy"]["OOS5"]
    res["pass"] = bool(b["n"] >= 100 and b["mean"] > 0.3 and b["t"] >= 1.5 and res["ls_oos_5"] > 0)
    return res


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# H3 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H3 龙虎榜机构席位跟单（3 年）", ""]
    lines.append("- 生成：%s；窗口 %s..%s（%d 交易日；IS 截止 %s）；成本 %.2f%%" % (
        res["generated_at"], res["window"]["start"], res["window"]["end"],
        res["window"]["trading_days"], res["window"]["split_date"], res["cost_pct"]))
    lines.append("- 组样本量：" + ", ".join("%s=%d" % (k, v) for k, v in res["counts"].items()))
    lines.append("")
    lines.append("| 组 | OOS5 n | OOS5 净均值 | OOS5 t | OOS10 n | OOS10 净均值 | OOS10 t |")
    lines.append("|---|---|---|---|---|---|---|")
    names = {"inst_buy": "机构净买入", "inst_sell": "机构净卖出", "inst_any": "有机构席位(合计)",
             "no_inst": "上榜无机构席位", "listed_all": "全部上榜"}
    for g, label in names.items():
        a, b = res["horizons"][g]["OOS5"], res["horizons"][g]["OOS10"]
        lines.append("| %s | %s | %s%% | %s | %s | %s%% | %s |" % (
            label, a["n"], a["mean"], a["t"], b["n"], b["mean"], b["t"]))
    lines.append("")
    lines.append("## 判定")
    o = res["horizons"]
    lines.append("- 机构净买 OOS5 净 = %s%%（t=%s）；净卖 = %s%%；多空差 = %s%% → **%s**" % (
        o["inst_buy"]["OOS5"]["mean"], o["inst_buy"]["OOS5"]["t"],
        o["inst_sell"]["OOS5"]["mean"], res["ls_oos_5"], "通过" if res["pass"] else "未通过"))
    lines += ["", "## 局限",
              "1. top_inst 中「机构专用」判定按席位名关键词，未区分机构类型；",
              "2. 信号=当日收盘可知（龙虎榜盘后发布）→ 次日开盘执行，假设无冲击成本；",
              "3. 未剔除涨跌停不可买；行业映射用当前分类；样本外只切一次。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="H3 龙虎榜机构席位")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.days)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md = REPORT_DIR / ("H3_dragon_tiger_inst_%s.md" % day)
    js = REPORT_DIR / ("H3_dragon_tiger_inst_%s.json" % day)
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({"counts": res["counts"],
                          "inst_buy_oos5": res["horizons"]["inst_buy"]["OOS5"],
                          "inst_sell_oos5": res["horizons"]["inst_sell"]["OOS5"],
                          "ls_oos_5": res["ls_oos_5"], "pass": res["pass"]},
                         ensure_ascii=False, indent=1))
    else:
        print(render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
