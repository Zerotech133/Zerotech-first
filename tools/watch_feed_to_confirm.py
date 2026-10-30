# -*- coding: utf-8 -*-
"""WA1 feed → 人工确认（WO-WATCH-FEED）。只读池文件，只建 pending。"""
from __future__ import annotations

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from agent_platform import watch_feed_consumer as feed  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="WA1 signal_pool+chanlun_pool → watch trigger pending")
    ap.add_argument("--signals", default=feed.SIGNAL_POOL)
    ap.add_argument("--chanlun", default=feed.CHANLUN_POOL)
    ap.add_argument("--day", default=None, help="YYYY-MM-DD；缺省=今天")
    ap.add_argument("--max-age-days", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--data-dir", default=None)
    args = ap.parse_args(argv)
    res = feed.run(args.signals, args.chanlun, args.day, args.max_age_days,
                   args.dry_run, args.limit, args.data_dir)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
