# -*- coding: utf-8 -*-
"""策略源健康度分析器（影子模式）。

只读分析 paper.db 的 orders 表，按 strategy 维度打分并产出路由建议。
本模块不接 automation、不写 paper.db，仅落盘建议文件 strategist_latest.json。
"""
import argparse
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "paper.db")
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "strategist_latest.json")

SHADOW_DECLARATION = {
    "mode": "shadow",
    "note": "本文件仅为建议，automation 未接线，不产生任何调度效果",
}

GRADES = [
    (85, "加权重"),
    (70, "维持"),
    (50, "降权"),
]


def _connect(db_path, readonly=True):
    if readonly:
        uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
        return sqlite3.connect(uri, uri=True)
    return sqlite3.connect(db_path)


def _grade(score):
    for floor, sugg in GRADES:
        if score >= floor:
            return sugg
    return "观察"


def _score_source(rows, now=None):
    """rows: [(status, reason)] 该源近 N 天落单。返回 metrics dict 或 None（无单）。"""
    if not rows:
        return None
    total = len(rows)
    n_filled = sum(1 for s, _ in rows if s == "filled")
    n_rejected = sum(1 for s, _ in rows if s == "rejected")
    n_cancelled = sum(1 for s, _ in rows if s == "cancelled")
    n_stale_block = sum(1 for _, r in rows if r and "陈旧产物" in r)
    n_superseded = sum(1 for _, r in rows if r and "superseded" in r)
    guard_hits = sum(1 for _, r in rows
                     if r and any(k in r for k in ("superseded", "陈旧", "涨停")))

    freshness_ratio = n_stale_block / total
    match_rate = n_filled / (n_filled + n_rejected) if (n_filled + n_rejected) else 1.0
    cancel_ratio = n_superseded / total
    guard_ratio = guard_hits / total

    score = 100.0
    score -= 30 * freshness_ratio
    score -= 30 * (1 - match_rate)
    score -= 20 * cancel_ratio
    score -= 20 * guard_ratio
    score = round(score, 1)

    reasons = []
    if freshness_ratio > 0:
        reasons.append(f"有 {n_stale_block} 单被陈旧产物拦截")
    if match_rate < 1.0:
        reasons.append(f"撮合率 {match_rate:.0%}")
    if cancel_ratio > 0:
        reasons.append(f"撤销率 {cancel_ratio:.0%}")
    if guard_ratio > 0:
        reasons.append(f"干预占比 {guard_ratio:.0%}")
    if not reasons:
        reasons.append("各项指标健康")

    return {
        "score": score,
        "grade": _grade(score),
        "metrics": {
            "total_orders": total,
            "filled": n_filled,
            "rejected": n_rejected,
            "cancelled": n_cancelled,
            "stale_block_ratio": round(freshness_ratio, 4),
            "match_rate": round(match_rate, 4),
            "superseded_ratio": round(cancel_ratio, 4),
            "guard_ratio": round(guard_ratio, 4),
        },
        "suggestion": _grade(score) + "：" + "、".join(reasons),
    }


def review(db_path=None, days=7, now=None):
    db_path = db_path or DEFAULT_DB
    if now is None:
        now = datetime.now()
    elif isinstance(now, str):
        now = datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    con = _connect(db_path)
    try:
        cur = con.execute(
            "SELECT strategy, status, reason FROM orders "
            "WHERE created_at >= ? AND strategy != 'manual'", (cutoff,))
        by_source = {}
        for strategy, status, reason in cur:
            by_source.setdefault(strategy, []).append((status, reason or ""))
    finally:
        con.close()

    sources = {}
    for strategy in sorted(by_source):
        result = _score_source(by_source[strategy])
        if result is not None:
            sources[strategy] = result

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "sources": sources,
        **SHADOW_DECLARATION,
    }


def save_report(report, path=None):
    path = path or OUTPUT_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子写：tmp + rename
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="策略源健康度分析（影子模式）")
    parser.add_argument("--days", type=int, default=7, help="统计窗口天数")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不落盘")
    args = parser.parse_args(argv)

    report = review(days=args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    path = save_report(report)
    print(f"影子模式报告已写入: {path}")
    for name, info in report["sources"].items():
        print(f"  {name:12s} score={info['score']:5.1f}  {info['suggestion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
