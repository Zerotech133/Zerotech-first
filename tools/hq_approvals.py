#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-03：审批队列只读模型 v1。

数据源（全部只读）：
- agent_platform/data/approvals/approvals.db（已决记录；复用 approvals_registry 只读适配器）
- agent_platform/data/approvals.jsonl（Lark/飞书批准流水）
- agent_platform/config/approval_signers.json（M4 政策/签署人）

不写审批库；pending 以“无 pending 源”fail-visible 呈现，不伪造待办。
用法：python -X utf8 tools/hq_approvals.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "agent_platform"
APPROVAL_DB = AP / "data" / "approvals" / "approvals.db"
APPROVAL_JSONL = AP / "data" / "approvals.jsonl"
SIGNERS = AP / "config" / "approval_signers.json"

sys.path.insert(0, str(AP))
try:
    from adapters.approvals_registry import build_registry  # noqa: E402
except Exception:  # noqa: BLE001
    build_registry = None


def _read_jsonl(path: Path) -> list:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _policy() -> dict:
    try:
        d = json.loads(SIGNERS.read_text(encoding="utf-8-sig"))
        return {"m4_policy": d.get("m4_policy") or {}, "map_size": len(d.get("map") or {})}
    except Exception:  # noqa: BLE001
        return {"m4_policy": {}, "map_size": 0}


def build_approvals() -> dict:
    reg = {}
    errors = []
    if build_registry is not None:
        try:
            reg = build_registry(ROOT, limit=1000)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"approvals_registry: {exc}")
    else:
        errors.append("approvals_registry import failed")
    records = reg.get("records") or []
    by_decision = reg.get("by_decision") or {}
    by_target = reg.get("by_domain") or {}
    deciders: dict[str, int] = {}
    for r in records:
        d = str(r.get("decided_by") or "?")
        deciders[d] = deciders.get(d, 0) + 1
    feed = _read_jsonl(APPROVAL_JSONL)
    feed_actions: dict[str, int] = {}
    for r in feed:
        a = str(r.get("action") or "?")
        feed_actions[a] = feed_actions.get(a, 0) + 1
    pol = _policy()
    all_approved = bool(by_decision) and set(by_decision) == {"approved"}
    # pending 必须从数据面计算，不能硬编码 0（否则审批待办永远不可见）。
    pending_records = [r for r in records if not str(r.get("decision") or "").strip()]
    pending = len(pending_records)
    pending_source = ("approvals.db（decision 为空）" if pending else
                      "none（approvals.db 无 decision 空行；飞书待办未同步，不伪造）")
    risks = []
    if all_approved and len(records) >= 10:
        risks.append("100% approved / 无 rejected：审批可能过于宽松或只记录通过项，需审计抽样")
    if not records:
        risks.append("审批库无可读记录（fail-visible）")
    if not feed:
        risks.append("approvals.jsonl 无流水（飞书侧可能另存）")
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "source": {"db": str(APPROVAL_DB), "jsonl": str(APPROVAL_JSONL), "errors": errors},
        "summary": {
            "total_decisions": len(records),
            "first_seen": reg.get("first_seen"),
            "last_seen": reg.get("last_seen"),
            "by_decision": by_decision,
            "by_target_type": by_target,
            "top_deciders": sorted(deciders.items(), key=lambda kv: -kv[1])[:10],
            "pending": pending,
            "pending_source": pending_source,
            "feed_total": len(feed),
            "feed_actions": feed_actions,
        },
        "policy": pol,
        "latest": records[:10],
        "risks": risks,
    }


def render_md(a: dict) -> str:
    s = a["summary"]
    lines = [
        "# 零界 HQ · 审批队列 v1",
        "",
        f"- 生成：{a['generated_at']}｜已决 **{s['total_decisions']}**（{s['first_seen']} ~ {s['last_seen']}）",
        "- 决策分布：" + "，".join(f"{k}={v}" for k, v in (s["by_decision"] or {}).items()),
        f"- pending：**{s['pending']}**（{s['pending_source']}）",
        "",
        "## 审批政策",
        "",
        f"- M4 policy：{json.dumps(a['policy']['m4_policy'], ensure_ascii=False)}",
        f"- 签署人映射：{a['policy']['map_size']} 条",
        "",
        "## 按类型 / 决策人",
        "",
        f"- 类型：{json.dumps(s['by_target_type'], ensure_ascii=False)}",
        "- 决策人：" + "，".join(f"{k}={v}" for k, v in s["top_deciders"]),
        "",
        "## 最近 10 条",
        "",
        "| 时间 | 类型 | 目标 | 决策 | 决策人 |",
        "|---|---|---|---|---|",
    ]
    for r in a["latest"]:
        lines.append("| {t} | {ty} | {tg} | {d} | {by} |".format(
            t=r.get("decided_at") or r.get("created_at") or "-", ty=r.get("target_type") or "-",
            tg=r.get("target_id") or "-", d=r.get("decision") or "-", by=r.get("decided_by") or "-"))
    lines += ["", "## 风险", ""]
    lines += [f"- {x}" for x in a["risks"]] or ["- 无"]
    lines += ["", "> 只读；审批写入仍走原 ApprovalStore/飞书卡，不在 HQ 侧开写口。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-03 审批队列只读模型")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    a = build_approvals()
    print(json.dumps(a, ensure_ascii=False, indent=1) if args.json else render_md(a), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
