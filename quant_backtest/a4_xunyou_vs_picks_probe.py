# -*- coding: utf-8 -*-
"""
A4 荀攸池 vs 旧 picks 链 只读对照探针
=====================================
SCOPE = READONLY_CROSS_CHAIN_OBSERVATION_NOT_ADOPTION_EVIDENCE

目的：为门③合龙预研提供两条独立链路的名单差异面（只读对照，不做优劣评判，
不构成任何采纳/互替依据）。

数据源（均只读）：
  A. 荀攸池（新融合链，方案 C 预聚合）：
     quant_backtest/reports/xunyou_pool_20261019.json
     pool=[{symbol, action, score, confidence, ...}]，stats.buy_total=87
     来源 = kg_boost + chan_monitor 融合
  B. 旧 picks 链（chanlun 池 + 降级规则）：
     portal/data/picks_history/todays_YYYYMMDD.json（取存在的最新日期）
     stocks=[{code, tier, sources, ...}] 即当期 live 名单
     （影子标注 env_diff/q_toxic/q_first_buy 只标注不拦截，故 stocks
       全量即为归档生效名单；对照时附带 tier 分布供参考）

输出：
  - quant_backtest/reports/a4_xunyou_vs_picks_report.json
  - stdout 摘要

本脚本不写任何其他文件，不修改荀攸代码、归档文件或任何配置。
"""

import json
import os
import sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XUNYOU_POOL = os.path.join(BASE, "quant_backtest", "reports", "xunyou_pool_20261019.json")
PICKS_DIR = os.path.join(BASE, "portal", "data", "picks_history")
REPORT_OUT = os.path.join(BASE, "quant_backtest", "reports", "a4_xunyou_vs_picks_report.json")

SCOPE = "READONLY_CROSS_CHAIN_OBSERVATION_NOT_ADOPTION_EVIDENCE"
SAMPLE_LIMIT = 15


def load_xunyou_symbols():
    """荀攸池 buy 名单 symbol 集合。"""
    with open(XUNYOU_POOL, encoding="utf-8") as f:
        doc = json.load(f)
    pool = doc.get("pool", [])
    syms = sorted({p["symbol"] for p in pool if p.get("action") == "buy"})
    meta = {
        "agent": doc.get("agent"),
        "trade_date": doc.get("trade_date"),
        "generated_at": doc.get("generated_at"),
        "scope": doc.get("scope"),
        "stats": doc.get("stats"),
    }
    return syms, meta


def latest_picks_file():
    """取 picks_history 下存在的最新 todays_YYYYMMDD.json。"""
    cands = sorted(
        fn for fn in os.listdir(PICKS_DIR)
        if fn.startswith("todays_") and fn.endswith(".json")
    )
    if not cands:
        raise FileNotFoundError("picks_history 下无 todays_*.json 归档")
    return os.path.join(PICKS_DIR, cands[-1]), cands[-1]


def load_picks_symbols():
    """旧链最新归档 live 名单 symbol 集合（stocks 全量，附 tier 映射）。"""
    path, fname = latest_picks_file()
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    stocks = doc.get("stocks", [])
    tiers = {}
    for s in stocks:
        code = s.get("code")
        if code:
            tiers.setdefault(code, s.get("tier"))
    syms = sorted(tiers)
    meta = {
        "file": fname,
        "generated_at": doc.get("generated_at"),
        "scan_run_date": doc.get("scan_run_date"),
        "trade_basis": doc.get("trade_basis"),
        "counts_total": (doc.get("counts") or {}).get("total"),
        "tier_distribution": {
            t: sum(1 for v in tiers.values() if v == t) for t in sorted(set(tiers.values()))
        },
    }
    return syms, meta


def main():
    xun_syms, xun_meta = load_xunyou_symbols()
    old_syms, old_meta = load_picks_symbols()

    xun_set, old_set = set(xun_syms), set(old_syms)
    inter = sorted(xun_set & old_set)
    only_xun = sorted(xun_set - old_set)
    only_old = sorted(old_set - xun_set)

    overlap_rate_vs_xun = round(len(inter) / len(xun_set), 4) if xun_set else None
    overlap_rate_vs_old = round(len(inter) / len(old_set), 4) if old_set else None
    jaccard = round(len(inter) / len(xun_set | old_set), 4) if (xun_set | old_set) else None

    report = {
        "report_id": "a4_xunyou_vs_picks",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "SCOPE": SCOPE,
        "purpose": "门③合龙预研：荀攸池（新融合链）与旧 picks 链归档的名单差异面只读对照",
        "disclaimer": [
            "两链来源不同：旧链 = chanlun 池 + 降级规则；荀攸池 = kg_boost + chan_monitor 融合（方案 C 预聚合）。",
            "两链口径、触发逻辑与运行日期均不同，名单存在预期内差异；本对照不评判优劣。",
            "本报告仅为只读观察（NOT_ADOPTION_EVIDENCE），不构成采纳、互替或下线任一链的依据；两链不可直接互替。",
        ],
        "xunyou_pool": {
            "file": "quant_backtest/reports/xunyou_pool_20261019.json",
            "meta": xun_meta,
            "buy_symbol_count": len(xun_set),
        },
        "old_picks_chain": {
            "file": "portal/data/picks_history/" + old_meta["file"],
            "meta": old_meta,
            "live_symbol_count": len(old_set),
            "note": "live 名单 = 归档 stocks 全量（影子标注只标注不拦截）",
        },
        "comparison": {
            "intersection_count": len(inter),
            "only_xunyou_count": len(only_xun),
            "only_old_count": len(only_old),
            "intersection_samples": inter[:SAMPLE_LIMIT],
            "only_xunyou_samples": only_xun[:SAMPLE_LIMIT],
            "only_old_samples": only_old[:SAMPLE_LIMIT],
            "overlap_rate_vs_xunyou": overlap_rate_vs_xun,
            "overlap_rate_vs_old_picks": overlap_rate_vs_old,
            "jaccard": jaccard,
        },
    }

    os.makedirs(os.path.dirname(REPORT_OUT), exist_ok=True)
    with open(REPORT_OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # stdout 摘要
    print("=" * 64)
    print("A4 荀攸池 vs 旧 picks 链 只读对照 | SCOPE =", SCOPE)
    print("=" * 64)
    print(f"荀攸池 buy 名单 : {len(xun_set)} 只  (xunyou_pool_20261019.json, buy_total={xun_meta['stats'].get('buy_total')})")
    print(f"旧链 live 名单  : {len(old_set)} 只  ({old_meta['file']}, scan_run_date={old_meta['scan_run_date']})")
    print("-" * 64)
    print(f"交集            : {len(inter)}  重合率(占荀攸)={overlap_rate_vs_xun}  重合率(占旧链)={overlap_rate_vs_old}  Jaccard={jaccard}")
    print(f"仅荀攸          : {len(only_xun)}  样例: {', '.join(only_xun[:SAMPLE_LIMIT])}")
    print(f"仅旧链          : {len(only_old)}  样例: {', '.join(only_old[:SAMPLE_LIMIT])}")
    print("-" * 64)
    print("说明：两链来源不同（旧链=chanlun池+降级规则；荀攸池=kg_boost+chan_monitor 融合），")
    print("      名单差异属预期；本对照仅供门③合龙预研参考，不构成采纳/互替证据。")
    print(f"报告已写入: {REPORT_OUT}")


if __name__ == "__main__":
    sys.exit(main())
