# -*- coding: utf-8 -*-
"""零界开发进度事件层（K-STOCK-113）——开发侧全量自动回流平台的数据地基。

  事件文件 : portal/data/dev_events.jsonl（每行一事件，append-only）
  事件类型 : task_start / commit / blocked / merge_done / guard_fail
             + dispatch / accept / review_verdict / patrol_verdict（K-ORG-027b 派单域）
             + research_intake / research_review / research_report（WO-LEGION-ACAD1-P0 研究院三件套）
             + meeting_saved / meeting_dispatched（P1c 会议回流）
             + acceptance_done（WO-HZ-MEET-20261030 黄忠日更验收联动）
  CLI 用法 :
    python tools/events.py emit --kind commit --task K-STOCK-113 --agent CodeBuddy --detail "xxx 完成"
    python tools/events.py today          # 今日汇总（JSON）
    python tools/events.py tail [-n 20]   # 最近 N 条（JSON）
  git 自动上报 : .git/hooks/post-commit 调 tools/post_commit_event.py，开发侧零操作。
  设计纪律 : 纯标准库；写坏不抛出（emit 永不阻塞开发动作）；append-only 不改历史。
"""
from __future__ import annotations

import sys

try:  # 2026-10-26 K-ORG-20261026-ENCPROT: non-UTF8 console printing CJK/checkmark
    # raises UnicodeEncodeError and can crash merge/hook paths; degrade to UTF-8 output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
import argparse
import json
import os
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVENTS_FILE = ROOT / "portal" / "data" / "dev_events.jsonl"


def _master_events_file() -> Path:
    """worktree 隔离开发时，事件必须落主仓文件（否则回流分裂）。
    经 git rev-parse --git-common-dir 定位主仓根；失败退回本仓根。"""
    try:
        import subprocess
        # 2026-10-27 B05 收口：Windows 中文路径下 git 输出必须显式 UTF-8 解码，
        # 否则 cp936 误解导致 worktree commit 事件落到乱码镜像目录（实测 bug）。
        gd = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=str(ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5).stdout.strip()
        if gd:
            return Path(gd).resolve().parent / "portal" / "data" / "dev_events.jsonl"
    except Exception:  # noqa: BLE001
        pass
    return EVENTS_FILE

KINDS = ("task_start", "commit", "blocked", "merge_done", "guard_fail",
        "dispatch", "accept", "review_verdict", "patrol_verdict",  # K-ORG-027b(OPS1)：+派单域四类
        "code_action", "code_observation",  # K-ORG-036e(F5)：+编码生产线动作/观察对（OpenHands 事件流范式）
        "approval_request", "approval_verdict",  # K-ORG-054(G4)：确定因素直批通道——Agent 请批/田丰判读（灰度期全部抄送老田）
        "confirmation_verdict",  # 2026-10-19 议事厅判例：用户对会议材料的确认/退回——不是田丰直批，不得混入 approval_verdict 灰度统计
        "deviation_report",  # K-ORG-054(G3)：SEC2 方案级走形检查报告（对照方案条目→证据→判定→建议命令）
        "research_intake", "research_review", "research_report",  # WO-LEGION-ACAD1-P0：研究院立项/评审/周报三件套（ACAD1 触发+审计取证）
        "meeting_saved", "meeting_dispatched",  # P1c 会议回流
        "acceptance_done")  # 黄忠日更验收联动（WO-HZ-MEET-20261030：日更验收单落事件层，验收会/日更两线收口）


def emit(kind: str, task: str = "", agent: str = "", detail: str = "",
         ide: str = "", model: str = "") -> dict:
    """追加一条事件。任何失败静默返回 {"ok": False}——事件层永远不挡开发。
    ide/model：开发工具与模型（如 CodeBuddy / qwen3.5:9b、Claude Code / claude-4.5）。"""
    try:
        if kind not in KINDS:
            raise ValueError("unknown kind: %s" % kind)
        target = _master_events_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "day": date.today().isoformat(),
            "kind": kind,
            "task": str(task or "").strip(),
            "agent": str(agent or os.environ.get("DEVTOOL_AGENT", "")).strip(),
            "ide": str(ide or os.environ.get("DEVTOOL_IDE", "")).strip()[:40],
            "model": str(model or os.environ.get("DEVTOOL_MODEL", "")).strip()[:60],
            "detail": str(detail or "").strip()[:300],
        }
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "rec": rec}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def read(limit: int | None = None) -> list[dict]:
    """读全部（或最近 N 条）事件；坏行跳过。"""
    try:
        lines = _master_events_file().read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out[-limit:] if limit else out


def today_summary(day: str | None = None) -> dict:
    """今日开发态势汇总：四类事件计数 + 活跃 Agent + 在办工单。"""
    d = day or date.today().isoformat()
    evs = [e for e in read() if e.get("day") == d]
    by_kind = {k: 0 for k in KINDS}
    agents: dict[str, int] = {}
    tasks: dict[str, dict] = {}
    for e in evs:
        by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
        if e.get("agent"):
            agents[e["agent"]] = agents.get(e["agent"], 0) + 1
        t = e.get("task") or "(未编号)"
        st = tasks.setdefault(t, {"events": 0, "last": "", "last_kind": ""})
        st["events"] += 1
        st["last"] = e.get("ts", "")
        st["last_kind"] = e.get("kind", "")
    return {"ok": True, "day": d, "total": len(evs), "by_kind": by_kind,
            "agents": agents, "tasks": tasks,
            "file": str(EVENTS_FILE)}


def main() -> None:
    ap = argparse.ArgumentParser(description="零界开发进度事件层")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("emit", help="追加事件")
    p1.add_argument("--kind", required=True, choices=KINDS)
    p1.add_argument("--task", default="")
    p1.add_argument("--agent", default="")
    p1.add_argument("--detail", default="")
    p1.add_argument("--ide", default="")
    p1.add_argument("--model", default="")
    p2 = sub.add_parser("today", help="今日汇总")
    p2.add_argument("--day", default=None)
    p3 = sub.add_parser("tail", help="最近 N 条")
    p3.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "emit":
        print(json.dumps(emit(a.kind, a.task, a.agent, a.detail,
                              getattr(a, "ide", ""), getattr(a, "model", "")),
                         ensure_ascii=False))
    elif a.cmd == "today":
        print(json.dumps(today_summary(a.day), ensure_ascii=False, indent=1))
    elif a.cmd == "tail":
        for e in read(a.n):
            print(json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()
