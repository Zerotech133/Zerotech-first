# -*- coding: utf-8 -*-
"""H11（3 年公告 CAR，行业中性）+ H2（PEAD，持有 5/10/20 日）研究工具。

- 数据：ann_history（2023+，49 万公告）+ tushare daily（3 年）+ stock_basic 行业 + daily_basic 市值；
- 中性化：行业（stock_basic.industry）均值调整，行业样本<10 回落全市场；
- 三关：样本内/样本外 60/40、成本双边 15bps、OOS 显著性；
- H2 PEAD：按事件日 AR0 正/负分组，看 CAR5/10/20 是否延续（long-short）。

用法：
  python tools/research/event_study2.py --mode h11 --days 760
  python tools/research/event_study2.py --mode pead --days 760
退出码：0=ok / 2=数据不足。
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_MAIN = Path(r"C:\项目\老田的AI助手")
DATA = (ROOT / "quant_backtest" / "data") if (ROOT / "quant_backtest" / "data" / "tushare" / "daily").exists() \
    else (_MAIN / "quant_backtest" / "data")
ANN_DB = ROOT / "zerobound-kg" / "data" / "ann_history" / "ann_history.db"
if not ANN_DB.exists():
    ANN_DB = Path(r"C:\项目\zerobound-kg\data\ann_history\ann_history.db")
REPORT_DIR = ROOT / "research" / "reports"
DAILY = DATA / "tushare" / "daily"
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import ann_classify  # noqa: E402
except Exception:  # noqa: BLE001
    ann_classify = None


def earn_direction(title: str) -> str:
    """从公告标题提取盈余方向：pos(预增/预盈) / neg(预减/预亏) / actual(快报) / unknown。"""
    t = title or ""
    if not any(k in t for k in ("业绩预告", "业绩快报", "预增", "预减", "预盈", "预亏")):
        return ""
    if "预增" in t or "预盈" in t:
        return "pos"
    if "预减" in t or "预亏" in t:
        return "neg"
    if "快报" in t:
        return "actual"
    return "unknown"


def classify(columns_txt: str, title: str) -> str:
    if ann_classify is not None:
        return ann_classify.classify(columns_txt, title)
    return title[:4] or "其他"


def trading_dates(n: int) -> list[str]:
    files = sorted(DAILY.glob("d_*.parquet"))
    ds = [f.stem[2:] for f in files if f.stem[2:].isdigit()]
    return ds[-n:] if len(ds) > n else ds


def load_returns(dates: list[str]) -> tuple[dict, dict]:
    import pyarrow.parquet as pq
    per: dict[str, dict] = {}
    market: dict[str, float] = {}
    for d in dates:
        try:
            tb = pq.read_table(DAILY / ("d_%s.parquet" % d), columns=["ts_code", "pct_chg"])
        except Exception:  # noqa: BLE001
            continue
        m = {}
        vals = []
        for c, v in zip(tb.column("ts_code").to_pylist(), tb.column("pct_chg").to_pylist()):
            if v is None:
                continue
            f = float(v)
            m[c] = f
            vals.append(f)
        per[d] = m
        market[d] = sum(vals) / len(vals) if vals else 0.0
    return per, market


def load_industry() -> dict:
    import pyarrow.parquet as pq
    parts = sorted((DATA / "tushare" / "stock_basic").glob("part_*.parquet"))
    if not parts:
        return {}
    tb = pq.read_table(parts[-1], columns=["ts_code", "industry"])
    out = {}
    for c, ind in zip(tb.column("ts_code").to_pylist(), tb.column("industry").to_pylist()):
        out[c] = ind or "未知"
    return out


def load_total_mv(dates: list[str]) -> dict:
    import pyarrow.parquet as pq
    base = DATA / "tushare" / "daily_basic"
    out: dict[str, dict] = {}
    for d in dates:
        try:
            tb = pq.read_table(base / ("d_%s.parquet" % d), columns=["ts_code", "total_mv"])
        except Exception:  # noqa: BLE001
            continue
        m = {}
        for c, v in zip(tb.column("ts_code").to_pylist(), tb.column("total_mv").to_pylist()):
            if v is not None:
                m[c] = float(v)
        if m:
            out[d] = m
    return out


def _size_bucket(day_mv: dict, n_bucket: int = 3) -> dict:
    codes = sorted(day_mv, key=lambda c: day_mv[c])
    n = len(codes)
    if n == 0:
        return {}
    return {c: min(n_bucket - 1, int(i * n_bucket / n)) for i, c in enumerate(codes)}


def neutral_maps(dates: list[str], rets: dict, market: dict, industry: dict,
                 mode: str = "industry", mv: dict | None = None, min_group: int = 10) -> dict:
    """{date: {code: 组均值AR}}，组=行业（样本不足回落市场）。"""
    out = {}
    for d in dates:
        day = rets.get(d) or {}
        sizes = _size_bucket((mv or {}).get(d, {})) if mode in ("size", "industry_size") else {}
        groups: dict[str, list[float]] = {}
        for c, r in day.items():
            ind = industry.get(c) or "未知"
            if mode in ("size", "industry_size"):
                g = ("S%s" % sizes.get(c, 9))
                if mode == "industry_size":
                    g = ind + "|" + g
            else:
                g = ind
            groups.setdefault(g, []).append(r - market.get(d, 0.0))
        means = {g: sum(v) / len(v) for g, v in groups.items() if len(v) >= min_group}
        m = {}
        for c in day:
            ind = industry.get(c) or "未知"
            if mode in ("size", "industry_size"):
                g = ("S%s" % sizes.get(c, 9))
                if mode == "industry_size":
                    g = ind + "|" + g
            else:
                g = ind
            m[c] = means.get(g, market.get(d, 0.0))
        out[d] = m
    return out


def code_to_ts(code: str) -> str:
    c = str(code).zfill(6)
    if c[:3] in ("600", "601", "603", "605", "688", "689", "900"):
        return c + ".SH"
    if c[:3] in ("000", "001", "002", "003", "300", "301"):
        return c + ".SZ"
    if c[0] in ("4", "8", "9"):
        return c + ".BJ"
    return c + ".SZ"


def load_anns_d_events(since: str, until: str) -> list[dict]:
    """3 年公告事件源：tushare anns_d 按日 parquet（ann_date/ts_code/title）。"""
    import pyarrow.parquet as pq
    base = DATA / "tushare" / "anns_d"
    files = sorted(base.glob("d_*.parquet"))
    out = []
    for f in files:
        d = f.stem[2:]
        if not d.isdigit() or d < since.replace("-", "") or d > until.replace("-", ""):
            continue
        try:
            tb = pq.read_table(f, columns=["ann_date", "ts_code", "title"])
        except Exception:  # noqa: BLE001
            continue
        for ad, tc, title in zip(tb.column("ann_date").to_pylist(),
                                 tb.column("ts_code").to_pylist(),
                                 tb.column("title").to_pylist()):
            if not ad or not tc:
                continue
            out.append({"notice_date": str(ad)[:4] + "-" + str(ad)[4:6] + "-" + str(ad)[6:8],
                        "title": title or "", "columns_txt": "",
                        "ts_code": str(tc),
                        "category": classify("", title or "")})
    return out


def load_events(since: str) -> list[dict]:
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % str(ANN_DB).replace("\\", "/"), uri=True, timeout=60)
    rows = con.execute(
        "SELECT a.notice_date, a.title, a.columns_txt, s.stock_code FROM ann a "
        "JOIN ann_stock s ON a.art_code=s.art_code WHERE a.notice_date>=?", (since,)).fetchall()
    con.close()
    return [{"notice_date": r[0], "title": r[1] or "", "columns_txt": r[2] or "",
             "stock_code": r[3], "category": classify(r[2] or "", r[1] or "")} for r in rows]


def stats(vals: list[float]) -> dict:
    n = len(vals)
    if n < 2:
        return {"n": n, "mean": round(sum(vals) / n, 4) if n else 0, "t": 0, "win_rate": 0}
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    std = math.sqrt(var)
    t = mean / (std / math.sqrt(n)) if std > 0 else 0.0
    return {"n": n, "mean": round(mean, 4), "median": round(sorted(vals)[n // 2], 4),
            "win_rate": round(sum(1 for v in vals if v > 0) / n, 4), "t": round(t, 2),
            "std": round(std, 4)}


def run(mode: str, days: int, cost_bps: int = 15, split: float = 0.6,
        min_n: int = 30, neutral_mode: str = "industry_size", shift: int = 1) -> dict:
    dates = trading_dates(days + 22)
    rets, market = load_returns(dates)
    dates = [d for d in dates if d in rets]
    if len(dates) < 60:
        return {"ok": False, "error": "trading dates insufficient: %d" % len(dates)}
    industry = load_industry()
    mv = load_total_mv(dates) if neutral_mode in ("size", "industry_size") else {}
    neutral = neutral_maps(dates, rets, market, industry, mode=neutral_mode, mv=mv)
    split_idx = int(len(dates) * split)
    cost_pct = 2 * cost_bps / 100.0
    since = "%s-%s-%s" % (dates[0][:4], dates[0][4:6], dates[0][6:])
    until = "%s-%s-%s" % (dates[-1][:4], dates[-1][4:6], dates[-1][6:])
    events = load_anns_d_events(since, until)
    horizons = [5, 10, 20] if mode == "pead" else [5]
    focus = "业绩预告"
    if mode == "h11dir":
        focus = "earnings"
    rows = []
    for ev in events:
        if mode in ("h11", "pead") and ev["category"] != focus:
            continue
        if mode == "h11dir":
            if ev["category"] != "业绩预告":
                continue
            direction = earn_direction(ev["title"])
            if direction not in ("pos", "neg"):
                continue
        ymd = ev["notice_date"].replace("-", "")
        idx = bisect.bisect_left(dates, ymd) if shift == 0 else bisect.bisect_right(dates, ymd)
        if idx <= 0 or idx + max(horizons) >= len(dates):
            continue
        ts = ev.get("ts_code") or code_to_ts(ev.get("stock_code", ""))
        ars = []
        ok = True
        for k in range(max(horizons) + 1):
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
        rows.append({"seg": seg, "dir": direction if mode == "h11dir" else "",
                     "ars": ars,
                     "car": {h: sum(ars[: h + 1]) for h in horizons},
                     "post": {h: sum(ars[1: h + 1]) for h in horizons}})
    if not rows:
        return {"ok": False, "error": "no computable events"}

    def summarize(seg_filter: str, h: int) -> dict:
        vals = [r["car"][h] - cost_pct for r in rows if r["seg"] == seg_filter]
        return stats(vals)

    cats = {}
    if mode == "h11":
        for ev in events:
            cats[ev["category"]] = cats.get(ev["category"], 0) + 1
    out = {"ok": True, "mode": mode, "window": {"start": dates[0], "end": dates[-1],
                                                "trading_days": len(dates),
                                                "split_date": dates[split_idx]},
           "neutral_mode": neutral_mode, "event_shift": shift, "cost_bps_per_side": cost_bps, "cost_pct_roundtrip": cost_pct,
           "events_used": len(rows), "focus": focus,
           "class_coverage": cats,
           "result": {}}
    for h in horizons:
        is_s = summarize("IS", h)
        oos = summarize("OOS", h)
        passed = bool(oos.get("n", 0) >= min_n and oos.get("mean", -1) > 0 and oos.get("t", 0) >= 1.5)
        out["result"][str(h)] = {"is": is_s, "oos": oos, "pass": passed}
        if mode == "h11dir":
            pos_is = [r["car"][h] - cost_pct for r in rows if r["seg"] == "IS" and r["dir"] == "pos"]
            neg_is = [r["car"][h] - cost_pct for r in rows if r["seg"] == "IS" and r["dir"] == "neg"]
            pos_oos = [r["car"][h] - cost_pct for r in rows if r["seg"] == "OOS" and r["dir"] == "pos"]
            neg_oos = [r["car"][h] - cost_pct for r in rows if r["seg"] == "OOS" and r["dir"] == "neg"]
            sp_is, sn_is, sp_o, sn_o = stats(pos_is), stats(neg_is), stats(pos_oos), stats(neg_oos)
            ls_is = round(sp_is["mean"] - sn_is["mean"], 4) if pos_is and neg_is else None
            ls_oos = round(sp_o["mean"] - sn_o["mean"], 4) if pos_oos and neg_oos else None
            out["result"][str(h)]["pos"] = {"is": sp_is, "oos": sp_o}
            out["result"][str(h)]["neg"] = {"is": sn_is, "oos": sn_o}
            out["result"][str(h)]["ls_is"] = ls_is
            out["result"][str(h)]["ls_oos"] = ls_oos
            out["result"][str(h)]["pass"] = bool(
                sp_o["n"] >= min_n and sn_o["n"] >= min_n
                and ls_oos is not None and ls_oos >= 0.30)  # OOS 多空差需覆盖 30bp 成本才通过
    if mode == "pead":
        # 按事件日 AR0 符号分组（正/负），比较 CAR[后] 延续性
        for h in horizons:
            pos = [r["post"][h] - cost_pct for r in rows if r["ars"][0] > 0]
            neg = [r["post"][h] - cost_pct for r in rows if r["ars"][0] < 0]
            sp, sn = stats(pos), stats(neg)
            out["result"][str(h)]["long_leg"] = sp
            out["result"][str(h)]["short_leg"] = sn
            if sp.get("n", 0) and sn.get("n", 0):
                out["result"][str(h)]["long_short_mean"] = round(sp["mean"] - sn["mean"], 4)
    out["industry_coverage"] = {
        "stocks_with_industry": sum(1 for v in industry.values() if v and v != "未知"),
        "stocks_total": len(industry),
        "industries": len(set(industry.values())),
    }
    out["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return out


def render(res: dict) -> str:
    if not res.get("ok"):
        return "# 失败\n\n" + json.dumps(res, ensure_ascii=False)
    title = ("H11 三年公告 CAR（行业×规模中性）" if res["mode"] == "h11"
             else "H11dir 盈余方向事件研究（预增/预盈 vs 预减/预亏）" if res["mode"] == "h11dir"
             else "H2 PEAD（公告后漂移）")
    lines = ["# %s" % title, ""]
    lines.append("- 生成：%s；窗口 %s..%s（%d 交易日；样本内截止 %s）" % (
        res["generated_at"], res["window"]["start"], res["window"]["end"],
        res["window"]["trading_days"], res["window"]["split_date"]))
    lines.append("- 成本：双边 %d bps（%.2f 百分点）；中性：%s（样本<10 回落市场）；事件：%s" % (
        res["cost_bps_per_side"], res["cost_pct_roundtrip"], res.get("neutral_mode"), res["focus"]))
    lines.append("- 事件数（可计算）：%d" % res["events_used"])
    if res.get("class_coverage"):
        lines.append("- 分类覆盖：%s" % json.dumps(
            dict(sorted(res["class_coverage"].items(), key=lambda kv: -kv[1])[:8]),
            ensure_ascii=False))
    lines.append("")
    for h, r in res["result"].items():
        lines.append("## 持有/窗口 %s 日" % h)
        if "is" in r:
            lines.append("- IS：n=%s 净均值=%s%% t=%s；OOS：n=%s 净均值=%s%% t=%s → **%s**" % (
                r["is"]["n"], r["is"]["mean"], r["is"]["t"], r["oos"]["n"], r["oos"]["mean"],
                r["oos"]["t"], "通过" if r["pass"] else "未通过"))
        if "pos" in r:
            p, n = r["pos"], r["neg"]
            lines.append("- 预增/预盈：IS n=%s 净=%s%% / OOS n=%s 净=%s%%（t=%s）；预减/预亏：IS n=%s 净=%s%% / OOS n=%s 净=%s%%（t=%s）" % (
                p["is"]["n"], p["is"]["mean"], p["oos"]["n"], p["oos"]["mean"], p["oos"]["t"],
                n["is"]["n"], n["is"]["mean"], n["oos"]["n"], n["oos"]["mean"], n["oos"]["t"]))
            lines.append("- 多空差：IS=%s%% / OOS=%s%% → **%s**" % (
                r.get("ls_is"), r.get("ls_oos"), "通过" if r["pass"] else "未通过"))
        if "long_leg" in r:
            lines.append("- 事件后窗口（排除 AR0）正/负组：n=%s 净=%s%% / n=%s 净=%s%%；多空差=%s%%" % (
                r["long_leg"]["n"], r["long_leg"]["mean"], r["short_leg"]["n"],
                r["short_leg"]["mean"], r.get("long_short_mean")))
    lines += ["", "## 局限",
              "1. 行业映射用当前 stock_basic.industry（未做历史行业快照）；",
              "2. 未控制市值/beta/流动性；",
              "3. 事件日=披露后下一交易日（未区分盘前/盘后）；",
              "4. 样本外只切一次（60/40），无滚动验证。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="H11/H2 事件研究 v3")
    ap.add_argument("--mode", choices=["h11", "h11dir", "pead"], default="h11")
    ap.add_argument("--days", type=int, default=760)
    ap.add_argument("--cost-bps", type=int, default=15)
    ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--neutral", choices=["industry", "size", "industry_size"],
                    default="industry_size")
    ap.add_argument("--shift", type=int, choices=[0, 1], default=1,
                    help="事件日：0=公告日当日起；1=下一交易日（默认，盘后公告假设）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.mode, args.days, args.cost_bps, args.split, args.min_n, args.neutral, args.shift)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False))
        return 2
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y%m%d")
    name = ("H11_announcement_car_3y_%s" % day if args.mode == "h11"
            else "H11dir_earnings_direction_%s" % day if args.mode == "h11dir"
            else "H2_pead_3y_%s" % day)
    md = REPORT_DIR / (name + ".md")
    js = REPORT_DIR / (name + ".json")
    md.write_text(render(res), encoding="utf-8")
    js.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.json:
        print(json.dumps({"ok": True, "mode": res["mode"], "events": res["events_used"],
                          "result": res["result"]}, ensure_ascii=False, indent=1))
    else:
        print(render(res))
    print("\n报告：%s" % md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
