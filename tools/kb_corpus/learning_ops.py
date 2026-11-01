# -*- coding: utf-8 -*-
"""学习流水线运维：pipeline（S2-S5 草稿）/ governance（审计）/ standardize（规范）。

铁律：只写学习命名空间（知识库/05_学习源件|20_清洗|30_知识单元|35_知识提炼、
learning_projects.json、research/reports/learning_*），绝不触碰数据仓。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_KB = Path(r"C:\项目\知识库")
REG = REPO / "agent_platform/registry/learning_projects.json"
REPORTS = REPO / "research/reports"


class SafetyError(RuntimeError):
    pass


def safe(path: Path, kb: Path) -> None:
    p = path.resolve()
    roots = [(kb / "05_学习源件").resolve(), (kb / "20_清洗").resolve(),
             (kb / "30_知识单元").resolve(), (kb / "35_知识提炼").resolve(),
             REPORTS.resolve()]
    if p == REG.resolve():
        return
    if any(r == p or r in p.parents for r in roots):
        return
    raise SafetyError(f"拒绝写入非学习命名空间: {p}")


def load_reg() -> dict:
    return json.loads(REG.read_text(encoding="utf-8")) if REG.exists() else {"projects": []}


def save_reg(reg: dict) -> None:
    REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


def read_texts(kb: Path, pid: str) -> list[Path]:
    d = kb / "20_清洗" / pid / "text"
    return sorted(d.glob("*.md")) if d.exists() else []


def clean_md(src: Path, out: Path) -> dict:
    raw = src.read_text(encoding="utf-8", errors="replace")
    lines = [ln.rstrip() for ln in raw.splitlines()]
    out_lines, blank = [], 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out_lines.append(ln)
    text = "\n".join(out_lines)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return {"src": str(src), "out": str(out), "chars": len(text), "lines": len(out_lines)}


def draft_units(pid: str, clean_files: list[Path], out_jsonl: Path) -> int:
    units, n = [], 0
    for f in clean_files:
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            s = line.strip()
            if len(s) < 8 or s.startswith("#") or s.startswith("|"):
                continue
            n += 1
            units.append({"unit_id": f"learn_{pid}_{n:04d}", "project_id": pid,
                          "claim": s, "evidence": {"file": f.name, "line": i},
                          "status": "draft", "created_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for u in units:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")
    return len(units)


def pipeline(kb: Path, write: bool = False) -> dict:
    reg = load_reg()
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "write": write, "projects": []}
    for p in reg.get("projects", []):
        pid = p.get("project_id")
        if not pid or p.get("status") in ("archived",):
            continue
        texts = read_texts(kb, pid)
        item = {"project_id": pid, "status": p.get("status"), "texts": len(texts)}
        if not texts:
            item["action"] = "skip_no_text"
            rep["projects"].append(item)
            continue
        if not write:
            item["action"] = "would_pipeline"
            rep["projects"].append(item)
            continue
        clean_dir = kb / "20_清洗" / pid / "clean"
        safe(clean_dir, kb)
        cleaned = [clean_md(t, clean_dir / t.name) for t in texts]
        units_path = kb / "30_知识单元" / f"{pid}_units_draft.jsonl"
        safe(units_path, kb)
        n_units = draft_units(pid, [Path(c["out"]) for c in cleaned], units_path)
        # 概念草稿（简单词频，不发布）
        concept_dir = kb / "35_知识提炼"
        safe(concept_dir / f"{pid}_concepts_draft.json", kb)
        concept_dir.mkdir(parents=True, exist_ok=True)
        words = {}
        for c in cleaned:
            for w in re.findall(r"[A-Za-z\u4e00-\u9fff]{2,}", Path(c["out"]).read_text(encoding="utf-8")):
                words[w] = words.get(w, 0) + 1
        top = sorted(words.items(), key=lambda x: -x[1])[:30]
        (concept_dir / f"{pid}_concepts_draft.json").write_text(json.dumps(
            {"project_id": pid, "generated_at": rep["generated_at"],
             "top_terms": [{"term": k, "count": v} for k, v in top if v >= 2],
             "note": "草稿：供 DATA5/研究线提纯；未发布到门户"}, ensure_ascii=False, indent=1), encoding="utf-8")
        p["status"] = "distilling"
        p["stage_updated_at"] = rep["generated_at"]
        item.update(action="pipelined", clean_files=[c["out"] for c in cleaned], units=n_units,
                    concepts_top=len(top))
        rep["projects"].append(item)
    if write:
        save_reg(reg)
        rp = REPORTS / f"learning_pipeline_{datetime.now().strftime('%Y%m%d')}.json"
        safe(rp, kb)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        rep["receipt"] = str(rp)
    return rep


def governance(kb: Path, write: bool = False) -> dict:
    reg = load_reg()
    issues, projects = [], []
    hashes = {}
    for p in reg.get("projects", []):
        pid, status = p.get("project_id"), p.get("status")
        if not pid:
            continue
        pdir = kb / "05_学习源件" / pid
        man_p = pdir / "manifest.json"
        info = {"project_id": pid, "status": status, "manifest": man_p.exists()}
        if man_p.exists():
            man = json.loads(man_p.read_text(encoding="utf-8"))
            info["files"] = len(man.get("files") or [])
            for f in man.get("files") or []:
                h = f.get("sha256")
                if h:
                    if h in hashes and hashes[h] != pid:
                        issues.append({"level": "high", "code": "DUPLICATE_ACROSS_PROJECTS",
                                       "projects": [hashes[h], pid], "file": f.get("name")})
                    hashes[h] = pid
                src = Path(f.get("origin", ""))
                if f.get("copied") and not (pdir / "source" / f.get("name", "")).exists():
                    issues.append({"level": "high", "code": "SOURCE_MISSING", "project": pid, "file": f.get("name")})
        units = kb / "30_知识单元" / f"{pid}_units_draft.jsonl"
        info["units_draft"] = sum(1 for _ in units.open(encoding="utf-8")) if units.exists() else 0
        if status not in ("archived", "intake_pending") and info["units_draft"] == 0:
            issues.append({"level": "medium", "code": "NO_DRAFT_UNITS", "project": pid})
        projects.append(info)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "projects": projects, "issues": issues,
           "summary": {"projects": len(projects), "issues": len(issues),
                       "high": sum(1 for i in issues if i["level"] == "high")}}
    if write:
        rp = REPORTS / f"learning_governance_{datetime.now().strftime('%Y%m%d')}.json"
        safe(rp, kb)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        md = REPORTS / f"learning_governance_{datetime.now().strftime('%Y%m%d')}.md"
        lines = ["# 学习项目治理日报", "", f"生成：{rep['generated_at']}", "",
                 f"项目 {rep['summary']['projects']}｜问题 {rep['summary']['issues']}（high {rep['summary']['high']}）", ""]
        for i in issues:
            lines.append(f"- [{i['level']}] {i['code']} {i.get('project','')} {i.get('file','')}")
        md.write_text("\n".join(lines), encoding="utf-8")
        rep["receipt"] = str(rp)
    return rep


def standardize(kb: Path, write: bool = False) -> dict:
    checks, issues = [], []
    root = kb / "05_学习源件"
    for pdir in sorted(root.iterdir()) if root.exists() else []:
        if not pdir.is_dir() or pdir.name == "_inbox":
            continue
        meta = pdir / "meta.json"
        man = pdir / "manifest.json"
        ok_meta = meta.exists() and "namespace" in json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else False
        checks.append({"dir": pdir.name, "meta": meta.exists(), "meta_ok": ok_meta, "manifest": man.exists()})
        if not meta.exists():
            issues.append({"level": "medium", "code": "META_MISSING", "project": pdir.name})
        if not man.exists():
            issues.append({"level": "high", "code": "MANIFEST_MISSING", "project": pdir.name})
        if re.search(r"[^\w\-]", pdir.name, flags=re.UNICODE):
            pass  # 中文项目名允许
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "dirs": checks, "issues": issues,
           "summary": {"dirs": len(checks), "issues": len(issues)}}
    if write:
        rp = REPORTS / f"learning_standardize_{datetime.now().strftime('%Y%m%d')}.json"
        safe(rp, kb)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        rep["receipt"] = str(rp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pipeline", "governance", "standardize"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--root", default=str(DEFAULT_KB))
    args = ap.parse_args(argv)
    kb = Path(args.root)
    fn = {"pipeline": pipeline, "governance": governance, "standardize": standardize}[args.cmd]
    rep = fn(kb, write=args.write)
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
