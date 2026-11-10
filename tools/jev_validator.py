# -*- coding: utf-8 -*-
"""JEV 三维度三角定位 · 判伪→N日回落 验证器（轻量、离线优先）。

目的（老田 2026-11-10 立项）：把「新闻/事实解读 OK」升级成「判定平台可信」的
硬证据——拿 N 个历史涨停日，对当日涨停股跑三维度三角定位，统计「判伪」集合在
其后 N 个交易日的实际回落比例，并与全样本涨停基线对照。

三维度（与 jev_flow / jev_social / limit_list 同源，但本脚本直接读快照，离线、
不触发联网、不被快照 TTL 卡死）：
  · 资金维度：ts_moneyflow 快照 flow_YYYYMMDD.json
        divergence = 主力净买(main_net>0) 但 全档净流出(net_mf<0) → 判伪
        主力净卖(main_net<0) → 判伪（没人接）
        主力净买且全档净流入 → 支撑
  · 结构维度（时间维度的离线代理，非 Δmin）：limit_list_d 的
        open_times（开板次数≥2）/ limit_times（连板≥3）/ first_time（14:00 后封板）
        → 任一命中即判伪（封不住 / 已高位）；首板少开板早封 → 支撑
        （注：真正的「时间维度」是价格先行 vs 消息追认的 Δmin，需 stk_mins+新闻
         时间戳，尚未接入——列入能力账本 unknown，本维度是其结构代理）
  · 社交维度：social_YYYYMMDD.json（股吧人气 + 雪球四榜）
        股吧进榜且雪球无人气榜(xq_hot_rank=None)且(名次上升或前20) → 散户接盘 判伪
        雪球有人气榜 → 机构关注 支撑

三角定位：取「可用维度」（缺数据的维记 unknown，不参与投票）；
  可用维 ≥2 且 判伪票 > 支撑票 → 判伪；支撑票 > 判伪票 → 支撑；否则 歧义。

前向收益：daily.parquet，从涨停日次一交易日起累乘 N 日 pct_chg；
  回落 = N 日累计收益 ≤ 0（涨停次日买入持有 N 日亏损）。

输出：quant_backtest/reports/jev_validator_YYYYMMDD.json + .md
只读，不做任何写库。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(r"C:\项目\老田的AI助手")
SNAP_DIR = ROOT / "quant_backtest" / "data" / "tushare" / "_jev_snap"
SOCIAL_DIR = ROOT / "portal"
CURATED = ROOT / "quant_backtest" / "data" / "tushare_curated"
REPORT_DIR = ROOT / "quant_backtest" / "reports"
BENCH = "000300.SH"


# ─────────────────────────── 数据读取 ───────────────────────────
def load_open_days() -> list[str]:
    t = pq.read_table(CURATED / "trade_cal.parquet",
                      columns=["exchange", "cal_date", "is_open"],
                      filters=[("exchange", "=", "SSE"), ("is_open", "=", 1)])
    return sorted(str(x) for x in t.column("cal_date").to_pylist())


def max_daily_date() -> str:
    t = pq.read_table(CURATED / "daily.parquet", columns=["trade_date"])
    return max(str(x) for x in t.column("trade_date").to_pylist())


def next_n_days(day: str, opens: list[str], n: int) -> list[str]:
    lo, hi = 0, len(opens)
    while lo < hi:
        mid = (lo + hi) // 2
        if opens[mid] <= day:
            lo = mid + 1
        else:
            hi = mid
    return opens[lo:lo + n]


def build_universe(days: list[str]) -> dict:
    """涨停宇宙，按优先级合并两源：
    1) limit_list_d（≤20261103）全字段（含 open_times/limit_times/first_time → 结构维度可用）；
    2) 近期日（不在 limit_list_d）从 daily.parquet 按 pct_chg>=9.8 补，结构字段置 None。
    返回 {day: [{ts_code, pct_chg, name, industry, open_times, limit_times, first_time}]}。"""
    out: dict = {}
    t = pq.read_table(CURATED / "limit_list_d.parquet")
    mask = pc.is_in(t["trade_date"], value_set=pa.array(days))
    t = t.filter(mask)
    cols = t.column_names
    for i in range(t.num_rows):
        r = {c: t.column(c)[i].as_py() for c in cols}
        if str(r.get("limit") or "") != "Y" and float(r.get("pct_chg") or 0) < 9.8:
            continue
        d = str(r["trade_date"])
        out.setdefault(d, []).append({
            "ts_code": str(r["ts_code"]), "pct_chg": float(r.get("pct_chg") or 0),
            "name": r.get("name"), "industry": r.get("industry"),
            "open_times": r.get("open_times"), "limit_times": r.get("limit_times"),
            "first_time": r.get("first_time")})
    ll_days = set(out.keys())
    recent = [d for d in days if d not in ll_days]
    if recent:
        dt = pq.read_table(CURATED / "daily.parquet",
                          columns=["ts_code", "trade_date", "pct_chg"])
        m = pc.and_(pc.is_in(dt["trade_date"], value_set=pa.array(recent)),
                    pc.greater_equal(dt["pct_chg"], 9.8))
        dt = dt.filter(m)
        for c, d, p in zip(dt.column("ts_code").to_pylist(),
                           dt.column("trade_date").to_pylist(),
                           dt.column("pct_chg").to_pylist()):
            out.setdefault(str(d), []).append({
                "ts_code": str(c), "pct_chg": float(p),
                "name": None, "industry": None,
                "open_times": None, "limit_times": None, "first_time": None})
    return out


def load_flow_snapshot(day: str) -> dict | None:
    p = SNAP_DIR / ("flow_%s.json" % day)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_social_snapshot(day: str) -> dict | None:
    p = SOCIAL_DIR / ("social_%s.json" % day)
    if not p.exists():
        return None
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        by = {str(k)[:6]: v for k, v in (j.get("by_code") or {}).items()}
        return {"by_code": by, "has": True}
    except Exception:
        return None


def load_returns(dates: set[str], codes: set[str]) -> dict:
    t = pq.read_table(CURATED / "daily.parquet",
                      columns=["ts_code", "trade_date", "pct_chg"])
    mask = pc.and_(pc.is_in(t["trade_date"], value_set=pa.array(sorted(dates))),
                   pc.is_in(t["ts_code"], value_set=pa.array(sorted(codes))))
    t = t.filter(mask)
    out = {}
    for c, d, p in zip(t.column("ts_code").to_pylist(),
                       t.column("trade_date").to_pylist(),
                       t.column("pct_chg").to_pylist()):
        if p is not None:
            out[(str(c), str(d))] = float(p)
    return out


# ─────────────────────────── 三维度判定 ───────────────────────────
def money_verdict(flow: dict | None, code6: str) -> str:
    if not flow:
        return "unknown"
    v = flow.get(code6)
    if not v:
        return "unknown"
    main_net = v.get("main_net")
    net_mf = v.get("net_mf")
    if main_net is None or net_mf is None:
        return "unknown"
    if main_net > 0 and net_mf < 0:
        return "fake"          # divergence：大单接、全档逃 = 接盘
    if main_net > 0 and net_mf >= 0:
        return "support"       # 真金白银进
    if main_net < 0:
        return "fake"          # 主力净卖，涨停无人接
    return "unknown"


def structural_verdict(row: dict) -> str:
    """limit_list 字段做时间/结构维度的离线代理；字段缺失（近期日不在
    limit_list_d 内）则记 unknown，绝不误判为支撑。"""
    open_times = row.get("open_times")
    limit_times = row.get("limit_times")
    first_time = row.get("first_time")
    if open_times is None and limit_times is None and first_time is None:
        return "unknown"
    weak = False
    if open_times is not None and float(open_times) >= 2:
        weak = True
    if limit_times is not None and float(limit_times) >= 3:
        weak = True
    if first_time and isinstance(first_time, str) and len(first_time) >= 2:
        try:
            if int(first_time[:2]) >= 14:
                weak = True
        except ValueError:
            pass
    return "fake" if weak else "support"


def social_verdict(social: dict | None, code6: str) -> str:
    if not social or not social.get("has"):
        return "unknown"
    h = (social.get("by_code") or {}).get(code6) or {}
    guba = h.get("guba_rank")
    guba_chg = h.get("guba_rank_chg")
    xq = h.get("xq_hot_rank")
    if guba is not None and xq is None:
        if (guba_chg is not None and guba_chg > 0) or (guba <= 20):
            return "fake"       # 股吧散户追捧 + 雪球机构无视 = 散户接盘
    if xq is not None:
        return "support"        # 雪球机构圈有人气 = 机构关注
    return "unknown"


def triangulate(verdicts: list[str]) -> str:
    avail = [v for v in verdicts if v in ("fake", "support")]
    if len(avail) < 2:
        return "insufficient"
    f = avail.count("fake")
    s = avail.count("support")
    if f > s:
        return "fake"
    if s > f:
        return "support"
    return "tie"


# ─────────────────────────── 主流程 ───────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="JEV 三角定位判伪→N日回落 验证器")
    ap.add_argument("--days", nargs="*", default=None,
                    help="显式指定 YYYYMMDD 列表；省略=自动取有资金快照的涨停日")
    ap.add_argument("--require-social", action="store_true",
                    help="只纳入同时有社交快照的日（跑完整三维度）")
    ap.add_argument("--mode", choices=["triangulation", "money_only"],
                    default="triangulation",
                    help="triangulation=三维度多数票；money_only=仅资金背离单维"
                         "（隔离测试 JEV 在 11-06 真正用过的维度）")
    ap.add_argument("--fetch-flow", action="store_true",
                    help="对无资金快照的日经 xtcloud 现拉（联网、慢、写快照缓存）")
    ap.add_argument("--n", type=int, default=3, help="前向交易日窗口（主指标）")
    ap.add_argument("--extra-n", type=int, nargs="*", default=[1, 5],
                    help="额外汇报窗口（默认 1 5）")
    args = ap.parse_args(argv)
    t0 = time.time()

    # 选定日
    if args.days:
        days = sorted(set(args.days))
    else:
        days = sorted(p.name[5:13] for p in SNAP_DIR.glob("flow_*.json"))
    print("[1/5] 候选日 %d 个：%s" % (len(days), " ".join(days)), flush=True)

    opens = load_open_days()
    max_dt = max_daily_date()
    limit = build_universe(days)
    # 过滤：前向窗口不完整（最后一日超出 daily 最大交易日）的日直接剔除——
    # 否则窗口塌缩成 T+1 单日，结论失真；require-social 时剔掉无社交快照的日；
    # 无资金快照且无 --fetch-flow 也剔。
    k_max = max(sorted(set([args.n] + list(args.extra_n))))
    kept = []
    for d in days:
        if d not in limit or not limit[d]:
            continue
        last_fwd = next_n_days(d, opens, k_max)
        if not last_fwd or last_fwd[-1] > max_dt:
            print("  跳过 %s（前向窗口超出数据边界 %s，无法测 %d 日）" % (
                d, max_dt, k_max), flush=True)
            continue
        if args.require_social and not (SOCIAL_DIR / ("social_%s.json" % d)).exists():
            continue
        if not (SNAP_DIR / ("flow_%s.json" % d)).exists() and not args.fetch_flow:
            print("  跳过 %s（无资金快照，未开 --fetch-flow）" % d, flush=True)
            continue
        kept.append(d)
    days = kept
    if not days:
        print("无可用日，退出。可加 --fetch-flow 或先跑 jev_mins_fetch/flow 采集。")
        return
    print("  实际纳入 %d 日，涨停样本合计 %d" % (
        len(days), sum(len(limit[d]) for d in days)), flush=True)

    # 资金快照
    if args.fetch_flow:
        import sys
        sys.path.insert(0, str(ROOT / "portal"))
        import jev_flow  # noqa: F401  (仅确保可加载；现拉走 jev_flow.load_flow)
    flows = {}
    for d in days:
        f = load_flow_snapshot(d)
        if f is None and args.fetch_flow:
            sys.path.insert(0, str(ROOT / "portal"))
            import jev_flow
            f = jev_flow.load_flow(d)
        flows[d] = f
    socials = {d: load_social_snapshot(d) for d in days}

    # 前向收益所需 (code,date) 全集
    ns = sorted(set([args.n] + list(args.extra_n)))
    all_dates, all_codes = set(), set()
    for d in days:
        for r in limit[d]:
            all_codes.add(str(r["ts_code"]))
            for k in ns:
                for nd in next_n_days(d, opens, k):
                    all_dates.add(nd)
    px = load_returns(all_dates, all_codes)
    print("[2/5] 前向收益点 %d 个" % len(px), flush=True)

    def cumret(ts_code: str, day: str, k: int) -> float | None:
        dates = next_n_days(day, opens, k)
        if not dates:
            return None
        prod = 1.0
        got = 0
        for dd in dates:
            p = px.get((ts_code, dd))
            if p is None:
                continue
            prod *= (1 + p / 100.0)
            got += 1
        if got == 0:
            return None
        return round((prod - 1.0) * 100.0, 2)

    # 三角定位 + 归类
    classes = {"fake": [], "support": [], "tie": [], "insufficient": []}
    dim_cov = {"money": 0, "structural": 0, "social": 0}
    per_day = {}
    for d in days:
        flow = flows[d]
        social = socials[d]
        soc_avail = bool(social and social.get("has"))
        rows_cls = {"fake": 0, "support": 0, "tie": 0, "insufficient": 0}
        for r in limit[d]:
            c6 = str(r["ts_code"])[:6]
            mv = money_verdict(flow, c6)
            sv = structural_verdict(r)
            uv = social_verdict(social, c6) if soc_avail else "unknown"
            if mv != "unknown":
                dim_cov["money"] += 1
            if soc_avail and uv != "unknown":
                dim_cov["social"] += 1
            if sv != "unknown":
                dim_cov["structural"] += 1
            if args.mode == "money_only":
                cls = "fake" if mv == "fake" else "support" if mv == "support" else "insufficient"
            else:
                cls = triangulate([mv, sv, uv])
            rows_cls[cls] += 1
            rec = {"code": r["ts_code"], "name": r.get("name"),
                   "industry": r.get("industry"), "pct": r.get("pct_chg"),
                   "money": mv, "structural": sv, "social": uv,
                   "open_times": r.get("open_times"),
                   "limit_times": r.get("limit_times"),
                   "first_time": r.get("first_time"),
                   "cumret": {str(k): cumret(r["ts_code"], d, k) for k in ns}}
            classes[cls].append(rec)
        per_day[d] = rows_cls

    def summarize(recs, k):
        sub = [x["cumret"].get(str(k)) for x in recs if x["cumret"].get(str(k)) is not None]
        n = len(sub)
        pull = sum(1 for v in sub if v <= 0)
        mean = round(sum(sub) / n, 2) if n else None
        return {"n": n, "pullback_rate": round(pull / n, 3) if n else None,
                "mean_cumret": mean}

    print("[3/5] 三角定位完成", flush=True)
    print("[4/5] 统计 N日回落…", flush=True)
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {"days": days, "n": args.n, "extra_n": ns,
                   "require_social": args.require_social,
                   "fetch_flow": args.fetch_flow, "mode": args.mode},
        "coverage": {
            "n_days": len(days),
            "n_limit_total": sum(len(limit[d]) for d in days),
            "dim_coverage": dim_cov,
            "per_day": per_day,
            "class_counts": {k: len(v) for k, v in classes.items()},
        },
        "by_class": {},
    }
    base = [x for cls in classes for x in classes[cls]]
    result["by_class"]["baseline_all_limit"] = {str(k): summarize(base, k) for k in ns}
    for cls in ("fake", "support"):
        result["by_class"][cls] = {str(k): summarize(classes[cls], k) for k in ns}

    # 判伪相对基线的提升（lift）
    lift = {}
    for k in ns:
        b = result["by_class"]["baseline_all_limit"][str(k)]["pullback_rate"]
        f = result["by_class"]["fake"][str(k)]["pullback_rate"]
        lift[str(k)] = round((f or 0) - (b or 0), 3) if (b is not None and f is not None) else None
    result["lift_fake_minus_baseline"] = lift

    # 判伪样本明细（前 40）
    result["fake_samples"] = classes["fake"][:40]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = REPORT_DIR / ("jev_validator_%s.json" % time.strftime("%Y%m%d"))
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # Markdown
    L = ["# JEV 三角定位验证（%s）" % result["generated_at"], "",
         "> 轻量验证器 ｜ 只读 ｜ 判伪=三维度多数票；回落=N日累计收益≤0", "",
         "## 配置与覆盖", "",
         "- 纳入日：%s（%d 日，涨停样本 %d）" % (
             " ".join(days), len(days), result["coverage"]["n_limit_total"]),
         "- 维度覆盖（条）：资金 %d / 结构 %d / 社交 %d" % (
             dim_cov["money"], dim_cov["structural"], dim_cov["social"]),
         "- 分类计数：%s" % result["coverage"]["class_counts"], "",
         "## 核心结果（N日累计收益≤0 记为回落）", "",
         "| 窗口 | 判伪回落率 | 判伪样本 | 支撑回落率 | 支撑样本 | 全涨停基线 | lift |",
         "|---|---|---|---|---|---|---|"]
    for k in ns:
        fk = result["by_class"]["fake"][str(k)]
        sk = result["by_class"]["support"][str(k)]
        bk = result["by_class"]["baseline_all_limit"][str(k)]
        L.append("| %d日 | %s | %d | %s | %d | %s | %s |" % (
            k, _pct(fk["pullback_rate"]), fk["n"],
            _pct(sk["pullback_rate"]), sk["n"],
            _pct(bk["pullback_rate"]), _pct(lift[str(k)])))
    L += ["", "## 判伪样本（前 40，按 N日回落升序）", "",
          "| code | name | 行业 | 涨停%% | 资金 | 结构 | 社交 | %d日%% |" % args.n,
          "|---|---|---|---|---|---|---|---|"]
    for x in sorted(classes["fake"], key=lambda r: (r["cumret"].get(str(args.n)) or 0))[:40]:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            x["code"], x["name"], x["industry"], x["pct"], x["money"],
            x["structural"], x["social"], x["cumret"].get(str(args.n))))
    L += ["", "## 口径与局限", "",
          "- 资金维度=ts_moneyflow 快照（divergence 定义见 jev_flow）。",
          "- 结构维度=limit_list 的 open_times/limit_times/first_time（时间维度的离线代理，"
          "非 Δmin；真正的 Δmin 需 stk_mins+新闻时间戳，列入 unknown）。",
          "- 社交维度=social_YYYYMMDD.json（股吧人气+雪球四榜）；仅已采集日可用。",
          "- 三角定位要求 ≥2 可用维度且多数票判伪才归入「判伪」。",
          "- 回落=从涨停日次一交易日起累乘 N 日 pct_chg ≤ 0。",
          "- 样本仅限已采集资金快照的涨停日；扩样本加 --fetch-flow 或先补采集。", ""]
    out_md = REPORT_DIR / ("jev_validator_%s.md" % time.strftime("%Y%m%d"))
    out_md.write_text("\n".join(L), encoding="utf-8")

    print("[5/5] 完成", flush=True)
    fr = result["by_class"]["fake"][str(args.n)]
    bk = result["by_class"]["baseline_all_limit"][str(args.n)]
    print("  判伪 %d 日回落率 %.1f%%  vs 全涨停基线 %.1f%%  lift %+.1f%%" % (
        args.n, (fr["pullback_rate"] or 0) * 100, (bk["pullback_rate"] or 0) * 100,
        (lift[str(args.n)] or 0) * 100), flush=True)
    print("  报告:", out_json, "| 耗时 %.1fs" % (time.time() - t0), flush=True)


def _pct(v):
    return ("%.1f%%" % (v * 100)) if v is not None else "-"


if __name__ == "__main__":
    main()
