# -*- coding: utf-8 -*-
"""学习项目入库工具（原文件/Word/PDF/文本）——只写学习命名空间，绝不触碰数据仓。

用法：
  python tools/kb_corpus/learning_intake.py scan                      # 只读扫描
  python tools/kb_corpus/learning_intake.py ingest --write           # 真入库
  python tools/kb_corpus/learning_intake.py ingest --write --project 老木创业笔记
  # 测试可用 --root 指定沙箱知识库根
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_KB = Path(r"C:\项目\知识库")
REG = REPO / "agent_platform/registry/learning_projects.json"
REPORTS = REPO / "research/reports"
SUPPORTED = {".docx", ".pdf", ".md", ".txt"}
SKIP_NAMES = {"README.txt", "readme.txt", "说明.txt", "00_目录与交付说明.txt"}

_PII_MODULE = None
_PII_RULES: dict = {}
PII_ENV = "INTEL_KG_PII_CLEAN"


def _find_intel_kg_root() -> Path | None:
    """定位 intel-kg 根（pii_policy 唯一来源）。"""
    env = os.environ.get("INTEL_KG_ROOT")
    here = Path(__file__).resolve()
    cands: list[Path] = []
    if env:
        cands.append(Path(env))
    for up in (2, 3, 4):
        try:
            base = here.parents[up]
        except IndexError:
            continue
        cands += [base.parent / "intel-kg", base / "intel-kg"]
    cands.append(Path(r"C:\项目\intel-kg"))
    seen: set[Path] = set()
    for c in cands:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c in seen:
            continue
        seen.add(c)
        if (c / "tools" / "pii_policy" / "policy.py").is_file():
            return c
    return None


def load_pii_policy():
    """加载 pii_policy；返回 (module, rules) 或 (None, {})。"""
    root = _find_intel_kg_root()
    if root is None:
        return None, {}
    tools = str(root / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from pii_policy import policy  # type: ignore
    return policy, policy.load_rules()


def _pii_enabled_by_env() -> bool:
    value = (os.environ.get(PII_ENV) or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def new_pii_stats(enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "policy_version": "",
        "rules_digest": "",
        "files_scanned": 0,
        "files_changed": 0,
        "chars_before": 0,
        "chars_after": 0,
        "hit_rules": {},
    }


def clean_pii_text(text: str, stats: dict) -> tuple[str, dict]:
    """单文件文本清洗；返回 (新文本, 文件级统计)。"""
    info = {"scanned": False, "changed": False, "hits": {}}
    if not stats.get("enabled") or _PII_MODULE is None or not text:
        return text, info
    stats["files_scanned"] += 1
    stats["chars_before"] += len(text)
    new, hits = _PII_MODULE.closed_clean(text, rules=_PII_RULES)
    stats["chars_after"] += len(new)
    for key, value in hits.items():
        stats["hit_rules"][key] = stats["hit_rules"].get(key, 0) + value
    info["hits"] = dict(sorted(hits.items()))
    info["changed"] = new != text
    if info["changed"]:
        stats["files_changed"] += 1
    return new, info


class SafetyError(RuntimeError):
    """写入越界（非学习命名空间）时抛出。"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def allowed_write_roots(kb_root: Path) -> dict:
    return {
        "inbox": (kb_root / "05_学习源件").resolve(),
        "clean": (kb_root / "20_清洗").resolve(),
        "registry": REG.resolve(),
        "reports": REPORTS.resolve(),
    }


def assert_safe(path: Path, kb_root: Path) -> None:
    path = path.resolve()
    roots = allowed_write_roots(kb_root)
    if path == roots["registry"] or roots["reports"] in path.parents:
        return
    if roots["inbox"] in path.parents or roots["clean"] in path.parents:
        return
    raise SafetyError(f"拒绝写入非学习命名空间: {path}")


def extract_docx(src: Path, out_md: Path) -> dict:
    import docx  # python-docx
    doc = docx.Document(str(src))
    lines = [f"# {src.stem}", ""]
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for t in paras:
        lines.append(t)
        lines.append("")
    tables = 0
    for tbl in doc.tables:
        tables += 1
        lines.append(f"## 表 {tables}")
        for row in tbl.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return {"paragraphs": len(paras), "tables": tables, "chars": sum(len(x) for x in paras)}


def project_id_for(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in Path(name).stem)
    return cleaned[:40] or "learning_project"


def scan(kb_root: Path) -> list[dict]:
    inbox = kb_root / "05_学习源件" / "_inbox"
    files = []
    if inbox.exists():
        for f in sorted(inbox.iterdir()):
            if f.is_file() and f.suffix.lower() in SUPPORTED and f.name not in SKIP_NAMES:
                files.append({"path": str(f), "name": f.name, "size": f.stat().st_size,
                              "sha256": sha256_file(f)})
    return files


def load_reg() -> dict:
    return json.loads(REG.read_text(encoding="utf-8")) if REG.exists() else {"projects": []}


def existing_hashes(reg: dict, kb_root: Path | None = None) -> dict:
    out = {}
    for p in reg.get("projects", []):
        for m in p.get("manifest") or []:
            if m.get("sha256"):
                out[m["sha256"]] = m.get("name")
    if kb_root is not None:
        root = kb_root / "05_学习源件"
        if root.exists():
            for mp in root.glob("*/manifest.json"):
                try:
                    man = json.loads(mp.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for m in man.get("files") or []:
                    if m.get("sha256"):
                        out[m["sha256"]] = m.get("name")
    return out


def ingest(files: list[dict], kb_root: Path, project: str | None = None,
           write: bool = False, no_pii_clean: bool = False) -> dict:
    global _PII_MODULE, _PII_RULES
    reg = load_reg()
    seen = existing_hashes(reg, kb_root)
    pid = project_id_for(project or (files[0]["name"] if files else "learning_project"))
    pdir = kb_root / "05_学习源件" / pid
    receipt = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "project_id": pid, "write": write, "items": []}
    pii_stats = new_pii_stats(False)
    if write:
        use_pii = _pii_enabled_by_env() and not no_pii_clean
        if use_pii:
            if _PII_MODULE is None:
                _PII_MODULE, _PII_RULES = load_pii_policy()
            if _PII_MODULE is None:
                raise SafetyError(
                    "PII 清洗策略不可用（intel-kg/tools/pii_policy 缺失或损坏）；"
                    "仅排障时可使用 --no-pii-clean")
            pii_stats = new_pii_stats(True)
            pii_stats["policy_version"] = getattr(_PII_MODULE, "POLICY_VERSION", "")
            pii_stats["rules_digest"] = _PII_MODULE.rules_digest(_PII_RULES)
        else:
            pii_stats["disabled"] = "flag" if no_pii_clean else "env"
    receipt["pii"] = pii_stats
    for f in files:
        item = {"name": f["name"], "sha256": f["sha256"], "size": f["size"]}
        if f["sha256"] in seen:
            item.update(action="skip_duplicate", duplicate_of=seen[f["sha256"]])
            receipt["items"].append(item)
            continue
        src = Path(f["path"])
        if not write:
            item.update(action="would_ingest", target=str(pdir / "source" / f["name"]))
            receipt["items"].append(item)
            continue
        # 写源件副本（PDF 超 40MB 仅登记不复制）
        source_dir = pdir / "source"
        assert_safe(source_dir / f["name"], kb_root)
        source_dir.mkdir(parents=True, exist_ok=True)
        is_pdf = src.suffix.lower() == ".pdf"
        if is_pdf and f["size"] > 40 * 1024 * 1024:
            item["copy"] = False
        else:
            shutil.copy2(src, source_dir / f["name"])
            item["copy"] = True
        # 抽取文本
        text_md = kb_root / "20_清洗" / pid / "text" / f"{Path(f['name']).stem}.md"
        assert_safe(text_md, kb_root)
        if src.suffix.lower() == ".docx":
            item["extract"] = extract_docx(src, text_md)
            item["text_md"] = str(text_md)
            text = text_md.read_text(encoding="utf-8", errors="replace")
            clean, pinfo = clean_pii_text(text, pii_stats)
            if pinfo["changed"]:
                text_md.write_text(clean, encoding="utf-8")
            item["pii"] = pinfo
        elif src.suffix.lower() in (".md", ".txt"):
            text_md.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8", errors="replace")
            clean, pinfo = clean_pii_text(text, pii_stats)
            text_md.write_text(clean, encoding="utf-8")
            item["extract"] = {"chars": len(clean)}
            item["text_md"] = str(text_md)
            item["pii"] = pinfo
        else:
            item["extract"] = {"deferred": "pdf→pdf_ingest OCR 流水线"}
        item["action"] = "ingested"
        # manifest
        pdir.mkdir(parents=True, exist_ok=True)
        manifest_p = pdir / "manifest.json"
        assert_safe(manifest_p, kb_root)
        manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.exists() else {"files": [], "project_id": pid}
        manifest["files"].append({"name": f["name"], "sha256": f["sha256"], "size": f["size"],
                                  "origin": str(src), "copied": item["copy"],
                                  "text_md": item.get("text_md"), "pii": item.get("pii"),
                                  "ingested_at": receipt["generated_at"]})
        manifest_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        seen[f["sha256"]] = f["name"]
        receipt["items"].append(item)
    # 项目登记
    if write and any(i["action"] == "ingested" for i in receipt["items"]):
        meta_p = pdir / "meta.json"
        if not meta_p.exists():
            assert_safe(meta_p, kb_root)
            meta_p.write_text(json.dumps({
                "project_id": pid, "name": project or pid, "namespace": "learning",
                "owner": "DATA1", "status": "intake_pending", "created_at": receipt["generated_at"],
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        projects = reg.setdefault("projects", [])
        if not any(x.get("project_id") == pid for x in projects):
            projects.append({"project_id": pid, "name": project or pid, "type": "auto_intake",
                             "status": "intake_pending", "owner": "DATA1",
                             "source": [str(pdir / "source")], "manifest_path": str(pdir / "manifest.json")})
            assert_safe(REG, kb_root)
            REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
        receipt["registered"] = True
    # 回执
    if write:
        date = datetime.now().strftime("%Y%m%d")
        rp = REPORTS / f"learning_intake_{date}.json"
        assert_safe(rp, kb_root)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
        receipt["receipt"] = str(rp)
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "ingest"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--project", default=None)
    ap.add_argument("--root", default=str(DEFAULT_KB))
    ap.add_argument("--no-pii-clean", action="store_true", help="仅排障：跳过入仓前 PII 清洗")
    args = ap.parse_args(argv)
    kb = Path(args.root)
    files = scan(kb)
    if args.cmd == "scan":
        print(json.dumps({"inbox": str(kb / '05_学习源件' / '_inbox'), "files": files,
                          "count": len(files)}, ensure_ascii=False, indent=1))
        return 0
    rep = ingest(files, kb, project=args.project, write=args.write,
                 no_pii_clean=args.no_pii_clean)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
