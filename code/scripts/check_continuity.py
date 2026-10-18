"""数据连续性检查 + 缺口回填 CLI。

用法：
    python scripts/check_continuity.py                          # 全市场，仅报告
    python scripts/check_continuity.py --market CN --since 2025-01-01
    python scripts/check_continuity.py --market HK --repair      # 检查并回填
    python scripts/check_continuity.py --repair --max-symbols 200

源无数据的停牌日会在回填后自动归入 suspended（不反复重试）。
"""
from __future__ import annotations

import argparse
from datetime import date

from zhice_os_v2.infrastructure.market_data.continuity import (
    backfill_daily_gaps,
    find_daily_gaps,
    summarize_gaps,
)

MARKETS = ("CN", "US", "HK", "CRYPTO")


def run(market: str, start: date, end: date, repair: bool, max_symbols: int | None) -> None:
    gaps = find_daily_gaps(market, start, end)
    s = summarize_gaps(gaps, top=10)
    print(f"\n=== {market}  {start}~{end} ===")
    print(f"  有缺口标的={s['symbols_with_gaps']}  缺口总天数={s['total_missing']}")
    for t in s["top"]:
        print(f"    {t['symbol']:14} 缺 {t['missing_days']:4} 天  {t['first']}~{t['last']}")
    if repair and gaps:
        print("  回填中…")
        rep = backfill_daily_gaps(market, gaps, max_symbols=max_symbols)
        print(f"  回填结果: 处理标的={rep['symbols']} 写入={rep['written']} "
              f"已补标的={rep['filled_symbols']} 残余(停牌)标的={rep['suspended_symbols']} "
              f"残余天数={rep['suspended_days']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="all", choices=("all", *MARKETS))
    ap.add_argument("--since", default="2015-01-01")
    ap.add_argument("--until", default=date.today().isoformat())
    ap.add_argument("--repair", action="store_true")
    ap.add_argument("--max-symbols", type=int, default=None)
    args = ap.parse_args()

    start, end = date.fromisoformat(args.since), date.fromisoformat(args.until)
    markets = MARKETS if args.market == "all" else (args.market,)
    for m in markets:
        run(m, start, end, args.repair, args.max_symbols)


if __name__ == "__main__":
    main()
