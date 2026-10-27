# -*- coding: utf-8 -*-
"""M1 统一投影只读聚合器（门户读侧唯一数据面）。

聚合 M0/M1 全部只读投影，供门户 /api/unified/projection 与 CLI 使用：

- tasks       task_adapter.build_report 摘要（canonical vs legacy 双读）
- events      event_adapter.build_report 摘要（v1 契约校验结果）
- projects    business_adapter.project_projection（registry vs portal 双源）
- academy     domains/academy_wiring 投影 + 越门审计
- gzh         domains/gzh_wiring 投影（WorkItem+Artifact 指纹）+ 越门审计
- invest      domains/invest_wiring 投影 + 越门/指纹漂移审计
- approvals   统一审批注册表统计（engine/approvals）

纪律：
- 全部只读：不写任何事实源；
- fail-visible：单个子投影异常不拖垮整体报告（fail-open 门户可用性），
  但必须在 findings 显式报告，绝不静默吞掉；
- 摘要化：tasks/events 不复制全量记录（list→count），门户拿概览，
  明细走各自专用端点/账本。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
REPO_ROOT = _AGENT_PLATFORM.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

SCHEMA_VERSION = "1.0"
READ_ONLY = True


def _load_module(name: str, rel: str):
    """按相对 agent_platform 路径加载模块（importlib 独立加载安全）。"""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _AGENT_PLATFORM / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 字符串注解解析需要
    spec.loader.exec_module(module)
    return module


def _slim(report: Dict[str, Any], keep_keys: List[str]) -> Dict[str, Any]:
    """报告瘦身：只保留指定顶层键（防御适配器结构演进）。"""
    return {k: report.get(k) for k in keep_keys if k in report}


def _safe(section_fn, section_name: str, findings: List[str]):
    """单节失败不拖垮整体（fail-visible：findings 显式记录）。"""
    try:
        return section_fn()
    except Exception as exc:  # noqa: BLE001
        findings.append(
            "[%s] 投影失败: %s: %s" % (section_name, type(exc).__name__, exc)
        )
        return {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}


def build_unified_report(root: Path = REPO_ROOT) -> Dict[str, Any]:
    root = Path(root)
    findings: List[str] = []

    task_adapter = _load_module("up_task_adapter", "adapters/task_adapter.py")
    event_adapter = _load_module("up_event_adapter", "adapters/event_adapter.py")
    business_adapter = _load_module(
        "up_business_adapter", "adapters/business_adapter.py"
    )
    gzh_wiring = _load_module("up_gzh_wiring", "domains/gzh_wiring.py")
    academy_wiring = _load_module("up_academy_wiring", "domains/academy_wiring.py")
    invest_wiring = _load_module("up_invest_wiring", "domains/invest_wiring.py")
    dev_wiring = _load_module("up_dev_wiring", "domains/dev_wiring.py")
    pitch_wiring = _load_module("up_pitch_wiring", "domains/pitch_wiring.py")
    personal_wiring = _load_module("up_personal_wiring", "domains/personal_wiring.py")
    devtask_wiring = _load_module("up_devtask_wiring", "domains/devtask_wiring.py")
    knowledge_wiring = _load_module(
        "up_knowledge_wiring", "domains/knowledge_wiring.py"
    )
    skill_wiring = _load_module("up_skill_wiring", "domains/skill_wiring.py")
    intel_wiring = _load_module("up_intel_wiring", "domains/intel_wiring.py")
    approvals_mod = _load_module("up_approvals", "engine/approvals.py")

    # ── tasks / events：M0 只读适配器摘要 ──
    def _tasks():
        rep = task_adapter.build_report(root)
        slim = _slim(
            rep,
            [
                "adapter_version",
                "read_only",
                "sources",
                "snapshot_freshness",
                "coverage",
                "conflicts",
                "source_errors",
            ],
        )
        slim["record_count"] = (
            len(rep.get("records", [])) if isinstance(rep, dict) else 0
        )
        return slim

    tasks = _safe(_tasks, "tasks", findings)

    def _events():
        rep = event_adapter.build_report(root)
        slim = _slim(
            rep,
            [
                "adapter_version",
                "schema_version",
                "read_only",
                "canonical_valid",
                "canonical_errors",
                "counts",
                "freshness",
                "v1_compatibility",
                "source_errors",
            ],
        )
        slim["record_count"] = (
            len(rep.get("records", [])) if isinstance(rep, dict) else 0
        )
        return slim

    events = _safe(_events, "events", findings)

    # ── projects：项目双源比对 ──
    def _projects():
        proj = business_adapter.project_projection(root)
        return {
            "source_status": proj.get("source_status"),
            "registry_count": proj.get("registry_count"),
            "portal_count": proj.get("portal_count"),
            "matched_ids": proj.get("matched_ids"),
            "canonical_candidate": proj.get("canonical_candidate"),
            "findings": proj.get("findings"),
        }

    projects = _safe(_projects, "projects", findings)

    # ── academy / gzh：M1 接线域投影 + 越门审计 ──
    def _academy():
        items = academy_wiring.project_workitems(root)
        audit = academy_wiring.audit_report(root)
        return {
            "workitem_count": len(items),
            "by_status": audit.get("by_status"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": [wi.to_dict() for wi in items],
        }

    academy = _safe(_academy, "academy", findings)

    def _gzh():
        items, artifacts = gzh_wiring.project_workitems(root)
        audit = gzh_wiring.audit_report(root)
        return {
            "workitem_count": len(items),
            "by_status": audit.get("by_status"),
            "missing_artifacts": audit.get("missing_artifacts"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": [wi.to_dict() for wi in items],
        }

    gzh = _safe(_gzh, "gzh", findings)

    def _invest():
        items, artifacts = invest_wiring.project_workitems(root)
        audit = invest_wiring.audit_report(root)
        return {
            "workitem_count": len(items),
            "by_status": audit.get("by_status"),
            "missing_artifacts": audit.get("missing_artifacts"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": [wi.to_dict() for wi in items],
        }

    invest = _safe(_invest, "invest", findings)

    # ── dev / pitch / personal / devtask：M1 阶段二接线域投影 + 越门审计 ──
    def _dev():
        proj = dev_wiring.projection()
        audit = dev_wiring.audit_report()
        return {
            "workitem_count": proj.get("count"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": proj.get("tasks", []),
        }

    dev = _safe(_dev, "dev", findings)

    def _pitch():
        proj = pitch_wiring.projection()
        audit = pitch_wiring.audit_report()
        return {
            "workitem_count": proj.get("count"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": proj.get("decks", []),
        }

    pitch = _safe(_pitch, "pitch", findings)

    def _personal():
        proj = personal_wiring.projection()
        audit = personal_wiring.audit_report()
        return {
            "workitem_count": proj.get("count"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": proj.get("schedules", []),
        }

    personal = _safe(_personal, "personal", findings)

    def _devtask():
        items, artifacts = devtask_wiring.project_workitems(root)
        audit = devtask_wiring.audit_report(root)
        return {
            "workitem_count": len(items),
            "by_status": audit.get("by_status"),
            "missing_worktrees": audit.get("missing_worktrees"),
            "violations": audit.get("violations"),
            "verdict": audit.get("verdict"),
            "items": [wi.to_dict() for wi in items],
        }

    devtask = _safe(_devtask, "devtask", findings)

    # ── knowledge / skill：M2 记忆与技能目录域（审计 verdict 驱动）──
    def _knowledge():
        audit = knowledge_wiring.audit_report(root)
        return {
            "verdict": audit.get("verdict"),
            "pending_total": audit.get("pending_total"),
            "memory_total": audit.get("memory_total"),
            "bypass_count": audit.get("bypass_count"),
            "credentials": audit.get("credentials"),
            "findings": audit.get("findings"),
        }

    knowledge = _safe(_knowledge, "knowledge", findings)

    def _skill():
        audit = skill_wiring.audit_report(root)
        return {
            "verdict": audit.get("verdict"),
            "skill_total": audit.get("skill_total"),
            "by_status": audit.get("by_status"),
            "findings": audit.get("findings"),
            "cross_check": audit.get("cross_check"),
        }

    skill = _safe(_skill, "skill", findings)

    # ── intel：M2-P3 情报裁决域（血缘断链 → 情报决策，审计 verdict 驱动）──
    def _intel():
        audit = intel_wiring.audit_report(root)
        return {
            "verdict": audit.get("verdict"),
            "verdicts_total": audit.get("verdicts_total"),
            "memory_total": audit.get("memory_total"),
            "bypass_count": audit.get("bypass_count"),
            "credentials": audit.get("credentials"),
            "findings": audit.get("findings"),
            "cross_check": audit.get("cross_check"),
        }

    intel = _safe(_intel, "intel", findings)

    # ── approvals：统一审批注册表 ──
    def _approvals():
        store = approvals_mod.ApprovalStore(
            _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
        )
        try:
            stats = store.stats()
            rows = store.list(target_type="workitem")
            by_domain: Dict[str, int] = {}
            for a in rows:
                dom = a.target_id.split(":", 1)[0] or "unknown"
                by_domain[dom] = by_domain.get(dom, 0) + 1
            stats["by_domain"] = by_domain
            stats["recent"] = [a.to_dict() for a in rows[-10:]]
            return stats
        finally:
            store.close()

    approvals = _safe(_approvals, "approvals", findings)

    # ── 汇总 ──
    verdicts = {
        name: sect.get("verdict")
        for name, sect in (
            ("academy", academy),
            ("gzh", gzh),
            ("invest", invest),
            ("dev", dev),
            ("pitch", pitch),
            ("personal", personal),
            ("devtask", devtask),
            ("knowledge", knowledge),
            ("skill", skill),
            ("intel", intel),
        )
        if isinstance(sect, dict)
    }
    if findings:
        findings.insert(
            0, "统一投影存在 %d 条显式异常（fail-visible，未拖垮整体）" % len(findings)
        )
    # 顶层 verdict：域 verdict 全 PASS 且无 findings 才 PASS；
    # 有 FAIL 或源缺失 → FAIL；仅 WARN/异常条目 → ATTENTION（审计补齐）
    dv = list(verdicts.values())
    if findings and "FAIL" not in dv:
        top = "ATTENTION"
    elif "FAIL" in dv:
        top = "FAIL"
    elif findings:
        top = "ATTENTION"
    else:
        top = "PASS"
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": READ_ONLY,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "verdict": top,
        "summary": {
            "domains_wired": [
                "gzh",
                "academy",
                "invest",
                "dev",
                "pitch",
                "personal",
                "devtask",
                "knowledge",
                "skill",
                "intel",
            ],
            "domain_verdicts": verdicts,
            "approval_total": (approvals or {}).get("count", 0)
            if isinstance(approvals, dict)
            else 0,
        },
        "tasks": tasks,
        "events": events,
        "projects": projects,
        "academy": academy,
        "gzh": gzh,
        "invest": invest,
        "dev": dev,
        "pitch": pitch,
        "personal": personal,
        "devtask": devtask,
        "knowledge": knowledge,
        "skill": skill,
        "intel": intel,
        "approvals": approvals,
        "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 统一投影只读聚合器")
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)
    report = build_unified_report(Path(args.root))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
