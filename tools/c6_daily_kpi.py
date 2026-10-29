#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C6 指标日报生成器（只读、确定性、零 LLM）。

用途：面向研发部 C 阶段 C6（D+14=2026-11-10）的每日指标快照：
- dev 采集份额（本周 dev 席 task_start 数，目标 ≥5/周）
- 归属率（近 7 天 task_start 中 owner 可归属占比，目标 ≥85%）
- 周吞吐（近 7 天 dev 席 merge_done 数，目标 ≥8/周）
- 验收命令覆盖（近 7 天 dev 单带 acceptance_cmd 的比例，目标 ≥90%）
- 归因缺口（attribution_missing=1）、dev-gate 告警、任务板盘面

数据源（只读）：
- .worktrees/tasks.json                    任务板真源
- portal/data/dev_events.jsonl             事件层（task_start/merge_done 等）
- agent_platform/config/dev_routing_rules.json  dev 席清单（缺失时用内置默认）

输出：
- agent_platform/data/dev/c6_daily/<date>.json
- agent_platform/data/dev/c6_daily/<date>.md
- stdout 摘要；退出码 0（只出数，不判负；判据只写 verdict 字段）

用法：
  python -X utf8 tools/c6_daily_kpi.py
  python -X utf8 tools/c6_daily_kpi.py --date 2026-10-27 --days 7
  python -X utf8 tools/c6_daily_kpi.py --out-dir <dir> --print-only
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
EVENTS = os.path.join(REPO, "portal", "data", "dev_events.jsonl")
ROUTING = os.path.join(REPO, "agent_platform", "config", "dev_routing_rules.json")
DEFAULT_OUT_DIR = os.path.join(REPO, "agent_platform", "data", "dev", "c6_daily")
PAPER_LEDGER = os.path.join(REPO, "agent_platform", "data", "paper", "paper_ledger.jsonl")
PAPER_STATE = os.path.join(REPO, "agent_platform", "data", "paper", "state.json")
PAPER_KILL = os.path.join(REPO, "agent_platform", "data", "paper", "KILL")

DEFAULT_DEV_SEATS = ("ARCH1", "DEV1", "DEV2", "DEV3", "DEV4", "DEV5", "DEV6",
                     "DEV7", "DEV8", "OPS1", "DOC1")
UNOWNED_AGENTS = ("", "unknown-agent", "devtask", "unknown")
# 2026-10-29 归因整改：只读修复映射（tools/attribution_repair.py 生成）
REPAIRS = os.path.join(REPO, "agent_platform", "data", "dev", "attribution_repairs.jsonl")

TARGET = {
    "dev_share_per_week": 5,
    "ownership_rate": 0.85,
    "week_throughput": 8,
    "acceptance_coverage": 0.90,
}


def _read_json(path, default=None):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _dev_seats():
    cfg = _read_json(ROUTING, {}) or {}
    seats = cfg.get("dev_seats")
    if isinstance(seats, list) and seats:
        return set(str(s) for s in seats)
    return set(DEFAULT_DEV_SEATS)


def _load_events(days):
    if not os.path.exists(EVENTS):
        return []
    out = []
    with io.open(EVENTS, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    # 事件各有 day 字段；缺失时退到 ts 前 10 位
    for e in out:
        if not e.get("day") and isinstance(e.get("ts"), str):
            e["day"] = e["ts"][:10]
    return out


def _in_period(day_str, start_day, end_day):
    return bool(day_str) and start_day <= day_str <= end_day


def _fingerprint(e):
    import hashlib
    raw = "|".join(str(e.get(k) or "") for k in ("ts", "kind", "task", "detail"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _load_repairs():
    """fingerprint -> agent（只读修复映射；缺失时返回空 dict）。"""
    out = {}
    try:
        with io.open(REPAIRS, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("fingerprint") and r.get("agent"):
                    out[r["fingerprint"]] = r["agent"]
    except OSError:
        pass
    return out


def _paper_snapshot():
    """paper MVP 可观测快照：NAV/仓位/台账/hash-chain/kill。"""
    state = _read_json(PAPER_STATE, {}) or {}
    rows = []
    if os.path.exists(PAPER_LEDGER):
        with io.open(PAPER_LEDGER, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    chain_ok = True
    prev = ""
    for r in rows:
        if r.get("prev_hash") != prev:
            chain_ok = False
            break
        prev = r.get("hash") or ""
    by_date = {}
    blocked = reduced = 0
    for r in rows:
        d = str(r.get("date") or "")
        by_date[d] = by_date.get(d, 0) + 1
        if r.get("verdict") == "block":
            blocked += 1
        elif r.get("verdict") == "reduce":
            reduced += 1
    return {
        "nav": state.get("nav"),
        "cash_weight": state.get("cash_weight"),
        "positions": len(state.get("positions") or {}),
        "ledger_rows": len(rows),
        "last_date": max(by_date) if by_date else "",
        "decisions_by_date": by_date,
        "blocked": blocked,
        "reduced": reduced,
        "hash_chain_ok": chain_ok,
        "killed": os.path.exists(PAPER_KILL),
        "mode": "paper",
    }


def compute(day=None, days=7):
    today = day or date.today()
    start = today - timedelta(days=days - 1)
    start_s, end_s = start.isoformat(), today.isoformat()
    seats = _dev_seats()

    tasks = _read_json(TASKS, {})
    if not isinstance(tasks, dict):
        tasks = {}
    status_counts = {}
    dev_tickets = []
    for tid, t in tasks.items():
        if not isinstance(t, dict):
            continue
        st = t.get("status", "?")
        status_counts[st] = status_counts.get(st, 0) + 1
        if str(t.get("dept") or "") == "dev":
            dev_tickets.append((tid, t))

    events = _load_events(days)
    starts_all = [e for e in events if e.get("kind") == "task_start"
                  and _in_period(e.get("day"), start_s, end_s)]
    # 数据质量：CLI 参数被当任务 ID（如 "--help"）属无效事件，不计入归属率分母
    invalid_starts = [e for e in starts_all
                      if not str(e.get("task") or "").strip()
                      or str(e.get("task") or "").startswith("-")]
    starts = [e for e in starts_all if e not in invalid_starts]
    merges = [e for e in events if e.get("kind") == "merge_done"
              and _in_period(e.get("day"), start_s, end_s)]
    guards = [e for e in events if e.get("kind") == "guard_fail"
              and _in_period(e.get("day"), start_s, end_s)]

    dev_starts = [e for e in starts if str(e.get("agent") or "") in seats]
    dev_merges = [e for e in merges if str(e.get("agent") or "") in seats]

    repairs = _load_repairs()

    def owned(e):
        agent = str(e.get("agent") or "")
        # 修复映射优先：历史缺口按任务 owner/detail owner/事件 agent 回填
        rep = repairs.get(_fingerprint(e))
        if rep and rep not in UNOWNED_AGENTS:
            return True
        if agent in UNOWNED_AGENTS:
            return False
        detail = str(e.get("detail") or "")
        if "owner=unknown-agent" in detail or "owner_degraded=1" in detail:
            return False
        return True

    owned_starts = [e for e in starts if owned(e)]
    attribution_missing_raw = sum(
        1 for e in starts if "attribution_missing=1" in str(e.get("detail") or ""))
    attribution_repaired = sum(
        1 for e in starts
        if "attribution_missing=1" in str(e.get("detail") or "")
        and (repairs.get(_fingerprint(e)) or "") not in UNOWNED_AGENTS)
    attribution_missing = attribution_missing_raw - attribution_repaired
    dev_gate_warns = sum(
        1 for e in guards if "[dev-gate]" in str(e.get("detail") or ""))

    dev_tickets_recent = [
        (tid, t) for tid, t in dev_tickets
        if _in_period(str(t.get("created") or "")[:10], start_s, end_s)
    ]
    with_acceptance = [
        (tid, t) for tid, t in dev_tickets_recent
        if str(t.get("acceptance_cmd") or "").strip()
    ]
    # 工单层归属率（与事件层区分）：近 N 天创建的 dev 单里 owner 合法/非 UNOWNED 的占比
    def ticket_owned(t):
        owner = str(t.get("owner") or "")
        desc = str(t.get("desc") or "")
        return bool(owner) and owner not in ("unknown-agent", "devtask") \
            and "[UNOWNED]" not in desc

    dev_ticket_ids = set(tid for tid, _t in dev_tickets)
    dev_ticket_merges = [
        e for e in merges
        if str(e.get("task") or "") in dev_ticket_ids
    ]

    def ratio(num, den, ndigits=4):
        return round(float(num) / den, ndigits) if den else None

    metrics = {
        "period": {"start": start_s, "end": end_s, "days": days},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_start_total": len(starts_all),
        "task_start_valid": len(starts),
        "task_start_invalid": len(invalid_starts),
        "task_start_dev": len(dev_starts),
        "dev_share_per_week": len(dev_starts),
        "merge_done_total": len(merges),
        "merge_done_dev": len(dev_merges),
        "week_throughput": len(dev_ticket_merges),
        "week_throughput_dev_events": len(dev_merges),
        "ownership_rate": ratio(len(owned_starts), len(starts)),
        "ownership_rate_tickets": ratio(
            sum(1 for _tid, t in dev_tickets_recent if ticket_owned(t)),
            len(dev_tickets_recent)),
        "dev_tickets_recent": len(dev_tickets_recent),
        "dev_tickets_owned": sum(1 for _tid, t in dev_tickets_recent if ticket_owned(t)),
        "attribution_missing": attribution_missing,
        "attribution_missing_raw": attribution_missing_raw,
        "attribution_repaired": attribution_repaired,
        "dev_gate_warns": dev_gate_warns,
        "dev_tickets_with_acceptance": len(with_acceptance),
        "acceptance_coverage": ratio(len(with_acceptance), len(dev_tickets_recent)),
        "tasks_total": len(tasks),
        "tasks_status": status_counts,
        "tasks_doing": [
            {"id": tid, "owner": t.get("owner", ""),
             "desc": str(t.get("desc") or "")[:120]}
            for tid, t in tasks.items()
            if isinstance(t, dict) and t.get("status") == "doing"
        ],
    }
    verdict = {
        "dev_share_ge_5": len(dev_starts) >= TARGET["dev_share_per_week"],
        "ownership_ge_85pct": (metrics["ownership_rate_tickets"] or 0) >= TARGET["ownership_rate"],
        "throughput_ge_8": len(dev_ticket_merges) >= TARGET["week_throughput"],
        "acceptance_ge_90pct": (metrics["acceptance_coverage"] or 0) >= TARGET["acceptance_coverage"],
    }
    metrics["paper"] = _paper_snapshot()
    return {"targets": TARGET, "metrics": metrics, "verdict": verdict}


def render_md(data):
    m = data["metrics"]
    v = data["verdict"]
    t = data["targets"]

    def yn(b):
        return "✅" if b else "🟡"

    def pct(x):
        return "—" if x is None else "%.1f%%" % (x * 100)

    lines = [
        "# C6 指标日报 · %s" % m["period"]["end"],
        "",
        "> 生成：%s ｜ 周期：%s ~ %s（%d 天）｜ 只读计算（tasks.json + dev_events.jsonl）"
        % (m["generated_at"], m["period"]["start"], m["period"]["end"], m["period"]["days"]),
        "",
        "## 一、C6 四指标",
        "",
        "| 指标 | 当前 | 目标 | 判定 |",
        "|---|---:|---:|---|",
        "| dev 采集（task_start 数/周） | %d | ≥%d | %s |"
        % (m["dev_share_per_week"], t["dev_share_per_week"], yn(v["dev_share_ge_5"])),
        "| 归属率（工单层，近 7 天新建 dev 单） | %s（%d/%d） | ≥85%% | %s |"
        % (pct(m["ownership_rate_tickets"]), m["dev_tickets_owned"],
           m["dev_tickets_recent"], yn(v["ownership_ge_85pct"])),
        "| 归属率（事件层，task_start） | %s | 观察 | — |"
        % pct(m["ownership_rate"]),
        "| 周吞吐（dev 单 merge_done 数/周） | %d（事件层 dev 席 %d） | ≥%d | %s |"
        % (m["week_throughput"], m["week_throughput_dev_events"],
           t["week_throughput"], yn(v["throughput_ge_8"])),
        "| 验收命令覆盖（近 7 天 dev 单） | %s（%d/%d） | ≥90%% | %s |"
        % (pct(m["acceptance_coverage"]), m["dev_tickets_with_acceptance"],
           m["dev_tickets_recent"], yn(v["acceptance_ge_90pct"])),
        "",
        "## 二、盘面",
        "",
        "- 任务板：共 **%d** 单；状态分布 `%s`"
        % (m["tasks_total"], json.dumps(m["tasks_status"], ensure_ascii=False)),
        "- 近 %d 天事件：task_start **%d**（dev %d）／merge_done **%d**（dev %d）"
        % (m["period"]["days"], m["task_start_total"], m["task_start_dev"],
           m["merge_done_total"], m["merge_done_dev"]),
        "- 归因缺口 attribution_missing：**%d**（已修复 %d / 原始 %d）；dev-gate 告警：**%d**"
        % (m["attribution_missing"], m["attribution_repaired"],
           m["attribution_missing_raw"], m["dev_gate_warns"]),
        "- paper（MVP）：NAV **%s**／现金 **%s**／持仓 **%d**／台账 **%d** 条（最后 %s）／"
        "blocked %d/reduced %d／hash-chain **%s**／kill **%s**"
        % (m["paper"].get("nav"), m["paper"].get("cash_weight"),
           m["paper"].get("positions", 0), m["paper"].get("ledger_rows", 0),
           m["paper"].get("last_date") or "—", m["paper"].get("blocked", 0),
           m["paper"].get("reduced", 0),
           "OK" if m["paper"].get("hash_chain_ok") else "BROKEN",
           "ON" if m["paper"].get("killed") else "off"),
        "",
        "## 三、doing（%d）" % len(m["tasks_doing"]),
        "",
    ]
    for d in m["tasks_doing"]:
        lines.append("- `%s`（%s）%s" % (d["id"], d["owner"] or "UNOWNED", d["desc"]))
    lines += [
        "",
        "## 四、口径与复跑",
        "",
        "```powershell",
        "python -X utf8 tools/c6_daily_kpi.py --date %s --days %d" % (m["period"]["end"], m["period"]["days"]),
        "```",
        "",
        "- 目标值：dev 采集 ≥5/周、归属率 ≥85%、周吞吐 ≥8/周、验收覆盖 ≥90%（C 阶段 C6，D+14=2026-11-10）。",
        "- 归属率口径：task_start 事件 agent 非 unknown/devtask 且 detail 无 owner_degraded；无样本时输出 null，不伪造。",
        "- 只出数不下结论；判定仅为 ✅/🟡，供治理席/日报引用。",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="C6 指标日报生成器（只读）")
    ap.add_argument("--date", default=None, help="报表日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--days", type=int, default=7, help="滚动窗口天数（默认 7）")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="输出目录")
    ap.add_argument("--repo", default=None, help="仓库根覆盖（测试/跨 worktree 用）")
    ap.add_argument("--print-only", action="store_true", help="只打印 JSON，不落盘")
    args = ap.parse_args(argv)

    global TASKS, EVENTS, ROUTING
    if args.repo:
        repo = os.path.abspath(args.repo)
        TASKS = os.path.join(repo, ".worktrees", "tasks.json")
        EVENTS = os.path.join(repo, "portal", "data", "dev_events.jsonl")
        ROUTING = os.path.join(repo, "agent_platform", "config", "dev_routing_rules.json")
        if args.out_dir == DEFAULT_OUT_DIR:
            args.out_dir = os.path.join(repo, "agent_platform", "data", "dev", "c6_daily")

    day = None
    if args.date:
        try:
            day = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("日期格式应为 YYYY-MM-DD: %s" % args.date, file=sys.stderr)
            return 2
    if args.days < 1 or args.days > 90:
        print("--days 需在 1~90", file=sys.stderr)
        return 2

    data = compute(day=day, days=args.days)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.print_only:
        print(text)
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    day_s = data["metrics"]["period"]["end"]
    jp = os.path.join(args.out_dir, day_s + ".json")
    mp = os.path.join(args.out_dir, day_s + ".md")
    with io.open(jp, "w", encoding="utf-8", newline="") as f:
        f.write(text + "\n")
    with io.open(mp, "w", encoding="utf-8", newline="") as f:
        f.write(render_md(data) + "\n")

    m = data["metrics"]
    print("C6 日报 %s -> %s" % (day_s, jp))
    print("  dev 采集=%d/周 归属率=%s 吞吐=%d/周 验收覆盖=%s"
          % (m["dev_share_per_week"],
             "—" if m["ownership_rate"] is None else "%.1f%%" % (m["ownership_rate"] * 100),
             m["week_throughput"],
             "—" if m["acceptance_coverage"] is None else "%.1f%%" % (m["acceptance_coverage"] * 100)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
