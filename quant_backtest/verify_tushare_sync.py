# -*- coding: utf-8 -*-
"""tushare 全量同步完整性核验（anns_d 死循环修复后首次全量跑批用）。

核验项：
  1. anns_d 日文件数 vs trade_cal 开放交易日数（本次修复的重点）
  2. 全表文件数快照（对照 2026-09-27 基线，异常缺失即报）
  3. anns_d 抽检 5 个文件：可读、行数>0、字段完整
  4. 幂等：可重复执行，只读不写

用法：
  python verify_tushare_sync.py          # anns_d 未跑完时会给 WARNING 不 FAIL
"""
import glob
import os
import random
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "tushare")

# 2026-09-27 同步基线（anns_d 为本次修复后重跑，预期跑满交易日历）
BASELINE = {
    "bak_basic": 5569, "cb_daily": 1171, "etf_mins": 740,
    "idx_mins": 2010, "stk_mins": 11570, "sw_mins": 2139,
}


def trade_open_days():
    """开放交易日（与 tushare_sync.py anns_d 窗口一致：20190101 起）。"""
    ps = sorted(glob.glob(os.path.join(OUT, "trade_cal", "part_*.parquet")))
    if not ps:
        return None
    cal = pd.concat([pd.read_parquet(p) for p in ps])
    days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    today = pd.Timestamp.now().strftime("%Y%m%d")
    return [d for d in days if d >= "20190101" and d <= today]


def test_anns_coverage():
    print("=== 1. anns_d 覆盖率 vs 交易日历 ===")
    days = trade_open_days()
    if days is None:
        print("  trade_cal 缺失，跳过")
        return None
    have = set(os.path.basename(p)[2:-8]
               for p in glob.glob(os.path.join(OUT, "anns_d", "d_*.parquet")))
    missing = [d for d in days if d not in have]
    extra = sorted(have - set(days))
    print("  开放交易日: %d, 已落盘: %d, 缺失: %d, 多余: %d"
          % (len(days), len(have), len(missing), len(extra)))
    if missing:
        print("  缺失样例: %s ... %s" % (missing[:3], missing[-3:]))
    ok = (len(missing) == 0 and not extra)
    print("  结果: %s" % ("PASS" if ok else
                          "WARN(未跑完)" if len(have) < len(days) else "FAIL"))
    return ok if len(have) >= len(days) else None


def test_baseline_counts():
    print("\n=== 2. 基线表文件数 ===")
    ok = True
    for tbl, expect in BASELINE.items():
        n = len(glob.glob(os.path.join(OUT, tbl, "*.parquet")))
        # 逐码表允许增量（新增标的），不少于基线即正常
        good = n >= expect
        print("  %-12s %6d (基线 %d) %s"
              % (tbl, n, expect, "OK" if good else "MISSING"))
        ok = ok and good
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    return ok


def test_anns_sample():
    print("\n=== 3. anns_d 抽检（5 文件）===")
    ps = glob.glob(os.path.join(OUT, "anns_d", "d_*.parquet"))
    if not ps:
        print("  无文件可抽检")
        return False
    random.seed(1)
    ok = True
    for p in random.sample(ps, min(5, len(ps))):
        try:
            df = pd.read_parquet(p)
            if len(df) == 0:
                print("  EMPTY: %s" % os.path.basename(p))
                ok = False
            elif "ann_date" not in df.columns:
                print("  NO ann_date col: %s cols=%s"
                      % (os.path.basename(p), list(df.columns)[:6]))
                ok = False
            else:
                print("  %-22s %6d rows" % (os.path.basename(p), len(df)))
        except Exception as e:
            print("  UNREADABLE: %s (%s)" % (os.path.basename(p), str(e)[:60]))
            ok = False
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    return ok


def main():
    results = [
        ("anns_d 覆盖率", test_anns_coverage()),
        ("基线表文件数", test_baseline_counts()),
        ("anns_d 抽检", test_anns_sample()),
    ]
    print("\n" + "=" * 50)
    passed = sum(1 for _, r in results if r is True)
    warned = sum(1 for _, r in results if r is None)
    for name, r in results:
        tag = "PASS" if r is True else "WARN" if r is None else "FAIL"
        print("  [%s] %s" % (tag, name))
    print("  %d/%d PASS, %d WARN" % (passed, len(results), warned))
    return 0 if warned == 0 and passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
