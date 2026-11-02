# -*- coding: utf-8 -*-
"""学习流水线运维：pipeline（S2-S5 草稿）/ governance（审计）/ standardize（规范）。

铁律：只写学习命名空间（知识库/05_学习源件|20_清洗|30_知识单元|35_知识提炼、
learning_projects.json、research/reports/learning_*），绝不触碰数据仓。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import shutil
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_KB = Path(r"C:\项目\知识库")
REG = REPO / "agent_platform/registry/learning_projects.json"
REPORTS = REPO / "research/reports"
ARCHIVE_ROOT = REPO / "_archive"
PDF_INGEST = REPO / "tools/kb_corpus/pdf_ingest.py"
LEARN_SRC = "05_学习源件"
OCR_TIMEOUT = 3600


class SafetyError(RuntimeError):
    pass


def safe(path: Path, kb: Path) -> None:
    p = path.resolve()
    roots = [(kb / "05_学习源件").resolve(), (kb / "20_清洗").resolve(),
             (kb / "30_知识单元").resolve(), (kb / "35_知识提炼").resolve(),
             REPORTS.resolve(), ARCHIVE_ROOT.resolve()]
    if p == REG.resolve():
        return
    if any(r == p or r in p.parents for r in roots):
        return
    raise SafetyError(f"拒绝写入非学习命名空间: {p}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
        ocr = ocr_stage(kb, p, write=write)
        item = {"project_id": pid, "status": p.get("status"), "pdf_ocr": ocr}
        texts = read_texts(kb, pid)
        item["texts"] = len(texts)
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


# ---------------------------------------------------------------------------
# 阶段 1：PDF OCR（幂等/可重入；产出登记 stage=ocr_done）
# ---------------------------------------------------------------------------
def project_dir(kb: Path, pid: str) -> Path:
    return kb / LEARN_SRC / pid


def _manifest_path(kb: Path, pid: str) -> Path:
    return project_dir(kb, pid) / "manifest.json"


def load_manifest(kb: Path, pid: str) -> dict:
    p = _manifest_path(kb, pid)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"schema_version": "learning_project.v1", "project_id": pid, "files": []}


def save_manifest(kb: Path, pid: str, man: dict) -> None:
    p = _manifest_path(kb, pid)
    safe(p, kb)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")


def find_project_pdfs(kb: Path, project: dict) -> list[Path]:
    """发现项目 PDF：源目录 source/*.pdf 优先，其次 registry.source / manifest 登记的原始路径。"""
    pid = str(project.get("project_id") or "")
    out: list[Path] = []
    src = project_dir(kb, pid) / "source"
    if src.exists():
        out.extend(sorted(src.glob("*.pdf")))
    for raw in project.get("source") or []:
        p = Path(raw)
        if p.exists() and p.suffix.lower() == ".pdf":
            out.append(p)
    for entry in load_manifest(kb, pid).get("files") or []:
        for key in ("origin", "path"):
            p = Path(str(entry.get(key) or ""))
            if p.suffix.lower() == ".pdf" and p.exists():
                out.append(p)
    uniq, seen = [], set()
    for p in out:
        key = str(p.resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def run_pdf_ocr(kb: Path, pid: str, pdf: Path, runner=None) -> dict:
    """对单个 PDF 调 pdf_ingest ocr；已有 ocr.txt/ocr.jsonl 视为已完成（幂等）。"""
    out_root = project_dir(kb, pid) / "ocr"
    book_dir = out_root / pdf.stem
    safe(out_root, kb)
    if (book_dir / "ocr.txt").exists() or (book_dir / "ocr.jsonl").exists():
        return {"name": pdf.name, "origin": str(pdf), "out_dir": str(book_dir),
                "skipped": True, "stage": "ocr_done", "sha256": sha256_file(pdf)}
    out_root.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-X", "utf8", str(PDF_INGEST), "ocr",
           "--pdf-dir", str(pdf.parent), "--out-dir", str(out_root), "--only", pdf.name]
    try:
        proc = (runner or subprocess.run)(cmd, capture_output=True, text=True, timeout=OCR_TIMEOUT)
        rc = int(getattr(proc, "returncode", 1))
        err = (getattr(proc, "stderr", "") or "")[-400:] if rc else None
    except Exception as exc:  # noqa: BLE001 - 失败保留 stage，可重入
        rc, err = 1, f"{type(exc).__name__}: {exc}"
    return {"name": pdf.name, "origin": str(pdf), "out_dir": str(book_dir),
            "skipped": False, "stage": "ocr_done" if rc == 0 else "ocr_failed",
            "rc": rc, "error": err, "sha256": sha256_file(pdf)}


def ocr_stage(kb: Path, project: dict, write: bool = False, runner=None) -> dict:
    """项目 OCR 阶段：无 PDF 跳过；已有阶段登记幂等；失败可重入（下次再试）。"""
    pid = str(project.get("project_id") or "")
    pdfs = find_project_pdfs(kb, project)
    if not pdfs:
        return {"stage": "skip_no_pdf", "pdfs": 0, "ran": 0, "skipped": 0, "failed": 0}
    man = load_manifest(kb, pid)
    done = {str(e.get("name")) for e in man.get("files") or []
            if e.get("stage") == "ocr_done"}
    plan = [{"name": p.name, "origin": str(p),
             "action": "skip_done" if p.name in done else "would_ocr"} for p in pdfs]
    if not write:
        return {"stage": "plan", "pdfs": len(pdfs), "ran": 0,
                "skipped": sum(1 for x in plan if x["action"] == "skip_done"),
                "failed": 0, "plan": plan}
    results = []
    for pdf in pdfs:
        rec = run_pdf_ocr(kb, pid, pdf, runner=runner)
        results.append(rec)
    files = [e for e in (man.get("files") or []) if e.get("name") not in {r["name"] for r in results}]
    files.extend({"name": r["name"], "origin": r["origin"], "out_dir": r["out_dir"],
                  "stage": r["stage"], "rc": r.get("rc"), "error": r.get("error"),
                  "sha256": r.get("sha256"),
                  "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
                 for r in results)
    failed = [r for r in results if r["stage"] != "ocr_done"]
    man.update({"schema_version": "learning_project.v1", "project_id": pid,
                "stage": "ocr_done" if not failed else (man.get("stage") or "ocr_pending"),
                "files": files,
                "ocr_updated_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    save_manifest(kb, pid, man)
    return {"stage": man["stage"], "pdfs": len(pdfs),
            "ran": sum(1 for r in results if not r.get("skipped")),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "failed": len(failed), "results": results}


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", str(name or "")).strip("_")[:60]


def _archive_target(rel: str | Path) -> Path:
    rel = str(rel)
    if rel.startswith("_archive/"):
        return ARCHIVE_ROOT / rel[len("_archive/"):]
    p = Path(rel)
    return p if p.is_absolute() else REPO / p


ARCHIVE_CONTRACT = "learning_archive_manifest.v1"
OWNER_AI = "DATA1"
ARTIFACT_KEYS = ("specs", "reports", "deliverables", "files", "knowledge_assets")


def resolve_artifact_path(path: str) -> str:
    """工件路径解析：仓库相对 → 平台 deliverables → 绝对路径原样。"""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    if (REPO / p).exists():
        return path
    plat = Path(r"C:\项目\deliverables") / p.name
    if plat.exists():
        return str(plat)
    return path


def artifacts_from_manifest(man: dict) -> list[dict]:
    """旧 manifest（specs/reports/deliverables/files）→ v2 artifacts[{path,sha256,size}]。"""
    out, seen = [], set()
    for key in ARTIFACT_KEYS:
        for e in man.get(key) or []:
            if not isinstance(e, dict):
                continue
            path = e.get("path") or e.get("file")
            if not path or path in seen:
                continue
            seen.add(path)
            resolved = resolve_artifact_path(path)
            out.append({"path": resolved, "sha256": e.get("sha256") or "",
                        "size": int(e.get("size") or 0)})
    return out


def build_archive_manifest_v2(project: dict, artifacts: list[dict],
                              archived_at: str | None = None) -> dict:
    """统一归档契约 learning_archive_manifest.v1（字段见 docs/standards/schemas）。"""
    return {
        "schema_version": ARCHIVE_CONTRACT,
        "project_id": project.get("project_id"),
        "name": project.get("name") or project.get("project_id"),
        "status": "archived",
        "archived_at": archived_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "owner_ai": OWNER_AI,
        "owner_cn": project.get("owner") or "",
        "conclusions": project.get("conclusions") or project.get("conclusion")
        or "【结项结论占位】待 Owner 填写：是否达成目标/保留价值/停止投入判断",
        "forbidden": ["不得基于已结项资产直接投入实盘策略（新假设须重走四关）",
                      "不得将归档资产复制回生产目录后继续迭代"],
        "continue_entry": "从 artifacts 取证据 → 新假设按 SOP 四关（PIT/成本/walk-forward/容量）"
                          "重开项目线（需老田/审批）",
        "artifacts": artifacts,
        "legacy_note": "registry 中的旧字段（source/artifacts 计数/note）保留；本文件为统一契约 v1",
    }


def _archive_dir(proj: dict) -> Path:
    rel = proj.get("archive") or f"_archive/{datetime.now().strftime('%Y-%m-%d')}_{_safe_name(proj.get('name') or proj.get('project_id'))}"
    return _archive_target(rel)


def adapt_v2_manifest(project: dict, write: bool = False) -> dict:
    """为既有归档目录生成 manifest.v2.json（不覆盖/不改动旧 ENTRY.md 与 manifest.json）。"""
    target = _archive_dir(project)
    v2_p = target / "manifest.v2.json"
    old_p = target / "manifest.json"
    if v2_p.exists():
        return {"mode": "v2_exists", "path": str(v2_p), "write": write}
    if not old_p.exists():
        return {"mode": "no_legacy_manifest", "path": str(v2_p), "write": write}
    old = json.loads(old_p.read_text(encoding="utf-8"))
    arts = artifacts_from_manifest(old)
    if not arts:
        arts = [{"path": a.get("path", ""), "sha256": a.get("sha256") or "",
                 "size": int(a.get("size") or 0)} for a in old.get("source_pdfs") or []
                if a.get("path")]
    man = build_archive_manifest_v2(project, arts,
                                   archived_at=old.get("archived_at"))
    if not write:
        return {"mode": "dry_run_adapt", "path": str(v2_p), "artifacts": len(arts),
                "write": False}
    target.mkdir(parents=True, exist_ok=True)
    v2_p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"mode": "v2_created", "path": str(v2_p), "artifacts": len(arts), "write": True}


def archive(kb: Path, project_id: str, write: bool = False,
            date_tag: str | None = None) -> dict:
    """结项归档：生成 _archive/<日期>_<项目名>/{ENTRY.md,manifest.json}（dry-run 默认）。"""
    reg = load_reg()
    proj = next((p for p in reg.get("projects") or [] if p.get("project_id") == project_id), None)
    if proj is None:
        return {"project_id": project_id, "mode": "not_found", "error": "项目未登记"}
    name = proj.get("name") or project_id
    date_tag = date_tag or datetime.now().strftime("%Y-%m-%d")
    target_rel = proj.get("archive") or f"_archive/{date_tag}_{_safe_name(name)}"
    target = _archive_target(target_rel)
    if proj.get("status") == "archived" or (target / "manifest.json").exists():
        exists = (target / "manifest.json").exists()
        adapted = adapt_v2_manifest(proj, write=write) if write else {"mode": "skip"}
        return {"project_id": project_id, "mode": "already_archived",
                "archive": target_rel, "exists": exists,
                "v2": adapted, "write": write,
                "note": "已归档（只读）；仅补 manifest.v2.json 适配文件，不改旧文件/不移动"}
    pdfs = find_project_pdfs(kb, proj)
    units = kb / "30_知识单元" / f"{project_id}_units_draft.jsonl"
    artifacts = [p for p in pdfs + ([units] if units.exists() else []) if p.exists()]
    plan = {"project_id": project_id, "name": name, "archive": target_rel,
            "files": [{"path": str(p), "sha256": sha256_file(p), "bytes": p.stat().st_size}
                      for p in artifacts],
            "status_before": proj.get("status"), "status_after": "archived"}
    if not write:
        return {"project_id": project_id, "mode": "dry_run", **plan}
    target.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    v2_artifacts = [{"path": f["path"], "sha256": f["sha256"], "size": f["bytes"]}
                    for f in plan["files"]]
    man = build_archive_manifest_v2(proj, v2_artifacts, archived_at=now)
    v2_p = target / "manifest.v2.json"
    safe(v2_p, kb)
    v2_p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    legacy = {"schema_version": "project_archive.v1", "project_id": project_id,
              "name": name, "status": "archived", "archived_at": now,
              "files": plan["files"], "artifacts": proj.get("artifacts") or {},
              "note": "legacy 兼容文件；统一契约为 manifest.v2.json（learning_archive_manifest.v1）"}
    man_p = target / "manifest.json"
    safe(man_p, kb)
    man_p.write_text(json.dumps(legacy, ensure_ascii=False, indent=1), encoding="utf-8")
    entry = [f"# {name} — 归档入口（已结项只读）", "",
             f"> 归档时间：{now}｜项目：`{project_id}`｜状态：**archived（只读）**｜"
             f"**契约：`{ARCHIVE_CONTRACT}`（manifest.v2.json）**", "",
             "## 一、结论（先看这个）", "",
             man["conclusions"], "",
             "## 二、产物索引（artifacts，含 sha256）", ""]
    for f in v2_artifacts:
        entry.append(f"- `{f['path']}`（{f['size']}B，sha256 `{(f['sha256'] or '')[:16]}…`）")
    entry += ["", "## 三、禁止与续做", ""] + [f"- {x}" for x in man["forbidden"]]
    entry += ["", "## 四、回滚/解冻", "", f"- {man['continue_entry']}", ""]
    (target / "ENTRY.md").write_text("\n".join(entry), encoding="utf-8")
    proj.update({"status": "archived", "archive": target_rel, "archived_at": now})
    save_reg(reg)
    return {"project_id": project_id, "mode": "archived", **plan,
            "manifest_v2": str(v2_p), "contract": ARCHIVE_CONTRACT,
            "created": [str(target / "ENTRY.md"), str(v2_p), str(man_p)],
            "status_updated": "archived"}


def archive_report(kb: Path, project_id: str, write: bool = False) -> dict:
    """真实跑一遍 archive + pipeline dry-run，输出 learning_archive_<tag>.{json,md}。"""
    tag = datetime.now().strftime("%Y%m%d")
    arch = archive(kb, project_id, write=write)
    pipe = pipeline(kb, write=False)
    rep = {"schema_version": "learning_archive.v1", "date_tag": tag,
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "project_id": project_id, "archive": arch, "pipeline_dry_run": pipe,
           "note": "archive 默认 dry-run；--write 才创建归档；pipeline 为 dry-run（不写盘）"}
    jp = REPORTS / f"learning_archive_{tag}.json"
    mp = REPORTS / f"learning_archive_{tag}.md"
    safe(jp, kb)
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = [f"# 学习线：PDF OCR 阶段 + 结项归档（{tag}）", "",
         f"> 生成 {rep['generated_at']}｜项目 `{project_id}`｜archive 模式 **{arch.get('mode')}**", "",
         "## 一、归档结果", ""]
    if arch.get("mode") == "already_archived":
        L += [f"- 已归档（只读），不重复归档：`{arch.get('archive')}`",
              f"- 本次 --write={write}；需补全项：{arch.get('completed') or '无'}", ""]
    elif arch.get("mode") == "dry_run":
        L += [f"- **dry-run**：将创建 `{arch.get('archive')}`，产物 {len(arch.get('files') or [])} 个（未写盘）", ""]
    elif arch.get("mode") == "archived":
        L += [f"- 已创建归档 `{arch.get('archive')}`（ENTRY.md + manifest.json），状态更新为 archived", ""]
    else:
        L += [f"- 异常：{arch}", ""]
    L += ["## 二、Pipeline dry-run（含 PDF OCR 阶段）", "",
          "| 项目 | 状态 | PDF OCR 阶段 | PDF 数 | 文本数 | 动作 |", "|---|---|---|---|---|---|"]
    for it in pipe.get("projects") or []:
        ocr = it.get("pdf_ocr") or {}
        L.append(f"| {it.get('project_id')} | {it.get('status')} | {ocr.get('stage')} | "
                 f"{ocr.get('pdfs')} | {it.get('texts')} | {it.get('action')} |")
    if not (pipe.get("projects") or []):
        L.append("| — | — | — | — | — | 无可处理项目（均已归档/无源） |")
    L += ["", "## 三、说明", "",
          "- 铁律：只写学习命名空间（知识库 05/20/30/35、learning_projects.json、_archive、research/reports/learning_*），**不碰数据仓**；",
          "- OCR 幂等：已有 ocr.txt/ocr.jsonl 或 manifest stage=ocr_done 的 PDF 跳过；失败保留 stage 可重入；",
          "- 归档为**复制索引**而非移动源文件；已归档项目再次运行 → no-op（只读）。", ""]
    mp.write_text("\n".join(L), encoding="utf-8")
    return {"report": rep, "json": str(jp), "md": str(mp)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pipeline", "governance", "standardize", "archive"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--project", default="")
    ap.add_argument("--report", action="store_true", help="archive 后输出 learning_archive_<tag>.{json,md}")
    ap.add_argument("--root", default=str(DEFAULT_KB))
    args = ap.parse_args(argv)
    kb = Path(args.root)
    if args.cmd == "archive":
        if not args.project:
            ap.error("archive 需要 --project <project_id>")
        if args.report:
            out = archive_report(kb, args.project, write=args.write)
            rep = out["report"]
            print(json.dumps({"archive": rep["archive"], "outputs": out["json"] + " / " + out["md"]},
                             ensure_ascii=False, indent=1)[:4000])
            return 0
        rep = archive(kb, args.project, write=args.write)
        print(json.dumps(rep, ensure_ascii=False, indent=1)[:4000])
        return 0
    fn = {"pipeline": pipeline, "governance": governance, "standardize": standardize}[args.cmd]
    rep = fn(kb, write=args.write)
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
