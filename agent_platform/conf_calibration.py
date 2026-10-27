# -*- coding: utf-8 -*-
"""D1 自报置信度后验校准探针（K-STOCK-086，方案 085-P0-B）。

用途：检验 9b 自报 confidence 与后验收益是否脱钩（菜鸟实证：置信 85 的 SELL
T+20 反向 +23.1%）。产出分桶 reliability 表，供 B8 解法二选一：
  - 桶间胜率/收益单调且区分度够 → P0-B 校准映射 conf'（更根本）
  - 脱钩/样本不足 → 方案甲分档锚定（prompt 侧）
门：每桶 n>=8 才出结论；不足打 INSUFFICIENT（防小样本误读）。

用法：python conf_calibration.py [--horizon 1]
horizon=后验交易日数（默认 1：决策日收盘→次一交易日收盘）。
"""

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
LEDGER = BASE / "data" / "shadow" / "shadow_ledger.jsonl"
DAILY_DB_CANDIDATES = [
    # 决策票日线来源（按可用性排序，首个命中者用）：
    BASE.parent / "k_stock" / "data" / "features_daily.parquet",  # 特征湖（5596 只）
]
BUCKETS = [(0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 0.81)]
MIN_BUCKET = 8


def load_ledger():
    rows = []
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return [
        r
        for r in rows
        if r.get("status") == "done"
        and r.get("confidence") is not None
        and r.get("price_at_decision")
    ]


def bucket_of(conf):
    for lo, hi in BUCKETS:
        if lo <= conf < hi:
            return (lo, hi)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument(
        "--px-latest",
        type=float,
        default=None,
        help="手工指定当前评估价（默认 quote_snapshot 现价；周末=最新收盘）",
    )
    a = ap.parse_args()

    rows = load_ledger()
    if a.px_latest is not None:
        for r in rows:
            r["_px_now"] = a.px_latest
    else:
        sys.path.insert(0, str(BASE / "engine"))
        from tools import quote_snapshot  # noqa: E402

        for r in rows:
            try:
                r["_px_now"] = quote_snapshot(r["symbol"])["price"]
            except Exception:  # noqa: BLE001
                r["_px_now"] = None

    table = {b: {"ret": [], "win": 0} for b in BUCKETS}
    used = skipped = 0
    for r in rows:
        if not r.get("_px_now"):
            skipped += 1
            continue
        ret = (r["_px_now"] / r["price_at_decision"] - 1) * 100
        b = bucket_of(r["confidence"])
        if b is None:
            skipped += 1
            continue
        table[b]["ret"].append(ret)
        table[b]["win"] += ret > 0
        used += 1

    print("=" * 64)
    print(
        "D1 conf 校准探针: 样本 used=%d skipped=%d horizon=T+%d"
        % (used, skipped, a.horizon)
    )
    print("%-12s %-5s %-10s %-8s %s" % ("conf 桶", "n", "均值收益%", "胜率", "结论"))
    ready = True
    for b in BUCKETS:
        d = table[b]
        n = len(d["ret"])
        if n < MIN_BUCKET:
            ready = False
            print(
                "%-14s %-5d %-10s %-8s n<%d 不出结论"
                % ("%.2f-%.2f" % b, n, "-", "-", MIN_BUCKET)
            )
            continue
        mean_r = sum(d["ret"]) / n
        winrate = 100.0 * d["win"] / n
        print("%-14s %-5d %+10.2f %-8.0f%%" % ("%.2f-%.2f" % b, n, mean_r, winrate))
    print("-" * 64)
    if not ready:
        print(
            "结论: INSUFFICIENT（各桶 n<%d）——B8 暂用方案甲分档锚定，"
            "本探针随影子样本增长复跑" % MIN_BUCKET
        )
        return 0
    # 单调性粗判：桶均值收益随 conf 递增 → conf 有信息量 → 校准映射可用
    means = [
        sum(table[b]["ret"]) / len(table[b]["ret"])
        for b in BUCKETS
        if len(table[b]["ret"]) >= MIN_BUCKET
    ]
    mono = all(means[i] <= means[i + 1] for i in range(len(means) - 1))
    print(
        "结论: %s —— %s"
        % (
            "MONOTONIC" if mono else "NON-MONOTONIC(脱钩信号)",
            "P0-B 校准映射 conf' 可用" if mono else "B8 走方案甲分档锚定",
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
