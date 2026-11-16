# -*- coding: utf-8 -*-
"""chanlun_queue_refresh.py — 缠论采集队列覆盖刷新（只扩覆盖、不评级，2026-11-16）

背景：target_queue_chanlun.json 由策略线生成（grade/stage 是其评级产物），
本仓无生成器，asof 停在 2026-11-06 导致最新扫描轮买点票大量池外（判例 24/308）。

本工具的口径边界（CR-20261116-01）：
- 只把策略线**最新扫描轮**（pool 中最大 detect_date 当日）的**买点**标的
  追加进 ``all``，grade='pool'、stage='pool_scan'，显式声明"未评级"；
- ``actionable``、既有条目的 grade/stage 一律不动——评级归策略线；
- 幂等：重复运行不产生重复条目；已刷新过的文件跳过同轮增量。
"""
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_F = os.path.join(HERE, "target_queue_chanlun.json")
POOL_F = os.path.join(os.path.dirname(HERE), "quant_backtest", "data",
                      "chanlun_pool.jsonl")

REFRESH_MODE = "append_only_latest_round"


def load_pool_buy_rounds(pool_path=POOL_F):
    """读池，返回 (latest_detect_date, {code6后缀格式code: 最新一条买点记录})。

    只取 type 含 '买' 的记录；同 code 取 detect_date/dt 最新一条。
    """
    latest, by_code = "", {}
    with open(pool_path, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            if "买" not in (r.get("type") or ""):
                continue
            dd = r.get("detect_date") or ""
            if not dd:
                continue
            if dd > latest:
                latest = dd
            c = r.get("code") or ""
            if not c:
                continue
            old = by_code.get(c)
            if old is None or (dd, r.get("dt") or "") > (
                    old.get("detect_date") or "", old.get("dt") or ""):
                by_code[c] = r
    return latest, by_code


def build_refresh(queue, latest_round, by_code):
    """纯函数：返回 (new_queue, stats)。不改输入。

    规则：只并入 detect_date == latest_round 的买点标的（"当日买点票"口径，
    即 CR-20261116-01 文本里的 308 只基准）。更早轮次的取舍归策略线。
    """
    all_items = list(queue.get("all") or [])
    seen = {e.get("code") for e in all_items}
    added = []
    for c in sorted(by_code):
        if c in seen:
            continue
        r = by_code[c]
        if (r.get("detect_date") or "") != latest_round:
            continue
        added.append({
            "code": c,
            "name": r.get("name") or "",
            "grade": "pool",
            "type": r.get("type") or "",
            "stage": "pool_scan",
            "sig_dt": r.get("dt") or "",
            "detect_date": r.get("detect_date") or "",
            "first_seen": r.get("first_seen") or "",
        })
    new_q = dict(queue)
    new_q["all"] = all_items + added
    new_q["total"] = len(new_q["all"])
    new_q["coverage_asof"] = latest_round
    new_q["refreshed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    new_q["refresh_mode"] = REFRESH_MODE
    new_q["refresh_note"] = (
        "本席(马良/OSINT1)按CR-20261116-01追加最新扫描轮买点标的，"
        "grade=pool/stage=pool_scan 表示未评级；评级与 actionable 归策略线重出。")
    stats = {"n_before": len(all_items), "n_added": len(added),
             "n_after": len(new_q["all"]), "latest_round": latest_round,
             "actionable_kept": len(queue.get("actionable") or [])}
    return new_q, stats


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="缠论采集队列覆盖刷新（只扩覆盖不评级）")
    ap.add_argument("--dry", action="store_true", help="只算不写")
    ap.add_argument("--pool", default=POOL_F)
    ap.add_argument("--queue", default=QUEUE_F)
    a = ap.parse_args(argv)

    with open(a.queue, encoding="utf-8") as fh:
        queue = json.load(fh)
    latest, by_code = load_pool_buy_rounds(a.pool)
    if not latest:
        print("池中无买点记录，退出")
        return 2
    new_q, stats = build_refresh(queue, latest, by_code)
    already = queue.get("refresh_mode") == REFRESH_MODE and \
        queue.get("coverage_asof") == latest
    print("latest_round=%s  %d -> %d (+%d)  actionable保留=%d%s" % (
        stats["latest_round"], stats["n_before"], stats["n_after"],
        stats["n_added"], stats["actionable_kept"],
        "  [本轮已刷新过，幂等跳过写入]" if already else ""))
    if a.dry or already:
        return 0
    bak = a.queue + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(a.queue, bak)
    tmp = a.queue + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(new_q, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, a.queue)
    print("written: %s  (backup: %s)" % (a.queue, os.path.basename(bak)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
