# scripts/backfill_daily_history.py
"""一次性/可重入日线历史回补。用法：
  PYTHONPATH=src python scripts/backfill_daily_history.py --market US --days 365 [--max-symbols 400] [--throttle-ms 800]
已覆盖(≥days 历史)的标的自动跳过；不带 --max-symbols 时循环跑到游标回绕(全覆盖)。"""
from __future__ import annotations

import argparse

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.market_data.backfill import backfill_daily_history


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--market", required=True)
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--max-symbols", type=int, default=None)
    p.add_argument("--throttle-ms", type=int, default=None)
    a = p.parse_args()
    s = get_settings()
    total = {"processed": 0, "written": 0, "skipped": 0, "errors": 0}
    while True:
        r = backfill_daily_history(
            a.market, days=a.days or s.backfill_daily_days,
            slice_size=400, batch_size=s.fetch_daily_batch_size,
            throttle_ms=a.throttle_ms if a.throttle_ms is not None else max(s.fetch_daily_throttle_ms, 500),
            max_retries=s.fetch_backoff_max_retries, base_ms=s.fetch_backoff_base_ms,
            max_symbols=a.max_symbols,
        )
        for k in total:
            total[k] += r[k]
        print("batch", r, "| total", total)
        if a.max_symbols or r["cursor"] == 0:  # 指定 max_symbols=只跑一批；否则回绕即全跑完
            break
    print("DONE", total)


if __name__ == "__main__":
    main()
