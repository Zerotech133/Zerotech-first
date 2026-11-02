# -*- coding: utf-8 -*-
"""学习数据 v1→v2 契约迁移：回填 schema_version + 生成 canonical decisions + 迁移报告（只做必要写回，先备份）。

用法：python tools/kb_corpus/learning_schema_migrate.py [--write]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KB = Path(r"C:\项目\知识库")
REG = REPO / "agent_platform/registry/learning_projects.json"
REPORTS = REPO / "research/reports"
BACKUPS = REPORTS / "backups"
now = datetime.now().astimezone().isoformat(timespec="seconds")


def backup(p: Path) -> str:
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dst = BACKUPS / f"{p.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.v1bak"
    shutil.copy2(p, dst)
    return str(dst)


def migrate(write: bool = False) -> dict:
    reg = json.loads(REG.read_text(encoding="utf-8"))
    rep = {"generated_at": now, "write": write, "projects": [], "canonical": {}}
    for proj in reg.get("projects", []):
        pid = proj.get("project_id")
        info = {"project_id": pid, "status": proj.get("status"), "artifacts": []}
        man_p = KB / "05_学习源件" / pid / "manifest.json"
        if man_p.exists():
            man = json.loads(man_p.read_text(encoding="utf-8"))
            changed = False
            if man.get("schema_version") != "learning_source_manifest.v1":
                man["schema_version"] = "learning_source_manifest.v1"
                changed = True
            if write and changed:
                info["backup"] = backup(man_p)
                man_p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
            info["artifacts"].append({"artifact": "source_manifest", "path": str(man_p),
                                      "migrated": changed, "schema": "learning_source_manifest.v1"})
        units_p = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
        if units_p.exists():
            lines = units_p.read_text(encoding="utf-8").splitlines()
            out, changed = [], False
            for line in lines:
                if not line.strip():
                    continue
                try:
                    u = json.loads(line)
                except ValueError:
                    out.append(line)
                    continue
                if u.get("schema_version") != "learning_unit.v1":
                    u["schema_version"] = "learning_unit.v1"
                    changed = True
                out.append(json.dumps(u, ensure_ascii=False))
            if write and changed:
                info["units_backup"] = backup(units_p)
                units_p.write_text("\n".join(out) + "\n", encoding="utf-8")
            info["artifacts"].append({"artifact": "units_draft", "path": str(units_p), "count": len(out),
                                      "migrated": changed, "schema": "learning_unit.v1"})
        cands = sorted(REPORTS.glob(f"card_candidates_{pid}*.json"))
        if cands:
            info["artifacts"].append({"artifact": "card_candidates", "path": str(cands[-1]),
                                      "schema": "learning_card_candidate.v1", "migrated": False})
        rep["projects"].append(info)

    # canonical decisions：老木（从 proposals 生成）
    prop_p = REPORTS / "card_proposals_laomu_20261102.json"
    if prop_p.exists():
        prop = json.loads(prop_p.read_text(encoding="utf-8"))
        decisions, summary = [], {"pass": 0, "revise": 0, "reject": 0}
        for c in prop.get("cards") or []:
            boxes = ((c.get("review") or {}).get("checkboxes") or {})
            action = "pass" if boxes.get("pass") else ("revise" if boxes.get("revise") else "reject")
            summary[action] += 1
            decisions.append({"card_id": c.get("card_id") or c.get("proposal_id") or "",
                              "action": action, "reviewer": (c.get("review") or {}).get("reviewer", "precheck_laomu_v1"),
                              "decided_at": prop.get("generated_at", now),
                              "note": ";".join(((c.get("precheck") or {}).get("issues") or []))})
        dec = {"schema_version": "learning_decisions.v1", "project_id": "laomu_notes",
               "generated_at": now, "source": "card_proposals_laomu_20261102.json",
               "decisions": decisions, "summary": summary}
        dec_p = REPORTS / "card_decisions_laomu_20261102.json"
        if write:
            dec_p.write_text(json.dumps(dec, ensure_ascii=False, indent=1), encoding="utf-8")
        rep["canonical"]["laomu_decisions"] = {"path": str(dec_p), "summary": summary, "count": len(decisions)}

    # 学习PDF archive manifest 映射说明（只读，不改归档包）
    arch_p = REPO / "_archive/2026-11-02_学习PDF_量学/manifest.json"
    if arch_p.exists():
        man = json.loads(arch_p.read_text(encoding="utf-8"))
        rep["canonical"]["learnpdf_archive"] = {
            "path": str(arch_p), "existing_schema": man.get("schema_version"),
            "mapped_to": "learning_archive_manifest.v1（gate 采用宽松校验：name/status/archived_at/owner_ai + ENTRY）",
            "migrated": False}
    if write:
        tag = datetime.now().strftime("%Y%m%d")
        jp = REPORTS / f"learning_schema_migration_{tag}.json"
        jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        L = ["# 学习数据 schema 迁移报告（v1→v2）", "", f"> {now}｜write={write}", ""]
        for p in rep["projects"]:
            L.append(f"## {p['project_id']}（{p['status']}）")
            for a in p["artifacts"]:
                L.append(f"- {a['artifact']}：{a.get('path','')} → {a.get('schema')}（migrated={a.get('migrated')}）")
            L.append("")
        if "laomu_decisions" in rep["canonical"]:
            d = rep["canonical"]["laomu_decisions"]
            L += ["## canonical decisions（老木）", "",
                  f"- 决策 {d['count']} 条：pass {d['summary']['pass']} / revise {d['summary']['revise']} / reject {d['summary']['reject']}",
                  f"- 文件：`{d['path']}`", ""]
        (REPORTS / f"learning_schema_migration_{tag}.md").write_text("\n".join(L), encoding="utf-8")
        rep["receipt"] = str(jp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    print(json.dumps(migrate(write=args.write), ensure_ascii=False, indent=1)[:5000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
