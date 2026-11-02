# -*- coding: utf-8 -*-
"""学习数据后流程门禁（v2）：阶段门 + 发布门（阻断式）。

用法：
  python tools/kb_corpus/learning_gate.py check [--stage all|intake|units|candidates|publish|archive]
                                                [--root KB] [--write] [--strict]
返回：0=全过；1=有失败（--strict 时任务应视为失败）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_KB = Path(r"C:\项目\知识库")
REG = REPO / "agent_platform/registry/learning_projects.json"
SCHEMAS = REPO / "docs/standards/schemas"
REPORTS = REPO / "research/reports"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def validate(obj, schema: dict, path="$") -> list[str]:
    """极简 JSON Schema 校验（required/type/enum/const/minLength/items/minItems）。"""
    errors = []
    t = schema.get("type")
    if t == "object" and not isinstance(obj, dict):
        return [f"{path}: expected object"]
    if t == "array" and not isinstance(obj, list):
        return [f"{path}: expected array"]
    if t == "string" and not isinstance(obj, str):
        return [f"{path}: expected string"]
    if t == "integer" and not isinstance(obj, int):
        return [f"{path}: expected integer"]
    if t == "boolean" and not isinstance(obj, bool):
        return [f"{path}: expected boolean"]
    if "const" in schema and obj != schema["const"]:
        errors.append(f"{path}: const {schema['const']!r} != {obj!r}")
    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{path}: enum {schema['enum']} != {obj!r}")
    if isinstance(obj, str) and "minLength" in schema and len(obj) < schema["minLength"]:
        errors.append(f"{path}: minLength {schema['minLength']}")
    if isinstance(obj, list) and "minItems" in schema and len(obj) < schema["minItems"]:
        errors.append(f"{path}: minItems {schema['minItems']}")
    if isinstance(obj, dict):
        for req in schema.get("required", []):
            if req not in obj:
                errors.append(f"{path}: missing required '{req}'")
        for k, sub in (schema.get("properties") or {}).items():
            if k in obj:
                errors += validate(obj[k], sub, f"{path}.{k}")
    if isinstance(obj, list) and "items" in schema:
        for i, it in enumerate(obj):
            errors += validate(it, schema["items"], f"{path}[{i}]")
    return errors


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def check_intake(kb: Path, pid: str) -> list[str]:
    errs = []
    man = kb / "05_学习源件" / pid / "manifest.json"
    if not man.exists():
        if (kb / "05_学习源件" / pid).exists():
            return [f"manifest missing: {man}"]
        return []  # 项目仅登记、尚未投文件
    data = json.loads(man.read_text(encoding="utf-8"))
    data.setdefault("project_id", pid)
    data.setdefault("files", [])
    data.setdefault("schema_version", "learning_source_manifest.v1")
    data.setdefault("generated_at", datetime.fromtimestamp(man.stat().st_mtime).astimezone().isoformat(timespec="seconds"))
    errs += validate(data, load_schema("learning_source_manifest.schema.json"))
    for f in data.get("files") or []:
        if f.get("copied") and not (kb / "05_学习源件" / pid / "source" / f.get("name", "")).exists():
            errs.append(f"source missing: {f.get('name')}")
        if len(str(f.get("sha256") or "")) != 64:
            errs.append(f"sha256 bad: {f.get('name')}")
    return errs


def check_units(kb: Path, pid: str) -> list[str]:
    errs = []
    units = kb / "30_知识单元" / f"{pid}_units_draft.jsonl"
    if not units.exists():
        return [f"units draft missing: {units.name}"]
    sch = load_schema("learning_unit.schema.json")
    text_dir = kb / "20_清洗" / pid / "text"
    text_files = {p.name for p in text_dir.glob("*")} if text_dir.exists() else set()
    n = 0
    for line in units.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n += 1
        try:
            obj = json.loads(line)
        except ValueError:
            errs.append(f"units line {n}: invalid json")
            continue
        obj.setdefault("schema_version", "learning_unit.v1")
        errs += [f"unit {obj.get('unit_id')}: {e}" for e in validate(obj, sch)]
        ev = (obj.get("evidence") or {}).get("file")
        if ev and text_files and ev not in text_files:
            errs.append(f"unit {obj.get('unit_id')}: evidence file not in text dir ({ev})")
    if n == 0:
        errs.append("units empty")
    return errs[:20]


def check_candidates(kb: Path, pid: str) -> list[str]:
    candidates = sorted(REPORTS.glob(f"card_candidates_{pid}*.json"))
    if not candidates:
        return []
    errs = []
    d = json.loads(candidates[-1].read_text(encoding="utf-8"))
    items = d.get("candidates") or d.get("cards") or []
    if not items:
        return [f"candidates empty: {candidates[-1].name}"]
    sch = load_schema("learning_card_candidate.schema.json")
    seen = set()
    for c in items:
        obj = dict(c)
        obj.setdefault("schema_version", "learning_card_candidate.v1")
        obj.setdefault("project_id", pid)
        obj.setdefault("card_id", c.get("card_id") or c.get("proposal_id") or "?")
        obj.setdefault("traceable", bool(obj.get("evidence_pointer") or obj.get("evidence")))
        obj.setdefault("precheck", {"action": (c.get("review") or {}).get("checkboxes") and "pass" or "pending_review",
                                    "issues": []})
        if obj["precheck"].get("action") not in ("pass", "revise", "reject", "pending_review"):
            obj["precheck"]["action"] = "pending_review"
        if obj.get("status") not in ("pending_review", "pass", "revise", "reject"):
            obj["status"] = "pending_review"
        errs += [f"cand {obj.get('card_id')}: {e}" for e in validate(obj, sch)]
        key = str(obj.get("concept") or "").strip()
        if key in seen:
            errs.append(f"cand duplicate concept in batch: {key}")
        seen.add(key)
    if len(errs) > 20:
        errs = errs[:20]
    return errs


def check_publish(kb: Path, pid: str) -> list[str]:
    errs = []
    cards_p = REPO / "portal/static/kb_learning_cards.json"
    cards = json.loads(cards_p.read_text(encoding="utf-8")).get("cards") or []
    concepts = [str(c.get("concept") or "") for c in cards]
    if len(concepts) != len(set(concepts)):
        errs.append("learning cards duplicate concept")
    for c in cards:
        if not all(c.get(f) for f in ("q", "a", "concept", "source", "topic")):
            errs.append(f"card fields missing: {c.get('concept')}")
            break
    dec = sorted(REPORTS.glob(f"*decisions*{pid}*.json")) + sorted(REPORTS.glob(f"card_decisions_{pid}*.json"))
    dec = [p for p in dec if p.exists()]
    if dec:
        d = json.loads(dec[-1].read_text(encoding="utf-8"))
        errs += validate(d, load_schema("learning_decisions.schema.json"))
    return errs


def check_archive(kb: Path, proj: dict) -> list[str]:
    errs = []
    arch = proj.get("archive")
    if not arch:
        return [f"archive path missing in registry for {proj.get('project_id')}"]
    p = REPO / arch if not Path(arch).is_absolute() else Path(arch)
    entry = p / "ENTRY.md"
    man = p / "manifest.json"
    if not entry.exists():
        errs.append(f"ENTRY.md missing: {p}")
    if not man.exists():
        errs.append(f"archive manifest missing: {p}")
    return errs


def run(kb: Path, stage: str = "all", write: bool = False) -> dict:
    reg = json.loads(REG.read_text(encoding="utf-8"))
    res = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "stage": stage, "projects": [], "summary": {"projects": 0, "checks": 0, "fail": 0}}
    for proj in reg.get("projects", []):
        pid = proj.get("project_id")
        status = proj.get("status")
        p = {"project_id": pid, "status": status, "stages": {}, "fail": 0}
        if status == "archived":
            stages = ["archive"]
        elif stage == "all":
            stages = ["intake", "units", "candidates", "publish"]
        else:
            stages = [stage]
        for st in stages:
            if st == "intake":
                errs = check_intake(kb, pid)
            elif st == "units":
                errs = check_units(kb, pid)
            elif st == "candidates":
                errs = check_candidates(kb, pid)
            elif st == "publish":
                errs = check_publish(kb, pid)
            else:
                errs = check_archive(kb, proj)
            p["stages"][st] = {"ok": not errs, "errors": errs}
            if errs:
                p["fail"] += 1
        res["projects"].append(p)
        res["summary"]["projects"] += 1
        res["summary"]["checks"] += len(stages)
        res["summary"]["fail"] += p["fail"]
    res["ok"] = res["summary"]["fail"] == 0
    if write:
        tag = datetime.now().strftime("%Y%m%d")
        rp = REPORTS / f"learning_gate_{tag}.json"
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        L = ["# 学习门禁报告", "", f"> {res['generated_at']}｜stage={stage}｜"
             f"项目 {res['summary']['projects']}｜检查 {res['summary']['checks']}｜失败 {res['summary']['fail']}", ""]
        for p in res["projects"]:
            L.append(f"## {p['project_id']}（{p['status']}）")
            for st, v in p["stages"].items():
                L.append(f"- {'✅' if v['ok'] else '❌'} {st}" + ("（" + "; ".join(v["errors"][:3]) + "）" if v["errors"] else ""))
            L.append("")
        (REPORTS / f"learning_gate_{tag}.md").write_text("\n".join(L), encoding="utf-8")
        res["receipt"] = str(rp)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="check", choices=["check"])
    ap.add_argument("--stage", default="all", choices=["all", "intake", "units", "candidates", "publish", "archive"])
    ap.add_argument("--root", default=str(DEFAULT_KB))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args(argv)
    rep = run(Path(args.root), stage=args.stage, write=args.write)
    print(json.dumps(rep, ensure_ascii=False, indent=1)[:4000])
    return 0 if (rep["ok"] or not args.strict) else 1


if __name__ == "__main__":
    sys.exit(main())
