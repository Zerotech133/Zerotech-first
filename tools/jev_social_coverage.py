# -*- coding: utf-8 -*-
"""JEV P2.3 覆盖率表 · 唯一入口（只读 / 离线 / 纯统计）

## A 方案落地（老田 2026-11-10 批准）

护栏纯函数已抽到 `portal/jev_snapshot_gate.py`，由**页面侧与研究侧共享同一套判定**：
- 页面侧 `jev_social.load_social()` / `jev_graph` **不接护栏**，行为原样保留，仅在 docstring 标注口径；
- **P2.3 覆盖率表（本脚本）** 与 **P5.3/P5.4 校准链路**（`jev_snapshot_gate.require_compliant_social`）
  必须走合规判定，非合规快照绝不进分子 / 分母；
- P5.1 / P5.2 历史结论与数字不改，仅标注口径。

## 数据契约

`jev_social_fetch.py` 三个源都不接收日期：`--day` 只决定「给哪批票拉帖子」，
榜单与帖子数值永远是「查询时刻」的实时值。因此：

- `day`         = 【选票日】（用了哪天的涨停 / 焦点板块清单）
- `fetched_at`  = 【观测时刻】（榜单 / 帖子真正反映的是这一刻）

## 纪律

- 只读 `portal/social_*.json`，不联网、不采集、不改采集器、不改计划任务；
- 默认不写盘，仅显式 `--out` 才落 JSON；
- 输出恒带 `coverage_only=True / gate_eligible=False / calibrated=False`：
  覆盖率是数据可用性指标，不是判定闸结论（P5.1 §八 三硬伤未解）。

## 覆盖率口径（两档必须同时给出，禁止挑好看的报）

- 分母 = 快照 `codes` 选票池（6 位代码去重），**不是** `by_code` 全量
  （`by_code` 混入了雪球四榜 + 股吧榜约 300 只非目标票）；
- 分子 A `listed` = 分母中出现在任一榜单的票数；
- 分子 B `posts`  = 分母中真正拉到帖子的票数（更严的一档）；
- 分母为 0 记 `None`，不硬算 0%。

用法：
    python tools/jev_social_coverage.py
    python tools/jev_social_coverage.py --after-close 18:00 --out tmp/p23_coverage.json
    python tools/jev_social_coverage.py --require-day YYYYMMDD   # 校准链路准入检查
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
PORTAL = os.path.join(STATION, "portal")
DEFAULT_DIR = PORTAL

# 渲染层共享护栏：本脚本与 P5.3/P5.4 校准链路用的是同一套判定，不另起炉灶。
sys.path.insert(0, PORTAL)
from jev_snapshot_gate import (  # noqa: E402
    DEFAULT_AFTER_CLOSE,
    RANK_FIELDS,
    XQ_KEYS,
    SocialSnapshotNotCompliant,
    _code6,
    _s,
    classify,
    coverage_row,
    day_from_filename,
    load_compliant_social,
    parse_day,
    parse_fetched_at,
    parse_hhmm,
    require_compliant_social,
)


def build_table(snapshot_dir=DEFAULT_DIR, after_close=(15, 0)):
    """扫描快照目录 → 可信度台账 + 合规覆盖率表。纯读，不写盘。"""
    items = []
    if os.path.isdir(snapshot_dir):
        for name in sorted(os.listdir(snapshot_dir)):
            fday = day_from_filename(name)
            if not fday:
                continue
            path = os.path.join(snapshot_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError) as exc:
                items.append({
                    "file": name, "doc": None,
                    "meta": {"file": name, "file_day": fday, "day": fday,
                             "fetched_at": None, "class": "unreadable",
                             "compliant": False, "reason": "读取失败: %s" % exc,
                             "n_codes": 0, "n_by_code": 0, "n_errors": 0,
                             "file_day_mismatch": False},
                })
                continue
            meta = classify(doc, after_close=after_close,
                            filename=name, file_day=fday)
            if meta["file_day_mismatch"]:
                meta["reason"] += "；文件名日期与内容 day 不一致"
            items.append({"file": name, "doc": doc, "meta": meta})

    rows, excluded = [], []
    for it in items:
        m = it["meta"]
        if m.get("compliant") and it["doc"] is not None:
            rows.append(coverage_row(it["doc"], m))
        else:
            excluded.append({k: m.get(k) for k in
                             ("file", "day", "fetched_at", "observed_date",
                              "observed_time", "lag_days", "class", "reason",
                              "n_codes", "n_by_code", "n_errors",
                              "file_day_mismatch")})

    def _mean(field):
        vals = [r[field] for r in rows if r.get(field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    summary = {
        "n_snapshots": len(items),
        "n_compliant": len(rows),
        "n_excluded": len(excluded),
        "compliant_days": [r["day"] for r in rows],
        "excluded_classes": sorted({e.get("class") for e in excluded
                                    if e.get("class")}),
        "coverage_listed_mean": _mean("coverage_listed"),
        "coverage_posts_mean": _mean("coverage_posts"),
    }
    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_dir": snapshot_dir,
        "rules": {
            "same_day_required": True,
            "after_close_hhmm": "%02d:%02d" % tuple(after_close),
            "denominator": "snapshot.codes (6 位代码去重)",
            "numerators": ["listed: 任一榜单出现", "posts: 真正拉到帖子"],
            "note": "day=选票日；fetched_at=观测时刻；两者不一致即时序错配",
        },
        "coverage_only": True,
        "gate_eligible": False,
        "calibrated": False,
        "disclaimers": [
            "非合规快照只列诊断，绝不进入覆盖率分子 / 分母",
            "覆盖率是数据可用性指标，不是 pattern 覆盖率，不得引用进判定闸 A 条件②",
            "选票池按涨停 / 焦点板块选取，与全市场不可比（P5.1 §八 样本偏倚）",
            "阈值未校准，恒 calibrated=False",
        ],
        "snapshots": [it["meta"] for it in items],
        "coverage_table": rows,
        "excluded": excluded,
        "summary": summary,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR, help="快照目录")
    ap.add_argument("--after-close", default=DEFAULT_AFTER_CLOSE,
                    help="盘后下界 HH:MM，缺省 15:00")
    ap.add_argument("--out", default="", help="显式指定才写 JSON；缺省只打印")
    ap.add_argument("--require-day", default="",
                    help="P5.3/P5.4 校准链路准入检查：该日快照必须合规，否则 rc=3")
    a = ap.parse_args()
    try:
        cutoff = parse_hhmm(a.after_close)
    except ValueError as exc:
        print("参数错误: %s" % exc)
        return 2

    if a.require_day:
        try:
            snap = require_compliant_social(a.require_day,
                                            snap_dir=a.dir,
                                            after_close=cutoff)
            print("准入通过 day=%s compliant=True n_codes=%d observed=%s"
                  % (a.require_day, snap["n_codes"], snap["observed_date"]))
        except SocialSnapshotNotCompliant as exc:
            print("准入拒绝（P5.3/P5.4 不得使用该日社交数据）: %s" % exc)
            return 3

    rep = build_table(a.dir, cutoff)
    s = rep["summary"]
    print("=== JEV 社交快照可信度 / 覆盖率表 ===")
    print("目录 %s  盘后下界 %s" % (a.dir, a.after_close))
    print("快照 %d 份：合规 %d / 剔除 %d（%s）"
          % (s["n_snapshots"], s["n_compliant"], s["n_excluded"],
             ", ".join(s["excluded_classes"]) or "-"))
    print("")
    print("%-24s %-10s %-20s %-14s %s"
          % ("file", "day", "fetched_at", "class", "reason"))
    for m in rep["snapshots"]:
        print("%-24s %-10s %-20s %-14s %s"
              % (str(m.get("file"))[:24], str(m.get("day"))[:10],
                 str(m.get("fetched_at"))[:20], str(m.get("class")),
                 str(m.get("reason"))[:70]))
    print("")
    if rep["coverage_table"]:
        print("%-10s %-8s %-8s %-8s %-10s %-10s"
              % ("day", "targets", "listed", "posts", "cov_list", "cov_post"))
        for r in rep["coverage_table"]:
            print("%-10s %-8s %-8s %-8s %-10s %-10s"
                  % (r["day"], r["n_targets"], r["n_listed"], r["n_posts"],
                     r["coverage_listed"], r["coverage_posts"]))
    else:
        print("覆盖率表：无合规快照，n_compliant=0，不产出任何覆盖率数字。")
    print("")
    print("coverage_only=True / gate_eligible=False / calibrated=False")
    for d in rep["disclaimers"]:
        print("  - " + d)
    if a.out:
        out_dir = os.path.dirname(os.path.abspath(a.out))
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print("已写出 %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
