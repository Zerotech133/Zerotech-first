# -*- coding: utf-8 -*-
"""H1 公告事件研究：公告披露 → 次日/5 日市场调整超额（CAR）。

方法（第一版，简单可复现）：
- 事件日：公告 notice_date 之后的**下一个交易日**（晚间披露→次日交易）；
- 异常收益：个股 pct_chg − 当日全市场等权平均 pct_chg（市场调整法）；
- CAR[0,1] / CAR[0,5]：事件日起 2/6 个交易日累计；
- 样本：最近 N 个交易日窗口内、有股票映射的公告，按标题关键词分类；
- 报告：按类别统计 n / 均值 / 中位数 / 胜率 / t 值（n<30 标样本不足）；
- 局限：未扣成本、未做样本外、未控制行业/市值/beta——只作第一版探索性证据。

用法：
  python tools/research/event_study.py --days 90
  python tools/research/event_study.py --days 120 --horizon 5 --json
退出码：0=成功 / 2=数据缺失。
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_MAIN = Path(r"C:\项目\老田的AI助手")
DATA = (ROOT / "quant_backtest" / "data") if (ROOT / "quant_backtest" / "data" / "tushare" / "daily").exists() else (_MAIN / "quant_backtest" / "data")
ANN_DB = (ROOT / "zerobound-kg" / "data" / "ann_history" / "ann_history.db")
if not ANN_DB.exists():
    ANN_DB = _MAIN / "quant_backtest" / "data" / ".." / ".." / "zerobound-kg" / "data" / "ann_history" / "ann_history.db"
if not ANN_DB.exists():
    ANN_DB = Path(r"C:\项目\zerobound-kg\data\ann_history\ann_history.db")
DAILY_DIR = DATA / "tushare" / "daily"
CATALOG = DATA / "catalog.json"
REPORT_DIR = ROOT / "research" / "reports"

# 标题 → 类别（顺序即优先级；先匹配先用）
CATEGORY_RULES = [
    ("业绩预告", ("业绩预告", "业绩预增", "业绩预减", "预盈", "预亏", "业绩快报", "业绩预披露")),
    ("增减持", ("减持", "增持")),
    ("回购", ("回购",)),
    ("重组并购", ("重组", "并购", "收购", "重大资产", "要约")),
    ("融资/定增", ("非公开发行", "定增", "可转债", "配股", "发行股份", "向特定对象")),
    ("重大合同/中标", ("中标", "重大合同", "框架协议", "签订")),
    ("分红/权益", ("权益分派", "分红", "利润分配", "派息")),
    ("股权激励", ("股权激励", "员工持股", "限制性股票")),
    ("诉讼/处罚/风险", ("诉讼", "仲裁", "处罚", "立案", "警示", "退市", "风险提示", "问询")),
    ("担保", ("担保",)),
    ("澄清/异动", ("澄清", "异常波动", "异动")),
    ("停复牌", ("停牌", "复牌")),
    ("高管变动", ("辞职", "聘任", "换届", "董事长", "总经理", "董事")),
    ("对外投资", ("对外投资", "设立子公司", "增资")),
]


def classify_title(title: str) -> str:
    t = title or ""
    for cat, kws in CATEGORY_RULES:
        if any(k in t for k in kws):
            return cat
    return "其他"


def trading_dates(last_n: int) -> list[str]:
    files = sorted(DAILY_DIR.glob("d_*.parquet"))
    dates = [f.stem[2:] for f in files if f.stem[2:].isdigit()]
    return dates[-last_n:] if len(dates) > last_n else dates


def load_returns(dates: list[str]) -> tuple[dict, dict]:
    """返回 (per_date {ts_code: pct_chg}, market_mean {date: mean})。"""
    import pyarrow.parquet as pq
    per_date: dict[str, dict] = {}
    market: dict[str, float] = {}
    for d in dates:
        f = DAILY_DIR / ("d_%s.parquet" % d)
        try:
            tb = pq.read_table(f, columns=["ts_code", "pct_chg"])
        except Exception:  # noqa: BLE001
            continue
        codes = tb.column("ts_code").to_pylist()
        vals = tb.column("pct_chg").to_pylist()
        m = {}
        clean = []
        for c, v in zip(codes, vals):
            if v is None:
                continue
            m[c] = float(v)
            clean.append(float(v))
        per_date[d] = m
        market[d] = sum(clean) / len(clean) if clean else 0.0
    return per_date, market


def code_to_ts(code: str) -> str:
    c = str(code).zfill(6)
    if c[:3] in ("600", "601", "603", "605", "688", "689", "900"):
        return c + ".SH"
    if c[:3] in ("000", "001", "002", "003", "300", "301"):
        return c + ".SZ"
    if c[0] in ("4", "8", "9"):
        return c + ".BJ"
    return c + ".SZ"


def load_events(start_yyyymmdd: str, end_yyyymmdd: str) -> list[dict]:
    con = sqlite3.connect("file:%s?mode=ro" % str(ANN_DB).replace("\\", "/"), uri=True, timeout=30)
    sql = """SELECT a.art_code, a.notice_date, a.title, a.columns_txt, s.stock_code
             FROM ann a JOIN ann_stock s ON a.art_code = s.art_code
             WHERE a.notice_date BETWEEN ? AND ?"""
    buy = (start_yyyymmdd[:4] + "-" + start_yyyymmdd[4:6] + "-" + start_yyyymmdd[6:],
           end_yyyymmdd[:4] + "-" + end_yyyymmdd[4:6] + "-" + end_yyyymmdd[6:])
    rows = con.execute(sql, buy).fetchall()
    con.close()
    return [{"art_code": r[0], "notice_date": r[1], "title": r[2] or "",
             "columns_txt": r[3] or "", "stock_code": r[4]} for r in rows]


def compute_event_car(event_idx: int, ts_code: str, dates: list[str], rets: dict,
                      market: dict, horizon: int = 5) -> dict | None:
    """计算事件 AR/CAR；数据不全返回 None。"""
    ars = []
    for k in range(horizon + 1):
        i = event_idx + k
        if i >= len(dates):
            return None
        d = dates[i]
        r = rets.get(d, {}).get(ts_code)
        if r is None:
            return None
        ars.append(r - market.get(d, 0.0))
    car1 = sum(ars[:2])
    car5 = sum(ars[: min(horizon, 5) + 1])
    return {"ar0": ars[0], "car1": car1, "car5": car5, "ars": ars}


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    t = mean / (std / math.sqrt(n)) if n > 1 and std > 0 else 0.0
    srt = sorted(vals)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
    win = sum(1 for v in vals if v > 0) / n
    return {"n": n, "mean": round(mean, 4), "median": round(med, 4),
            "win_rate": round(win, 4), "t": round(t, 2), "std": round(std, 4)}


def run(days: int = 90, horizon: int = 5, min_n: int = 30) -> dict:
    all_dates = trading_dates(days + horizon + 2)
    if len(all_dates) < days:
        return {"ok": False, "error": "trading dates insufficient"}
    start, end = all_dates[0], all_dates[-1]
    print("加载行情 %d 个交易日（%s..%s）..." % (len(all_dates), start, end))
    rets, market = load_returns(all_dates)
    # 只保留有效交易日（文件存在）
    dates = [d for d in all_dates if d in rets]
    print("加载公告事件...")
    events = load_events(start, end)
    print("事件候选 %d 条" % len(events))
    cat_rows: dict[str, list[float]] = {}
    cat_rows5: dict[str, list[float]] = {}
    cat_n: dict[str, int] = {}
    skipped = 0
    examples = []
    for ev in events:
        ymd = ev["notice_date"].replace("-", "")
        idx = bisect.bisect_right(dates, ymd)
        if idx <= 0 or idx >= len(dates):
            skipped += 1
            continue
        ts = code_to_ts(ev["stock_code"])
        car = compute_event_car(idx, ts, dates, rets, market, horizon)
        if car is None:
            skipped += 1
            continue
        cat = classify_title(ev["title"])
        cat_rows.setdefault(cat, []).append(car["car1"])
        cat_rows5.setdefault(cat, []).append(car["car5"])
        cat_n[cat] = cat_n.get(cat, 0) + 1
        if len(examples) < 5:
            examples.append({"title": ev["title"][:80], "stock": ev["stock_code"],
                             "notice_date": ev["notice_date"], "car1": round(car["car1"], 3)})
    cat_stats = []
    for cat in sorted(cat_rows, key=lambda c: -len(cat_rows[c])):
        s1 = stats(cat_rows[cat])
        s5 = stats(cat_rows5[cat])
        cat_stats.append({"category": cat, "n": s1["n"], "car1": s1, "car5": s5,
                          "low_sample": s1["n"] < min_n})
    result = {"ok": True, "window": {"start": start, "end": end, "trading_days": len(dates)},
              "events_used": sum(cat_n.values()), "events_skipped": skipped,
              "categories": cat_stats, "examples": examples,
              "method": "market-adjusted AR; event day = next trading day after notice_date",
              "catalog_version": (json.loads(CATALOG.read_text(encoding="utf-8")).get("version")
                                  if CATALOG.exists() else None),
              "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    return result


def load_total_mv(dates: list[str]) -> dict:
    """载入 daily_basic.total_mv：{date: {ts_code: total_mv}}。"""
    import pyarrow.parquet as pq
    base = DATA / "tushare" / "daily_basic"
    out: dict[str, dict] = {}
    for d in dates:
        f = base / ("d_%s.parquet" % d)
        try:
            tb = pq.read_table(f, columns=["ts_code", "total_mv"])
        except Exception:  # noqa: BLE001
            continue
        codes = tb.column("ts_code").to_pylist()
        mvs = tb.column("total_mv").to_pylist()
        m = {c: float(v) for c, v in zip(codes, mvs) if v is not None}
        if m:
            out[d] = m
    return out


def size_bucket_means(date: str, rets_day: dict, mv_day: dict, market_mean: float) -> dict:
    """按当日市值分 5 组，返回 {ts_code: 该组平均 AR}；样本不足返回空。"""
    codes = [c for c in mv_day if c in rets_day]
    n = len(codes)
    if n < 25:
        return {}
    codes.sort(key=lambda c: mv_day[c])
    bucket_of = {c: min(4, int(i * 5 / n)) for i, c in enumerate(codes)}
    sums = [0.0] * 5
    cnt = [0] * 5
    for c in codes:
        b = bucket_of[c]
        sums[b] += rets_day[c] - market_mean
        cnt[b] += 1
    means = [sums[b] / cnt[b] if cnt[b] else 0.0 for b in range(5)]
    return {c: means[bucket_of[c]] for c in codes}


def compute_neutral_car(event_idx: int, ts_code: str, dates: list[str], rets: dict,
                        market: dict, bucket_means: dict, horizon: int = 1) -> dict | None:
    """市值中性化 CAR：每日 AR = 个股收益 − 同市值组均值；horizon=1 即 CAR[0,1]。"""
    ars = []
    for k in range(horizon + 1):
        i = event_idx + k
        if i >= len(dates):
            return None
        d = dates[i]
        r = rets.get(d, {}).get(ts_code)
        if r is None:
            return None
        bmean = bucket_means.get(d, {}).get(ts_code)
        if bmean is None:
            return None
        ars.append(r - bmean)
    return {"car": sum(ars), "ars": ars}


def run_v2(days: int = 90, cost_bps: int = 15, split: float = 0.6,
           min_n: int = 30, class_focus: str = "业绩预告") -> dict:
    """H1 晋级三关：样本内/样本外 + 市值中性 + 成本后净 CAR。"""
    all_dates = trading_dates(days + 8)
    rets, market = load_returns(all_dates)
    dates = [d for d in all_dates if d in rets]
    if len(dates) < 30:
        return {"ok": False, "error": "trading dates insufficient"}
    mv = load_total_mv(dates)
    bucket_means = {d: size_bucket_means(d, rets[d], mv.get(d, {}), market.get(d, 0.0)) for d in dates}
    start, end = dates[0], dates[-1]
    events = load_events(start, end)
    split_idx = int(len(dates) * split)
    cost_pct = 2 * cost_bps / 100.0  # bps→百分点，双边
    rows = {}
    for ev in events:
        ymd = ev["notice_date"].replace("-", "")
        idx = bisect.bisect_right(dates, ymd)
        if idx <= 0 or idx >= len(dates) - 1:
            continue
        ts = code_to_ts(ev["stock_code"])
        cat = classify_title(ev["title"])
        car1 = compute_event_car(idx, ts, dates, rets, market, horizon=1)
        neu = compute_neutral_car(idx, ts, dates, rets, market, bucket_means, horizon=1)
        if car1 is None or neu is None:
            continue
        seg = "IS" if idx < split_idx else "OOS"
        d = rows.setdefault(cat, {"IS": [], "OOS": []})
        d[seg].append({"raw": car1["car1"], "neutral": neu["car"],
                       "net": neu["car"] - cost_pct})
    out = []
    for cat, segs in sorted(rows.items(), key=lambda kv: -(len(kv[1]["IS"]) + len(kv[1]["OOS"]))):
        is_vals = [x["raw"] for x in segs["IS"]]
        oos_raw = [x["raw"] for x in segs["OOS"]]
        oos_neu = [x["neutral"] for x in segs["OOS"]]
        oos_net = [x["net"] for x in segs["OOS"]]
        s_raw, s_neu, s_net = stats(oos_raw), stats(oos_neu), stats(oos_net)
        passed = bool(s_net.get("n", 0) >= min_n and s_net.get("mean", -1) > 0
                      and s_net.get("t", 0) >= 1.5)
        out.append({"category": cat, "n_is": len(is_vals), "n_oos": s_net.get("n", 0),
                    "is_raw": stats(is_vals), "oos_raw": s_raw, "oos_neutral": s_neu,
                    "oos_net": {**s_net, "mean": round(s_net.get("mean", 0), 4)},
                    "cost_pct": cost_pct, "pass": passed, "low_sample": s_net.get("n", 0) < min_n})
    focus = next((r for r in out if r["category"] == class_focus), None)
    return {"ok": True, "window": {"start": start, "end": end, "trading_days": len(dates),
                                  "split_date": dates[split_idx]},
            "events": {r["category"]: r["n_is"] + r["n_oos"] for r in out},
            "cost_bps_per_side": cost_bps, "cost_pct_roundtrip": cost_pct,
            "min_n": min_n, "class_focus": class_focus, "focus": focus,
            "categories": out,
            "catalog_version": (json.loads(CATALOG.read_text(encoding="utf-8")).get("version")
                                if CATALOG.exists() else None),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}


def render_v2(res: dict) -> str:
    if not res.get("ok"):
        return "# H1 v2 失败\n\n" + json.dumps(res, ensure_ascii=False)
    lines = ["# H1 公告事件 CAR — 晋级三关（v2，样本外+市值中性+成本后）", ""]
    lines.append("- 生成：%s" % res["generated_at"])
    lines.append("- 窗口：%s..%s（%d 交易日；样本内截止 %s，之后为样本外）" % (
        res["window"]["start"], res["window"]["end"], res["window"]["trading_days"],
        res["window"]["split_date"]))
    lines.append("- 成本：双边 %d bps（合计 %.2f 百分点）；中性：当日市值 5 分位；catalog v%s" % (
        res["cost_bps_per_side"], res["cost_pct_roundtrip"], res.get("catalog_version")))
    lines.append("")
    lines.append("## 判定口径（四关简化）")
    lines.append("- 样本外（OOS）：窗口后 40% 交易日；")
    lines.append("- 成本后净 CAR = 市值中性 CAR[0,1] − 双边成本；")
    lines.append("- **通过条件**：OOS n≥%d 且 净CAR>0 且 t≥1.5。" % res["min_n"])
    lines.append("")
    lines.append("| 类别 | n(IS) | n(OOS) | IS raw | OOS raw | OOS 中性 | **OOS 净** | t(净) | 判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in res["categories"]:
        mark = "✅" if c["pass"] else ("⚠样本少" if c["low_sample"] else "❌")
        lines.append("| %s | %d | %d | %.2f%% | %.2f%% | %.2f%% | **%.2f%%** | %s | %s |" % (
            c["category"], c["n_is"], c["n_oos"],
            c["is_raw"].get("mean", 0), c["oos_raw"].get("mean", 0),
            c["oos_neutral"].get("mean", 0), c["oos_net"].get("mean", 0),
            c["oos_net"].get("t", 0), mark))
    lines.append("")
    f = res.get("focus")
    if f:
        lines.append("## 重点：%s" % res["class_focus"])
        lines.append("- IS raw %.2f%% → OOS raw %.2f%% → OOS 中性 %.2f%% → **OOS 净 %.2f%%**（t=%s, n=%d）" % (
            f["is_raw"].get("mean", 0), f["oos_raw"].get("mean", 0),
            f["oos_neutral"].get("mean", 0), f["oos_net"].get("mean", 0),
            f["oos_net"].get("t"), f["n_oos"]))
        lines.append("- 判定：%s" % ("**通过**（可进入影子/策略评审）" if f["pass"] else "**未通过**（不晋升，记录原因）"))
    lines.append("")
    lines.append("## 局限")
    lines.append("1. 市值中性化用当日 total_mv 5 分组，未做行业中性；")
    lines.append("2. 成本用固定双边 bps，未计冲击成本/涨跌停不可交易；")
    lines.append("3. 样本外只切一次（时间 60/40），无滚动验证；")
    lines.append("4. 事件日假设=披露后下一交易日。")
    lines.append("")
    lines.append("> 只有 OOS 净 CAR 为正且 t≥1.5 的类别才可上调为 validated；否则维持 backtested。")
    return "\n".join(lines) + "\n"


def render_md(res: dict) -> str:
    lines = ["# H1 公告事件 CAR（第一版）", ""]
    lines.append("- 生成：%s" % res.get("generated_at"))
    lines.append("- 数据窗口：%s..%s（%d 个交易日）" % (
        res["window"]["start"], res["window"]["end"], res["window"]["trading_days"]))
    lines.append("- catalog 版本：v%s；事件数：%d（跳过 %d 数据不全）" % (
        res.get("catalog_version"), res["events_used"], res["events_skipped"]))
    lines.append("- 方法：市场调整 AR（个股 minus 全市场等权均值），事件日=披露后下一交易日；CAR[0,1]/[0,5]")
    lines.append("")
    lines.append("## 分类结果（按样本量降序）")
    lines.append("")
    lines.append("| 类别 | n | CAR[0,1] 均值 | 中位 | 胜率 | t | CAR[0,5] 均值 | 中位 | 胜率 | t |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in res["categories"]:
        a, b = c["car1"], c["car5"]
        mark = " ⚠样本少" if c["low_sample"] else ""
        lines.append("| %s%s | %d | %s%% | %s%% | %s | %s | %s%% | %s%% | %s | %s |" % (
            c["category"], mark, a["n"], a.get("mean", 0), a.get("median", 0),
            a.get("win_rate", 0), a.get("t", 0),
            b.get("mean", 0), b.get("median", 0), b.get("win_rate", 0), b.get("t", 0)))
    lines.append("")
    lines.append("## 样例事件")
    for e in res["examples"]:
        lines.append("- %s %s（%s）CAR[0,1]=%.2f%%" % (
            e["stock"], e["title"], e["notice_date"], e["car1"]))
    lines.append("")
    lines.append("## 局限（第一版）")
    lines.append("1. 未扣交易成本、未考虑涨跌停/停牌；")
    lines.append("2. 未做行业/市值/beta 中性化（用市场等权调整）；")
    lines.append("3. 未做样本外与参数敏感性；")
    lines.append("4. 事件日假设为「披露后下一交易日」，未区分盘前/盘后披露。")
    lines.append("")
    lines.append("> 结论口径：本版只作探索性证据；任何类别要进入策略验证，必须补做 OOS/成本/容量三关（见 research/README.md）。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H1 公告事件 CAR")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--promote", action="store_true", help="晋级三关模式（OOS+市值中性+成本）")
    ap.add_argument("--cost-bps", type=int, default=15, help="单边成本 bps（默认 15）")
    ap.add_argument("--split", type=float, default=0.6, help="样本内占比（默认 0.6）")
    ap.add_argument("--focus", default="业绩预告", help="重点观察类别")
    args = ap.parse_args(argv)
    if args.promote:
        res = run_v2(args.days, args.cost_bps, args.split, args.min_n, args.focus)
        if not res.get("ok"):
            print(json.dumps(res, ensure_ascii=False))
            return 2
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        md_path = REPORT_DIR / ("H1_announcement_car_v2_%s.md" % day)
        js_path = REPORT_DIR / ("H1_announcement_car_v2_%s.json" % day)
        md_path.write_text(render_v2(res), encoding="utf-8")
        js_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.json:
            print(json.dumps({k: res[k] for k in ("ok", "window", "cost_bps_per_side",
                                                  "focus", "catalog_version")},
                             ensure_ascii=False, indent=1))
        else:
            print(render_v2(res))
        print("\n报告：%s" % md_path)
        return 0
    res = run(args.days, args.horizon, args.min_n)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    md_path = REPORT_DIR / ("H1_announcement_car_%s.md" % day)
    js_path = REPORT_DIR / ("H1_announcement_car_%s.json" % day)
    md_path.write_text(render_md(res), encoding="utf-8")
    js_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({k: res[k] for k in ("ok", "window", "events_used", "events_skipped",
                                              "catalog_version")}, ensure_ascii=False, indent=1))
    else:
        print(render_md(res))
    print("\n报告：%s" % md_path)
    print("数据：%s" % js_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
