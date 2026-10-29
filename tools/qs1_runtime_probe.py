# -*- coding: utf-8 -*-
"""QS1（司马懿）运行体只读探针（2026-10-29 WO-INV-P0-ALIGN）。

背景：QS1 是 runtime 席（llm=none），不参与 seat_heartbeat 的模型心跳；此前只有
2026-10-26 一次失败 patrol，无法自证在岗/挂起。本探针只读检查：

- QS1 是否在 org 注册；
- 最近 N 天是否有 QS1 的 dev_events（patrol/accept/task_start 等）；
- 任务板是否有 QS1 的 doing 单；
- 输出 `active` / `standby` / `unverified`，可选 `--emit` 落一条 patrol_verdict。

不改任何配置/任务；`--emit` 仅追加事件层。退出码 0=正常（含 standby），2=自身错误。

用法::

    python -X utf8 tools/qs1_runtime_probe.py --json
    python -X utf8 tools/qs1_runtime_probe.py --emit
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(REPO, "portal", "data", "dev_events.jsonl")
TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
ORG = os.path.join(REPO, "agent_platform", "registry", "org.json")
ACKED = os.path.join(REPO, "agent_platform", "data", "quant_upstream", "acked")
SEAT = "QS1"


def _load_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _parse_ts(v):
    try:
        return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _recent_acks(acked_dir, start):
    """只读统计 acked/*.json 中窗口内的受理回执数（无目录=0）。"""
    if not acked_dir or not os.path.isdir(acked_dir):
        return 0
    n = 0
    for name in os.listdir(acked_dir):
        if not name.endswith(".json"):
            continue
        try:
            if datetime.fromtimestamp(os.path.getmtime(os.path.join(acked_dir, name))) >= start:
                n += 1
        except OSError:
            continue
    return n


def probe(events, tasks, now=None, days=7, acked_dir=None):
    now = now or datetime.now()
    start = now - timedelta(days=days)
    acks = _recent_acks(acked_dir, start)
    qs_events = [e for e in events if str(e.get("agent") or "") == SEAT]
    recent = [e for e in qs_events
              if (_parse_ts(e.get("ts")) or datetime.min) >= start]
    last = max((str(e.get("ts") or "") for e in qs_events), default="")
    doing = [k for k, v in (tasks or {}).items()
             if isinstance(v, dict) and str(v.get("owner") or "") == SEAT
             and v.get("status") == "doing"]
    ok_patrol = [e for e in recent if e.get("kind") == "patrol_verdict"
                 and "ok=True" in str(e.get("detail") or "")]
    if acks:
        status = "active"
        reason = "近 %d 天有 %d 份受理 ACK（WA1→QS1 链已跑）" % (days, acks)
    elif ok_patrol:
        status = "active"
        reason = "近 %d 天有 QS1 patrol_verdict ok=True" % days
    elif recent:
        status = "standby"
        reason = "近 %d 天有 QS1 事件但无 ok 心跳（llm=none 运行席待探针/上游）" % days
    else:
        status = "standby"
        reason = "近 %d 天无 QS1 事件；llm=none 运行席按 standby 管理" % days
    return {"seat": SEAT, "status": status, "reason": reason,
            "last_event": last, "recent_events": len(recent),
            "recent_acks": acks,
            "doing_tasks": doing, "days": days,
            "mode": "runtime-no-llm", "checked_at": now.strftime("%Y-%m-%d %H:%M:%S")}


def emit_result(result):
    sys.path.insert(0, os.path.join(REPO, "tools"))
    from events import emit  # noqa: E402
    emit("patrol_verdict", task=SEAT, agent=SEAT,
         detail="runtime_probe status=%s reason=%s doing=%d" % (
             result["status"], result["reason"][:120], len(result["doing_tasks"])))


def main(argv=None):
    ap = argparse.ArgumentParser(description="QS1 运行体只读探针")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--emit", action="store_true", help="追加一条 patrol_verdict")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        org = json.load(io.open(ORG, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print("[qs1] org.json 读取失败：%s" % e, file=sys.stderr)
        return 2
    agents = set()
    for d in org.get("departments") or []:
        agents.update(d.get("agents") or [])
    if SEAT not in agents:
        print("[qs1] %s 不在 org 编制" % SEAT, file=sys.stderr)
        return 2
    result = probe(_load_jsonl(EVENTS), _load_jsonl_tasks(), days=args.days,
                   acked_dir=ACKED)
    if args.emit:
        emit_result(result)
        result["emitted"] = True
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("[qs1] status=%s last_event=%s doing=%d | %s" % (
            result["status"], result["last_event"] or "—",
            len(result["doing_tasks"]), result["reason"]))
    return 0


def _load_jsonl_tasks():
    try:
        d = json.load(io.open(TASKS, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
