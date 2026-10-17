# -*- coding: utf-8 -*-
"""M0 P5 业务域只读投影适配器（项目双源 / Academy / GZH）。

按 docs/audit/2026-10-17_M0事实源与适配器盘点.md 适配器优先级：
  P1 项目双源：agent_platform/registry/projects.json（规范注册，canonical 候选）
              vs portal/projects_registry.json（sandbox UI 投影）——双读比对，
              不合并、不选边写回；
  P1 Academy：复用 research_topics.json 只读映射（阶段机不可与项目 phase/
              任务 status 混同）；
  P2 GZH：    gzh/drafts/index.json 为草稿状态真源，md 文件仅内容；
              正文缺失显式标记。

纪律：全部只读；缺失数据源显式报告 missing_source；状态不做跨域映射
（academy 阶段 / project phase / gzh 审批状态三者语义独立，原样保留）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA_VERSION = "1.0"
READ_ONLY = True


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return "__error__"


# ----------------------------------------------------------------------
# 项目双源
# ----------------------------------------------------------------------
def _norm_registry(raw) -> list:
    """规范注册（agent_platform/registry/projects.json）→ 统一记录。"""
    if raw in (None, "__error__") or not isinstance(raw, (dict, list)):
        return []
    items = raw if isinstance(raw, list) else raw.get("projects", [])
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "source": "registry.projects",
            "project_id": it.get("project_id", ""),
            "name": it.get("name", ""),
            "dept": it.get("dept", ""),
            "status": it.get("status", ""),          # active/archived
            "k_prefix": it.get("k_prefix", ""),
            "members": it.get("members", []),
            "charter": it.get("charter", ""),
            "raw_status": it.get("status", ""),
        })
    return out


def _norm_portal(raw) -> list:
    """沙盘 UI 投影（portal/projects_registry.json）→ 统一记录。"""
    if raw in (None, "__error__") or not isinstance(raw, dict):
        return []
    out = []
    for it in raw.get("projects", []):
        if not isinstance(it, dict):
            continue
        tasks = it.get("tasks", []) or []
        out.append({
            "source": "portal.projects_registry",
            "project_id": it.get("id", ""),
            "name": it.get("name", ""),
            "dept": it.get("dept_label", ""),
            "status": it.get("phase", ""),           # design/dev/test/accept（阶段，非状态机）
            "k_prefix": "",
            "members": [m.get("id", "") for m in it.get("members", []) if isinstance(m, dict)],
            "charter": it.get("summary", ""),
            "raw_status": it.get("phase", ""),
            "progress": it.get("progress"),
            "lead": it.get("lead", ""),
            "subtask_count": len(tasks),
            "subtask_status": {
                s: sum(1 for t in tasks if t.get("st") == s)
                for s in ("todo", "doing", "done")
            },
        })
    return out


def project_projection(root: Path) -> dict:
    reg_raw = _load_json(root / "agent_platform" / "registry" / "projects.json")
    portal_raw = _load_json(root / "portal" / "projects_registry.json")
    reg = _norm_registry(reg_raw)
    portal = _norm_portal(portal_raw)
    reg_ids = {r["project_id"] for r in reg}
    portal_ids = {p["project_id"] for p in portal}
    matched = sorted(reg_ids & portal_ids)
    findings = []
    if reg_raw == "__error__":
        findings.append("registry/projects.json 解析失败")
    if portal_raw == "__error__":
        findings.append("portal/projects_registry.json 解析失败")
    if not reg and reg_raw is not None:
        findings.append("registry/projects.json 无有效项目记录")
    if matched:
        findings.append(
            "两源存在同 ID 项目 %s：仅投影比对，未合并、未写回" % matched)
    else:
        findings.append("两源项目 ID 零交集：覆盖范围不同，不能按时间或状态合并")
    return {
        "source_status": {
            "registry.projects": "ok" if reg else (
                "missing_source" if reg_raw is None else "error"),
            "portal.projects_registry": "ok" if portal else (
                "missing_source" if portal_raw is None else "error"),
        },
        "canonical_candidate": "registry.projects（规范注册；portal 为 UI 投影）",
        "registry_count": len(reg),
        "portal_count": len(portal),
        "matched_ids": matched,
        "records": reg + portal,
        "findings": findings,
    }


# ----------------------------------------------------------------------
# Academy
# ----------------------------------------------------------------------
ACADEMY_STAGES = ["intake", "hypothesis", "evidence", "validation",
                  "review", "joint_review", "decision", "closed"]


def academy_projection(root: Path) -> dict:
    raw = _load_json(root / "agent_platform" / "data" / "academy"
                     / "research_topics.json")
    out = {"source": "academy.research_topics",
           "path": str(root / "agent_platform" / "data" / "academy"
                       / "research_topics.json"),
           "status": "ok" if isinstance(raw, (list, dict)) else (
               "missing_source" if raw is None else "error")}
    items = raw if isinstance(raw, list) else (
        raw.get("topics", []) if isinstance(raw, dict) else [])
    records = []
    for t in items:
        if not isinstance(t, dict):
            continue
        stage = t.get("status", "")
        records.append({
            "source": "academy.research_topics",
            "topic_id": t.get("topic_id", ""),
            "title": t.get("title", ""),
            "owner": t.get("owner", ""),
            "direction": t.get("direction", ""),
            "stage": stage,                           # 阶段机，原样保留
            "stage_index": ACADEMY_STAGES.index(stage) if stage in ACADEMY_STAGES else None,
            "history_len": len(t.get("history", []) or []),
        })
    out["count"] = len(records)
    out["records"] = records
    out["note"] = "status=阶段机（intake→closed），不与 project phase 或任务 status 混同"
    return out


# ----------------------------------------------------------------------
# GZH
# ----------------------------------------------------------------------
def gzh_projection(root: Path) -> dict:
    raw = _load_json(root / "gzh" / "drafts" / "index.json")
    drafts_dir = root / "gzh" / "drafts"
    out = {"source": "gzh.drafts_index", "path": str(root / "gzh" / "drafts"
                                                     / "index.json"),
           "status": "ok" if isinstance(raw, list) else (
               "missing_source" if raw is None else "error")}
    records = []
    missing_md = 0
    by_status = {}
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict):
            continue
        md = d.get("md", "")
        exists = bool(md) and (drafts_dir / md).exists()
        if md and not exists:
            missing_md += 1
        st = d.get("status", "")
        by_status[st] = by_status.get(st, 0) + 1
        records.append({
            "source": "gzh.drafts_index",
            "draft_id": d.get("id", ""),
            "date": d.get("date", ""),
            "title": d.get("title", ""),
            "col": d.get("col", ""),
            "status": st,                              # draft/reviewed/published（审批态，原样保留）
            "md": md,
            "md_exists": exists,
        })
    out["count"] = len(records)
    out["records"] = records
    out["by_status"] = by_status
    out["missing_md_count"] = missing_md
    out["note"] = "status=发布审批态；index 为草稿状态真源，正文 md 仅为内容"
    return out


# ----------------------------------------------------------------------
# 汇总
# ----------------------------------------------------------------------
def build_business_report(root: Path) -> dict:
    root = Path(root)
    projects = project_projection(root)
    academy = academy_projection(root)
    gzh = gzh_projection(root)
    findings = []
    for proj in projects["findings"]:
        findings.append("[projects] " + proj)
    if academy["status"] != "ok":
        findings.append("[academy] 数据源状态=%s" % academy["status"])
    if gzh["missing_md_count"]:
        findings.append("[gzh] %d 篇草稿 index 有记录但正文 md 缺失"
                        % gzh["missing_md_count"])
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": READ_ONLY,
        "root": str(root),
        "projects": projects,
        "academy": academy,
        "gzh": gzh,
        "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M0 P5 业务域只读投影")
    ap.add_argument("--root", default=".")
    ap.add_argument("--output", default="")
    args = ap.parse_args(argv)
    report = build_business_report(Path(args.root))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
