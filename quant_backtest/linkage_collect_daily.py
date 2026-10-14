# -*- coding: utf-8 -*-
"""K35 三线联动 · 观察池每日影子采集（2026-10-14 linkage-dev，产线只读）。

观察池 = 三路并集（去重口径：tushare 代码 600519.SH 精确去重）：
  1. 持仓账本 positions.json（runner.py 账本）pending+open 状态的 CN 票
     —— 数据源：本目录 positions.json，market=="CN"；
  2. runner 的 watchlist —— 数据源：../news_collector/news_config.json
     的 watchlist，CN 归一化逻辑与 runner.resolve_watchlist 同款
     （sh/sz/bj 前缀 → .SH/.SZ/.BJ）；
  3. cross 每日 TopN —— 现无落盘产物，本版跳过（见留痕文档遗留事项）。

行为：
  · 逐票调 linkage_collector._judge_and_collect(code, ["1d"])
    （1d 周期即可，控制量；默认上限 --max 60 票/次）；
  · 幂等：collector 按 ref_date+code+period+signal 去重，重复跑零副作用；
  · 观察池为空 → 静默退出 0（不打日志不报错）；
  · 单票失败 try/except 跳过，不影响其余票。

用法:
  python linkage_collect_daily.py                # 全池采集
  python linkage_collect_daily.py --max 30       # 控制单次票数
  python linkage_collect_daily.py --dry-run      # 只列观察池不采集
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

POSITIONS = os.path.join(HERE, "positions.json")
NEWS_CONFIG = os.path.abspath(
    os.path.join(HERE, "..", "news_collector", "news_config.json"))
PERIODS = ["1d"]


def pool_positions():
    """positions.json pending+open 的 CN 票。"""
    out = []
    try:
        with open(POSITIONS, "r", encoding="utf-8") as f:
            led = json.load(f)
    except Exception:
        return out
    for k in ("pending", "open"):
        for p in led.get(k) or []:
            if p.get("market") == "CN" and p.get("symbol"):
                out.append((str(p["symbol"]), p.get("name") or ""))
    return out


def pool_watchlist():
    """news_config.json watchlist 的 CN 票（runner.resolve_watchlist 同款）。"""
    out = []
    try:
        with open(NEWS_CONFIG, "r", encoding="utf-8") as f:
            wl = json.load(f).get("watchlist", [])
    except Exception:
        return out
    for w in wl:
        c = str(w.get("code", "")).lower()
        nm = w.get("name", "")
        if c.startswith("sh"):
            out.append((c[2:] + ".SH", nm))
        elif c.startswith("sz") or c.startswith("bj"):
            out.append((c[2:] + "." + c[:2].upper(), nm))
    return out


def build_pool():
    """三路并集去重（保序：持仓优先，其次 watchlist）。返回 [(code,name)]。"""
    pool, seen = [], set()
    for code, name in pool_positions() + pool_watchlist():
        if code not in seen:
            seen.add(code)
            pool.append((code, name))
    return pool


def main():
    ap = argparse.ArgumentParser(description="观察池每日影子采集")
    ap.add_argument("--max", type=int, default=60, help="单次采集票数上限")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    pool = build_pool()[:max(0, a.max)]
    if not pool:
        return 0                              # 观察池为空：静默退出
    if a.dry_run:
        for code, name in pool:
            print("POOL %s %s" % (code, name))
        print("POOL total=%d (dry-run, no collect)" % len(pool))
        return 0

    from linkage_collector import _judge_and_collect
    n_rec, n_ok, n_fail = 0, 0, 0
    for code, name in pool:
        try:
            _, recs = _judge_and_collect(code, PERIODS)
            n_rec += len(recs)
            n_ok += 1
            if recs:
                print("COLLECT %s %s +%d" % (code, name, len(recs)))
        except Exception as e:
            n_fail += 1
            print("FAIL %s %s: %s" % (code, name, e))
    print("DONE pool=%d ok=%d fail=%d new_records=%d" % (
        len(pool), n_ok, n_fail, n_rec))
    return 0 if n_fail < len(pool) else 1


if __name__ == "__main__":
    raise SystemExit(main())
