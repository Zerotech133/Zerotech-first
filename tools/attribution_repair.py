# -*- coding: utf-8 -*-
"""归因缺口修复映射（2026-10-29 · 事件层归属率整改）。

读 `portal/data/dev_events.jsonl` 里带 `attribution_missing=1` 的 task_start，
用**只读**证据回填归属：
  1) 事件自身 agent 有效（非 devtask/unknown）→ 用它（如 dev-gate 事件由 DSH-Lead 发出）；
  2) 事件 task 命中 tasks.json 且 owner 有效 → 用 owner；
  3) detail 里 `--owner X` / `owner=X` 且有效 → 用 X。
不修改原事件文件；写 sidecar 映射 `agent_platform/data/dev/attribution_repairs.jsonl`
（fingerprint → agent），C6 日报读取该映射重算归属率/缺口。

用法::

    python -X utf8 tools/attribution_repair.py --dry-run
    python -X utf8 tools/attribution_repair.py --write
    python -X utf8 tools/attribution_repair.py --write --days 7 --strict
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(REPO, "portal", "data", "dev_events.jsonl")
TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
OUT = os.path.join(REPO, "agent_platform", "data", "dev", "attribution_repairs.jsonl")
MARKER = "attribution_missing=1"
UNOWNED = {"", "unknown-agent", "devtask", "unknown"}
OWNER_RE = re.compile(r"(?:--owner[= ]\s*|owner=)([A-Za-z0-9_.\-]+)")
TASK_PREFIX_RE = re.compile(r"^WO-([A-Z][A-Z0-9]{2,})-")
BRACKET_RE = re.compile(r"\[([A-Z][A-Z0-9]{1,8})\]")
# 任务 ID 命名空间 → 席位（仅无歧义前缀；DEV/LEGION 等多席前缀不猜）
PREFIX_AGENTS = {"GUANYU": "GUANYU"}
KNOWN_BRACKET = {
    "T1", "T2", "N1", "F1", "D1", "ST1", "WA1", "QS1", "A0", "A0A",
    "ARCH1", "DEV1", "DEV2", "DEV3", "DEV4", "DEV5", "DEV6", "DEV7", "DEV8",
    "OPS1", "DOC1", "DATA1", "ACAD1", "INTEL0", "BACK1", "ZHAOYUN", "DSH-Lead",
    "GUANYU", "PM1", "QA1", "QA2", "SEC1", "SEC2", "LEG1", "MKT1",
    "R1", "R2", "R3", "TR1", "OPT1", "COMM1", "PROD1", "AI1",
}


def fingerprint(e):
    raw = "|".join(str(e.get(k) or "") for k in ("ts", "kind", "task", "detail"))
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _valid(agent):
    return bool(agent) and str(agent) not in UNOWNED


def resolve_event(e, tasks):
    """返回 (agent, source) 或 (None, None)。不修改 e。"""
    agent = str(e.get("agent") or "").strip()
    if _valid(agent):
        return agent, "event-agent"
    task = tasks.get(str(e.get("task") or ""))
    if isinstance(task, dict):
        owner = str(task.get("owner") or "").strip()
        if _valid(owner):
            return owner, "task-owner"
    m = OWNER_RE.search(str(e.get("detail") or ""))
    if m and _valid(m.group(1)):
        return m.group(1), "detail-owner"
    # 任务 ID 命名空间（如 WO-GUANYU-* → GUANYU；仅显式登记的无歧义前缀）
    m = TASK_PREFIX_RE.match(str(e.get("task") or ""))
    if m and m.group(1) in PREFIX_AGENTS:
        return PREFIX_AGENTS[m.group(1)], "task-id-prefix"
    # detail 里的席位标签（如 [T1]、[A0A]）
    for tag in BRACKET_RE.findall(str(e.get("detail") or "")):
        if tag in KNOWN_BRACKET:
            return tag, "detail-seat-tag"
    return None, None


def _load_events():
    rows = []
    if not os.path.exists(EVENTS):
        return rows
    with io.open(EVENTS, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                if not e.get("day") and isinstance(e.get("ts"), str):
                    e["day"] = e["ts"][:10]
                rows.append(e)
    return rows


def _load_tasks():
    try:
        d = json.load(io.open(TASKS, encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _load_map():
    out = {}
    if os.path.exists(OUT):
        with io.open(OUT, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("fingerprint") and r.get("agent"):
                    out[r["fingerprint"]] = r
    return out


def build_repairs(events, tasks, days=0, now=None):
    now = now or datetime.now()
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d") if days else ""
    resolved, unresolved = [], []
    for e in events:
        if e.get("kind") != "task_start":
            continue
        detail = str(e.get("detail") or "")
        old_agent = str(e.get("agent") or "").strip()
        flags = ("owner=unknown-agent" in detail or "owner_degraded=1" in detail)
        # 覆盖三类缺口：显式 attribution_missing 标记 / agent 不可归属 / detail 旧 owner 标记
        if MARKER not in detail and old_agent not in UNOWNED and not flags:
            continue
        if start and str(e.get("day") or "") < start:
            continue
        agent, source = resolve_event(e, tasks)
        item = {"fingerprint": fingerprint(e), "ts": e.get("ts"),
                "task": e.get("task"), "day": e.get("day"),
                "old_agent": old_agent,
                "missing_marker": MARKER in detail}
        if agent:
            item.update({"agent": agent, "source": source,
                         "resolved_at": now.strftime("%Y-%m-%d %H:%M:%S")})
            resolved.append(item)
        else:
            item["reason"] = "unresolved"
            unresolved.append(item)
    return resolved, unresolved


def main(argv=None):
    ap = argparse.ArgumentParser(description="归因缺口修复映射（只读+sidecar）")
    ap.add_argument("--days", type=int, default=0, help="窗口天数（0=全部）")
    ap.add_argument("--write", action="store_true", help="写 sidecar 映射（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="显式 dry-run（默认行为，兼容文档）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="有 unresolved 时 rc=1")
    args = ap.parse_args(argv)
    events, tasks = _load_events(), _load_tasks()
    resolved, unresolved = build_repairs(events, tasks, args.days)
    existing = _load_map()
    added = [r for r in resolved if r["fingerprint"] not in existing]
    if args.write and added:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with io.open(OUT, "a", encoding="utf-8", newline="") as f:
            for r in added:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    result = {"events": len(events), "missing": len(resolved) + len(unresolved),
              "resolved": len(resolved), "unresolved": len(unresolved),
              "added": len(added) if args.write else 0,
              "sidecar": OUT,
              "unresolved_samples": unresolved[:5],
              "resolved_by_source": {}}
    for r in resolved:
        k = r.get("source") or "?"
        result["resolved_by_source"][k] = result["resolved_by_source"].get(k, 0) + 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("[attribution_repair] missing=%d resolved=%d unresolved=%d added=%d sidecar=%s" % (
            result["missing"], result["resolved"], result["unresolved"],
            result["added"], OUT))
        print("  by_source:", result["resolved_by_source"])
        for r in unresolved[:5]:
            print("  UNRESOLVED", r.get("day"), r.get("task"), r.get("old_agent"))
    return 1 if (args.strict and unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main())
