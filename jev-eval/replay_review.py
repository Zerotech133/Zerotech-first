#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史回放对比：对 2026-07~09 生产期历史候选池跑 Jev 复核 → 与全量基线比收益。

目的（用户需求：今天出"接 Jev 前后"对比数字）：
  基线 = 历史候选池全量 5 日收益（= 当时"没接 Jev"的实际水平）
  实验组 = Jev 复核 PASS 子集 5 日收益
  REJECT/HUMAN 组 = Jev 想拦/想人工复核的子集
  若 PASS 显著跑赢基线且 REJECT 跑输 → Jev 复核门有真实增益

防未来函数（回放有效性核心）：
  公告窗口   pool_date-14d ~ pool_date（不用 today）
  持仓       opened_at <= pool_date 的未平仓（模拟当时持仓，不用当前持仓）
  regime     pool_date 之前的指数 5 日（按交易日缓存，池内共享）
  收益       pool_date 收盘 → 第 5 个交易日收盘（v2_bar_daily）

成本：~1552 候选 + ~45 regime 日级判断 ≈ 1600 次 Jev 调用 ≈ $0.4，约 35-45 分钟。

用法：
  python replay_review.py                # 全量回放（CN+US+HK+CRYPTO）
  python replay_review.py --market CN    # 单市场
  python replay_review.py --max-pools 10 # 限量试跑
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from signal_review_gate import (  # noqa: E402  复用判定层
    API_KEY, BASE_URL, QUESTIONS, TUSHARE_TOKEN, jev_ask, psql,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
OUT = HERE / "replay"
OUT.mkdir(exist_ok=True)

N_OUTCOME_DAYS = 5
ANN_DAYS = 14
REPLAY_END = "2026-09-15"   # 只回放生产期（9/16 起属于部署后，基线口径另算）


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── as-of 数据组装（防未来函数） ────────────────────────────
def fetch_pools(market: str | None, max_pools: int) -> list[dict]:
    where = f"pool_date <= '{REPLAY_END}'" + (f" AND market='{market}'" if market else "")
    rows = psql(
        "SELECT pool_id, pool_date, market FROM v2_candidate_pool "
        f"WHERE {where} AND candidate_count > 0 "
        "ORDER BY pool_date ASC, id ASC LIMIT %d" % max_pools
    )
    return [{"pool_id": r[0], "pool_date": r[1], "market": r[2]}
            for r in rows if len(r) == 3]


def fetch_cands(pool_id: str) -> list[dict]:
    rows = psql(
        "SELECT candidate_id, ts_code, market, coalesce(candidate_score,0), "
        "coalesce(candidate_reason,'') FROM v2_candidate_asset "
        f"WHERE pool_id='{pool_id}' ORDER BY candidate_score DESC NULLS LAST LIMIT 20"
    )
    return [{"candidate_id": r[0], "ts_code": r[1], "market": r[2],
             "score": float(r[3] or 0), "reason": r[4]} for r in rows if len(r) == 5]


def fetch_held_asof(pool_date: str) -> list[str]:
    """pool_date 当日未平仓持仓（模拟时点持仓）。"""
    rows = psql(
        "SELECT DISTINCT ts_code FROM v2_position "
        f"WHERE market='CN' AND opened_at::date <= '{pool_date}' "
        f"AND (closed_at IS NULL OR closed_at::date > '{pool_date}')"
    )
    return [r[0] for r in rows if r]


def fetch_concepts_sym(ts_code: str) -> list[str]:
    rows = psql(
        "SELECT c.concept_name FROM v2_concept_membership m "
        "JOIN v2_concept c ON c.id=m.concept_id "
        f"WHERE m.symbol='{ts_code}' LIMIT 10"
    )
    return [r[0] for r in rows if r]


def anns_asof(ts_code: str, pool_date: str) -> list[str] | None:
    """公告窗口 [pool_date-14d, pool_date]，CN only。"""
    if not TUSHARE_TOKEN:
        return None
    try:
        import tushare as ts
        pro = ts.pro_api(TUSHARE_TOKEN)
        end = date.fromisoformat(pool_date)
        start = end - timedelta(days=ANN_DAYS)
        df = pro.anns_d(ts_code=ts_code, start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"), src="沪深京")
        return (df["title"].astype(str).tolist()
                if df is not None and "title" in df else [])[:15]
    except Exception:  # noqa: BLE001
        return None


_regime_day_cache: dict[str, float] = {}


def regime_asof(pool_date: str) -> float:
    """按交易日缓存的大盘判断（指数只用 pool_date 之前数据）。失败→1.0 放行。"""
    if pool_date in _regime_day_cache:
        return _regime_day_cache[pool_date]
    p = 1.0
    try:
        idx = psql(
            "SELECT trade_date, close FROM v2_bar_daily "
            "WHERE symbol IN ('000001.SH','sh000001') AND market='CN' "
            f"AND trade_date < '{pool_date}' ORDER BY trade_date DESC LIMIT 5"
        )
        if idx:
            txt = "; ".join(f"{r[0]} 收盘 {float(r[1]):.0f}" for r in idx)
            chg = ""
            if len(idx) >= 2:
                chg = f"（近{len(idx)-1}个交易日累计 {(float(idx[0][1])/float(idx[-1][1])-1):+.1%}）"
            state = f"日期 {pool_date}。上证指数近5个交易日: {txt}{chg}。"
            ans = jev_ask(state, {"regime_fit": {
                "type": "noul",
                "instructions": "当前 A 股大盘环境整体适合执行新买入信号"
                                "（无系统性风险事件主导、消息面未显著恶化）。"}})
            p = float(ans.get("regime_fit", {}).get("noul", 1.0))
    except Exception as e:  # noqa: BLE001
        log(f"  regime({pool_date}) 失败降级: {str(e)[:80]}")
    _regime_day_cache[pool_date] = p
    return p


def build_state_asof(cand: dict, held: list[str], anns: list[str] | None) -> str:
    concepts = fetch_concepts_sym(cand["ts_code"]) if cand["market"] == "CN" else []
    overlap = []
    if concepts and held:
        rows = psql(
            "SELECT DISTINCT c.concept_name FROM v2_concept_membership m "
            "JOIN v2_concept c ON c.id=m.concept_id WHERE m.symbol = ANY(%s)",
            (held,))
        hs = {r[0] for r in rows if r}
        overlap = [c for c in concepts if c in hs]
    parts = [
        f"候选: {cand['ts_code']} 市场 {cand['market']} (策略评分 {cand['score']:.3f})",
        f"信号理由: {cand['reason'][:200]}",
        f"所属概念: {', '.join(concepts[:6]) or ('仅A股提供' if cand['market'] != 'CN' else '未知')}",
        f"现有持仓 {len(held)} 只: {', '.join(held[:8])}",
        f"与持仓重叠概念: {', '.join(overlap[:4]) or '无'}",
    ]
    if anns is None:
        parts.append("近期公告: 数据源不可用（无法核验）")
    else:
        parts.append("近期公告标题: " + ("; ".join(anns[:8]) if anns else "近14天无公告"))
    return "\n".join(parts)


def decide(news_p, overlap_p, quality, quality_p, crowd, crowd_p, regime_p):
    """与 signal_review_gate 影子决策 v2 保持一致（2026-09-22 回放反转后规则）。"""
    if news_p >= 0.7:
        return "REJECT", f"news_risk={news_p:.2f}"
    if crowd == "hot" and crowd_p >= 0.7:
        return "REJECT", f"crowding=hot({crowd_p:.2f})"
    if quality == "low":
        return "FOCUS", f"quality=low({quality_p:.2f})"
    if overlap_p >= 0.7:
        return "WATCH", f"overlap={overlap_p:.2f}"
    return "PASS", "regular"


# ── 收益回填（回放池早已过 5 日窗口，可立即回填） ──────────
def outcome_5d(ts_code: str, market: str, pool_date: str) -> dict | None:
    rows = psql(
        "SELECT trade_date, close FROM v2_bar_daily "
        f"WHERE symbol='{ts_code}' AND market='{market}' "
        f"AND trade_date >= '{pool_date}' ORDER BY trade_date ASC LIMIT %d"
        % (N_OUTCOME_DAYS + 1))
    if len(rows) < N_OUTCOME_DAYS + 1:
        return None
    entry, exit_ = float(rows[0][1]), float(rows[N_OUTCOME_DAYS][1])
    if entry <= 0:
        return None
    return {"ret_5d": (exit_ - entry) / entry, "entry": entry, "exit": exit_}


# ── 主流程 ──────────────────────────────────────────────────
def run(market: str | None, max_pools: int) -> None:
    if not API_KEY:
        sys.exit("需要 JEV_API_KEY（先 . jev-gate.env.ps1）")
    pools = fetch_pools(market, max_pools)
    log(f"回放池 {len(pools)} 个（{pools[0]['pool_date']}~{pools[-1]['pool_date']}）"
        if pools else "无池")
    results = []
    tushare_cn = bool(TUSHARE_TOKEN)
    for i, pool in enumerate(pools, 1):
        regime_p = regime_asof(pool["pool_date"])
        held = fetch_held_asof(pool["pool_date"])
        cands = fetch_cands(pool["pool_id"])
        for c in cands:
            anns = (anns_asof(c["ts_code"], pool["pool_date"])
                    if tushare_cn and c["market"] == "CN" else None)
            state = build_state_asof(c, held, anns)
            try:
                ans = jev_ask(state, QUESTIONS)
            except Exception as e:  # noqa: BLE001
                log(f"  {c['ts_code']} Jev 失败跳过: {str(e)[:60]}")
                time.sleep(1)
                continue
            news_p = float(ans.get("news_risk", {}).get("noul", 0.0))
            overlap_p = float(ans.get("concept_overlap", {}).get("noul", 0.0))
            quality = ans.get("signal_quality", {}).get("choice", "unknown")
            quality_p = (ans.get("signal_quality", {}).get("probabilities") or {}).get(quality, 0.0)
            crowd = ans.get("crowding", {}).get("choice", "unknown")
            crowd_p = (ans.get("crowding", {}).get("probabilities") or {}).get(crowd, 0.0)
            d, why = decide(news_p, overlap_p, quality, quality_p, crowd, crowd_p, regime_p)
            o = outcome_5d(c["ts_code"], c["market"], pool["pool_date"])
            results.append({
                "pool_date": pool["pool_date"], "market": c["market"],
                "ts_code": c["ts_code"], "score": c["score"],
                "decision": d, "why": why,
                "news_p": news_p, "quality": quality, "crowd": crowd,
                "regime_p": regime_p,
                **(o if o else {"ret_5d": None}),
            })
            time.sleep(0.25)
        log(f"[{i}/{len(pools)}] {pool['pool_date']} {pool['market']}: "
            f"{len(cands)} 条 regime={regime_p:.2f}")

    out_file = OUT / f"replay-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    log(f"结果落盘 {out_file}（{len(results)} 条）")
    report(results)


def report(results: list[dict]) -> None:
    valid = [r for r in results if r.get("ret_5d") is not None]
    if not valid:
        log("无有效收益样本（检查 v2_bar_daily 覆盖）")
        return

    def stat(rs: list[dict]) -> tuple:
        rets = sorted(x["ret_5d"] for x in rs)
        n = len(rs)
        win = sum(1 for x in rs if x["ret_5d"] > 0)
        return n, win / n, sum(rets) / n, rets[n // 2]

    print("\n========= Jev 接入前 vs 后 · 历史回放对比 =========")
    print(f"{'组':<10}{'样本':<7}{'胜率':<9}{'均值5日收益':<13}{'中位数'}")
    base_n, base_win, base_avg, base_med = stat(valid)
    print(f"{'基线(全量)':<11}{base_n:<7}{base_win:<9.1%}{base_avg:<13.2%}{base_med:.2%}")
    groups = defaultdict(list)
    for r in valid:
        groups[r["decision"]].append(r)
    stats = {}
    for d in ("FOCUS", "PASS", "WATCH", "REJECT"):
        if groups.get(d):
            n, w, a, m = stat(groups[d])
            stats[d] = a
            print(f"{d:<11}{n:<7}{w:<9.1%}{a:<13.2%}{m:.2%}")
    if "FOCUS" in stats:
        print(f"\nFOCUS - 基线: {stats['FOCUS']-base_avg:+.2%}  （低置信加分规则增量）")
    if "PASS" in stats:
        print(f"PASS - 基线: {stats['PASS']-base_avg:+.2%}")
    if "FOCUS" in stats and "REJECT" in stats:
        print(f"PASS - REJECT: {stats['PASS']-stats['REJECT']:+.2%}")
    print("判读：PASS 跑赢基线 ≥1% 且 REJECT 跑输 → 复核门有真实增益，阶段 2 有据")
    # 分市场明细
    print("\n-- 分市场（PASS 增量）--")
    for mk in ("CN", "US", "HK", "CRYPTO"):
        g = [r for r in valid if r["market"] == mk]
        gp = [r for r in g if r["decision"] == "PASS"]
        if g and gp:
            ga = sum(x["ret_5d"] for x in gp) / len(gp)
            ba = sum(x["ret_5d"] for x in g) / len(g)
            print(f"{mk:<8} 样本 {len(g):<5} PASS {len(gp):<5} 增量 {ga-ba:+.2%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=None)
    ap.add_argument("--max-pools", type=int, default=300)
    a = ap.parse_args()
    run(a.market, a.max_pools)


if __name__ == "__main__":
    main()
