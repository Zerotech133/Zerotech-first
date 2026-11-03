# -*- coding: utf-8 -*-
"""学习项目看板数据聚合器：registry + learning_* 报告 + 知识库统计 → 静态看板 JSON。

输入：
  - agent_platform/registry/learning_projects.json（项目/owner/阶段/工件/SOP）
  - research/reports/learning_*.json（intake/standardize/pipeline/governance 最新报告）
  - 计划任务状态（ZeroBound_Learning*，powershell 查询，失败降级静态表）
  - 知识库规模（knowledge units 数，kb_read_api 只读）
输出：portal/static/learning_projects_dashboard.json（供 /learning_projects.html 读取）

CLI：python tools/kb_corpus/learning_dashboard_build.py build [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "agent_platform" / "registry" / "learning_projects.json"
REPORTS = REPO / "research" / "reports"
OUT = REPO / "portal" / "static" / "learning_projects_dashboard.json"

STAGE_LABELS = {
    "intake_pending": "待入库（等源文件）", "registered": "已登记", "ingesting": "入库中",
    "cleaning": "清洗中", "extracting": "抽取中", "governing": "治理中", "distilling": "提炼中",
    "standardizing": "标准化中", "published": "已发布", "archived": "已归档（只读）",
    "blocked": "阻塞",
}
PIPELINE_TASKS = [
    ("ZeroBound_LearningIntake", "学习入库", "learning_intake_*.json", "入库巡检"),
    ("ZeroBound_LearningStandardize", "学习标准化", "learning_standardize_*.json", "目录/格式标准化"),
    ("ZeroBound_LearningPipeline", "学习流水线", "learning_pipeline_*.json", "S2-S6 流水线推进"),
    ("ZeroBound_LearningGovernance", "学习治理", "learning_governance_*.json", "清单/质量/归档治理"),
]
DEFAULT_SCHEDULES = {
    "ZeroBound_LearningIntake": "04:00", "ZeroBound_LearningStandardize": "10:00",
    "ZeroBound_LearningPipeline": "20:00", "ZeroBound_LearningGovernance": "02:00",
}


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def latest_report(pattern: str, reports_dir: Path = REPORTS):
    files = sorted(reports_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        data = load_json(f)
        if data:
            return f, data
    return None, None


def latest_gate(reports_dir: Path = REPORTS):
    """读取最新 learning_gate_*.json（v2 门禁报告）。"""
    files = sorted(Path(reports_dir).glob("learning_gate_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        data = load_json(f)
        if data:
            return f, data
    return None, None


def build_gate_section(f, data) -> dict:
    """把 v2 门禁报告归一为看板 gate 段（保留各项目×阶段 errors 明细）。"""
    if not data:
        return {"available": False, "file": None, "ok": None, "generated_at": None,
                "stage": None, "summary": {"projects": 0, "checks": 0, "fail": 0},
                "projects": [], "failed_stages": []}
    projects = []
    failed = []
    for proj in data.get("projects") or []:
        stages = []
        for name, info in (proj.get("stages") or {}).items():
            ok = bool((info or {}).get("ok"))
            errs = list((info or {}).get("errors") or [])
            stages.append({"stage": name, "ok": ok, "errors": errs})
            if not ok:
                failed.append({"project_id": proj.get("project_id"), "stage": name, "errors": errs})
        projects.append({"project_id": proj.get("project_id"), "status": proj.get("status"),
                         "stages": stages, "fail": int(proj.get("fail") or 0)})
    return {"available": True, "file": str(f) if f else None,
            "ok": bool(data.get("ok")), "generated_at": data.get("generated_at"),
            "stage": data.get("stage"),
            "summary": {"projects": int((data.get("summary") or {}).get("projects") or 0),
                        "checks": int((data.get("summary") or {}).get("checks") or 0),
                        "fail": int((data.get("summary") or {}).get("fail") or 0)},
            "projects": projects, "failed_stages": failed}


def sync_registry_gate(reg: dict, registry_path: Path, gate: dict) -> tuple[dict, bool]:
    """把最新 gate 结果写回 registry 的 lifecycle.gate（仅 ok/checked_at 两字段）。

    不改流程状态（status/stage/history 保持不动）；无 gate 数据时原样返回。
    """
    if not gate.get("available"):
        return reg, False
    gate_by = {p.get("project_id"): p for p in gate.get("projects") or []}
    changed = False
    for proj in reg.get("projects") or []:
        gp = gate_by.get(proj.get("project_id"))
        if gp is None:
            continue
        life = proj.setdefault("lifecycle", {"stage": proj.get("status"),
                                             "stage_updated_at": None, "history": []})
        new_gate = {"ok": int(gp.get("fail") or 0) == 0, "checked_at": gate.get("generated_at")}
        if life.get("gate") != new_gate:
            life["gate"] = new_gate
            changed = True
    if changed:
        Path(registry_path).write_text(json.dumps(reg, ensure_ascii=False, indent=1) + "\n",
                                       encoding="utf-8")
    return reg, changed


def task_status() -> dict:
    """查询 ZeroBound_Learning* 计划任务状态/下次运行；失败返回静态表。"""
    names = [n for n, *_ in PIPELINE_TASKS]
    try:
        ps = (
            "$out=@(); Get-ScheduledTask -TaskName '" + "','".join(names) + "' -ErrorAction SilentlyContinue | "
            "ForEach-Object { $i=$_ | Get-ScheduledTaskInfo; $out += [pscustomobject]@{name=$_.TaskName;state=[string]$_.State;next=if($i.NextRunTime){$i.NextRunTime.ToString('s')}else{$null}} }; "
            "($out | ConvertTo-Json -Compress)"
        )
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=25, encoding="utf-8", errors="replace")
        data = json.loads(proc.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        return {str(d.get("name")): {"state": d.get("state"), "next_run": d.get("next")} for d in data}
    except Exception:  # noqa: BLE001 - 计划任务查询失败降级
        return {}


def build(registry_path: Path = REGISTRY, reports_dir: Path = REPORTS,
          out_path: Path = OUT) -> dict:
    reg = load_json(Path(registry_path))
    if not reg:
        raise SystemExit(f"registry 读取失败：{registry_path}")
    owner_meta = reg.get("process_owner") or {}
    owner_cn = owner_meta.get("cn") or "张松"
    reports = {}
    for _name, _label, pattern, _desc in PIPELINE_TASKS:
        f, data = latest_report(pattern, Path(reports_dir))
        reports[_name] = {"file": str(f) if f else None, "data": data or {}}
    # 质量门数据
    std = reports.get("ZeroBound_LearningStandardize", {}).get("data") or {}
    gov = reports.get("ZeroBound_LearningGovernance", {}).get("data") or {}
    intake = reports.get("ZeroBound_LearningIntake", {}).get("data") or {}
    pipeline = reports.get("ZeroBound_LearningPipeline", {}).get("data") or {}
    gov_projects = {p.get("project_id"): p for p in (gov.get("projects") or [])}
    pipeline_projects = {p.get("project_id"): p for p in (pipeline.get("projects") or [])}
    std_summary = std.get("summary") or {}
    gov_summary = gov.get("summary") or {}
    issues = list(std.get("issues") or []) + list(gov.get("issues") or [])
    high = int(gov_summary.get("high") or 0)
    gate_passed = len(issues) == 0 and high == 0
    # 知识库规模
    kb_units = 0
    try:
        sys.path.insert(0, str(REPO / "tools" / "kb_corpus"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("kb_read_api_for_learning", REPO / "tools" / "kb_corpus" / "kb_read_api.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        kb_units = len(module.load_units())
    except Exception:  # noqa: BLE001
        kb_units = 0
    gate_file, gate_data = latest_gate(Path(reports_dir))
    gate = build_gate_section(gate_file, gate_data)
    reg_at = str(reg.get("generated_at") or "")
    reg, registry_synced = sync_registry_gate(reg, Path(registry_path), gate)
    lifecycle_ok = sum(1 for p in reg.get("projects") or []
                       if ((p.get("lifecycle") or {}).get("gate") or {}).get("ok") is True)
    projects = []
    for p in reg.get("projects") or []:
        pid = p.get("project_id")
        gov_p = gov_projects.get(pid) or {}
        pipe_p = pipeline_projects.get(pid) or {}
        report_times = [str(r.get("data", {}).get("generated_at") or "") for r in reports.values()]
        last = max([t for t in [reg_at, str(pipe_p.get("generated_at") or ""), *report_times] if t] or [""])
        artifacts = p.get("artifacts") or {}
        life = p.get("lifecycle") or {}
        projects.append({
            "project_id": pid, "name": p.get("name"), "type": p.get("type"),
            "schema_version": p.get("schema_version") or "learning_project.v1",
            "lifecycle": {"stage": life.get("stage") or p.get("status"),
                          "stage_updated_at": life.get("stage_updated_at"),
                          "gate": life.get("gate") or {},
                          "history": list(life.get("history") or [])},
            "status": p.get("status"), "stage": p.get("status"),
            "stage_label": STAGE_LABELS.get(str(p.get("status")), str(p.get("status"))),
            "owner": owner_cn, "owner_agent": p.get("owner"),
            "source_count": len(p.get("source") or []), "sources": p.get("source") or [],
            "drafts": {"units_draft": int(gov_p.get("units_draft") or 0),
                       "manifest": bool(gov_p.get("manifest")),
                       "pipeline_action": pipe_p.get("action"),
                       "artifacts": artifacts},
            "last_update": last or None,
            "next_action": p.get("next_action"), "note": p.get("note"),
            "archive": p.get("archive"),
        })
    status_counts = {}
    for p in projects:
        status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1
    tasks = task_status()
    pipeline_rows = []
    for name, label, pattern, desc in PIPELINE_TASKS:
        info = tasks.get(name) or {}
        rep = reports.get(name) or {}
        gen = (rep.get("data") or {}).get("generated_at")
        pipeline_rows.append({
            "task": name, "label": label, "desc": desc,
            "schedule": DEFAULT_SCHEDULES.get(name),
            "next_run": info.get("next_run"), "state": info.get("state") or "unknown",
            "last_run": gen, "report": rep.get("file"),
        })
    todos = []
    for p in projects:
        if p.get("next_action"):
            todos.append({"project_id": p["project_id"], "todo": p["next_action"], "level": "high"})
        if p.get("status") == "archived" and p.get("note"):
            todos.append({"project_id": p["project_id"], "todo": p["note"], "level": "info"})
    for issue in issues:
        todos.append({"project_id": issue.get("project_id") or "pipeline",
                      "todo": f"[{issue.get('code') or issue.get('type') or 'issue'}] {issue.get('message') or issue}",
                      "level": "warn"})
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "schema_version": "learning_projects_dashboard.v2", "kind": "learning_projects_dashboard",
        "generated_at": now,
        "owner": {"agent_id": owner_meta.get("agent_id"), "cn": owner_cn,
                  "title": owner_meta.get("title"), "reports_to": owner_meta.get("reports_to")},
        "executors": reg.get("executors") or {},
        "summary": {"projects": len(projects), "status_counts": status_counts,
                    "pipeline_tasks": len(pipeline_rows), "gate_passed": gate_passed,
                    "gate_v2_ok": gate.get("ok"), "gate_v2_fail": gate.get("summary", {}).get("fail"),
                    "lifecycle_projects": len(projects), "lifecycle_gate_ok": lifecycle_ok,
                    "registry_gate_synced": registry_synced,
                    "issues": len(issues), "high": high, "kb_units": kb_units},
        "projects": projects, "pipeline": pipeline_rows,
        "gate": gate,
        "lifecycle": {"source": "agent_platform/registry/learning_projects.json",
                      "schema_version": "learning_project.v1",
                      "projects": len(projects), "gate_ok": lifecycle_ok,
                      "registry_synced": registry_synced},
        "quality": {"gate_passed": gate_passed, "issues": len(issues), "high": high,
                    "checks": [{"name": "intake", "ok": bool(intake.get("write", True)), "report": reports.get("ZeroBound_LearningIntake", {}).get("file")},
                               {"name": "standardize", "ok": int(std_summary.get("issues") or 0) == 0, "issues": std_summary.get("issues")},
                               {"name": "pipeline", "ok": bool(pipeline.get("projects") is not None), "projects": len(pipeline.get("projects") or [])},
                               {"name": "governance", "ok": int(gov_summary.get("issues") or 0) == 0 and high == 0, "issues": gov_summary.get("issues")}]},
        "todos": todos,
        "sop": reg.get("sop"),
        "sources": {"registry": str(registry_path), "reports_dir": str(reports_dir)},
        "note": "由 tools/kb_corpus/learning_dashboard_build.py 生成；只读聚合，不修改源数据。",
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="学习项目看板数据聚合")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="聚合并写 portal/static/learning_projects_dashboard.json")
    b.add_argument("--registry", default=str(REGISTRY))
    b.add_argument("--reports-dir", default=str(REPORTS))
    b.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    payload = build(args.registry, args.reports_dir, args.out)
    s = payload["summary"]
    print(f"[learning-dashboard] projects={s['projects']} pipeline={s['pipeline_tasks']} "
          f"gate={s['gate_passed']} issues={s['issues']} kb_units={s['kb_units']} -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
