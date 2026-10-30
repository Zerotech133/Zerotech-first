#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACAD1 研究账本一致性校验（2026-10-30，关羽 P99）。

只读校验 `agent_platform/data/academy/` 的课题台账与回测账本，
发现"签字未落 decision 字段 / 阶段与决策矛盾 / queued 回测超期 / 冻结申请未登记课题 / 新课题缺责任人截止"等问题。

用法：
  python tools/academy_ledger_check.py            # 人类可读摘要
  python tools/academy_ledger_check.py --json     # 机器可读（供 a0_patrol 等聚合）
  python tools/academy_ledger_check.py --strict   # 有 error 时退出码 1
退出码：0 正常（--strict 且存在 error 时 1）；2 数据目录缺失。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO / "agent_platform" / "data" / "academy"

STAGES = ["intake", "hypothesis", "evidence", "validation", "review",
          "joint_review", "decision", "closed"]
DECISIONS = ["补证", "Go", "No-Go", "试点", "产品化", "入库", "终止"]
BACKTEST_STATUSES = ["queued", "running", "done", "failed", "cancelled", "superseded"]
QUEUED_STALE_DAYS = 7


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _age_days(iso: str, now: float) -> float:
    try:
        ts = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return -1.0
    return round((now - ts) / 86400.0, 1)


def check(topics, backtests, freeze_requests, *, now=None, queued_stale_days=QUEUED_STALE_DAYS):
    """纯函数：返回 {ok, errors, warnings, stats}。不读写磁盘。"""
    now = time.time() if now is None else now
    errors, warnings = [], []
    by_id = {}
    for t in topics:
        tid = (t.get("topic_id") or "").strip()
        if not tid:
            errors.append("topic 缺 topic_id")
            continue
        if tid in by_id:
            errors.append("topic_id 重复: %s" % tid)
        if not (t.get("title") or "").strip():
            warnings.append("topic %s 缺 title" % tid)
        by_id[tid] = t

    for t in topics:
        tid = (t.get("topic_id") or "").strip()
        if not tid:
            continue
        status = (t.get("status") or "").strip()
        decision = (t.get("decision") or "").strip()
        if status not in STAGES:
            errors.append("%s: 非法 status=%r（允许: %s）" % (tid, status, ",".join(STAGES)))
        if decision and decision not in DECISIONS:
            warnings.append("%s: decision=%r 不在枚举 %s 内（建议用枚举值 + note 存细节）"
                            % (tid, decision, ",".join(DECISIONS)))
        if status == "decision" and not decision:
            errors.append("%s: status=decision 但 decision 字段为空（签字未落账）" % tid)
        if status == "closed" and not decision:
            errors.append("%s: status=closed 但无 decision" % tid)
        closed = t.get("closed")
        if closed and status != "closed":
            warnings.append("%s: closed=%r 但 status=%s（语义不一致）" % (tid, closed, status))
        if status == "closed" and not closed:
            warnings.append("%s: status=closed 但 closed 标记缺失" % tid)

    # 回测账本
    for b in backtests:
        tid = (b.get("topic_id") or "").strip()
        st = (b.get("status") or "").strip()
        if st not in BACKTEST_STATUSES:
            warnings.append("backtest %s: 未知 status=%r" % (tid or "?", st))
        if st == "queued":
            age = _age_days(str(b.get("created") or ""), now)
            if age < 0:
                warnings.append("backtest %s: queued 但 created 缺失/非法" % (tid or "?"))
            elif age > queued_stale_days:
                warnings.append("backtest %s: queued 已 %.1f 天（>%d 天阈值），需处置"
                                % (tid or "?", age, queued_stale_days))
        if tid and tid not in by_id:
            warnings.append("backtest %s: 对应课题不在台账" % tid)

    # 冻结申请未登记课题
    for fr in freeze_requests:
        tid = (fr.get("topic_id") or "").strip()
        if tid and tid not in by_id:
            errors.append("冻结申请 %s 未登记进 research_topics.json" % tid)

    # 新/进行中课题缺责任人+截止（decision/closed 阶段除外）
    for t in topics:
        tid = (t.get("topic_id") or "").strip()
        if not tid:
            continue
        if (t.get("status") or "") in ("decision", "closed"):
            continue
        if not (t.get("next_action_owner") or "").strip():
            warnings.append("%s: 缺 next_action_owner（下一步责任人）" % tid)
        if not (t.get("next_deadline") or "").strip():
            warnings.append("%s: 缺 next_deadline（下一步截止）" % tid)

    stats = {
        "topics": len(topics),
        "by_stage": {s: sum(1 for t in topics if (t.get("status") or "") == s) for s in STAGES},
        "backtests": len(backtests),
        "queued_stale": sum(
            1 for b in backtests
            if (b.get("status") or "") == "queued"
            and _age_days(str(b.get("created") or ""), now) > queued_stale_days),
        "freeze_requests": len(freeze_requests),
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


def run(data_dir: Path = DEFAULT_DATA, *, strict: bool = False) -> dict:
    if not data_dir.is_dir():
        raise SystemExit("数据目录不存在: %s" % data_dir)
    topics_path = data_dir / "research_topics.json"
    backtests_path = data_dir / "backtests.jsonl"
    topics = _read_json(topics_path) if topics_path.exists() else []
    backtests = _read_jsonl(backtests_path)
    freeze_dir = data_dir / "freeze_requests"
    freezes = []
    if freeze_dir.is_dir():
        for p in sorted(freeze_dir.glob("*.json")):
            try:
                freezes.append(_read_json(p))
            except (OSError, json.JSONDecodeError):
                pass
    rep = check(topics, backtests, freezes)
    rep["data_dir"] = str(data_dir)
    rep["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ACAD1 研究账本一致性校验")
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    rep = run(Path(args.data_dir), strict=args.strict)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("academy_ledger: ok=%s topics=%d warnings=%d errors=%d queued_stale=%d"
              % (rep["ok"], rep["stats"]["topics"], len(rep["warnings"]),
                 len(rep["errors"]), rep["stats"]["queued_stale"]))
        for e in rep["errors"]:
            print("  ERROR", e)
        for w in rep["warnings"]:
            print("  WARN ", w)
        print("  by_stage", rep["stats"]["by_stage"])
    if args.strict and not rep["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
