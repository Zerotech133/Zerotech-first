# -*- coding: utf-8 -*-
"""学习项目入库监听（SOP S0/S1 自动化脚手架）。

扫描 C:\\项目\\PDF 与 知识库 指定目录的新源件（PDF/MD/DOCX），与
agent_platform/registry/learning_projects.json 比对，输出「待登记」清单；
默认 dry-run 只报告，--write 才把 intake_pending 新项目登记进注册表。

用法：
  python tools/kb_corpus/learning_intake_watch.py            # dry-run 报告
  python tools/kb_corpus/learning_intake_watch.py --write    # 登记新项目
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REG = REPO / "agent_platform/registry/learning_projects.json"
SUPPLIERS = [Path(r"C:\\项目\\PDF"), Path(r"C:\\项目\\知识库")]
EXTS = {".pdf", ".md", ".docx", ".txt"}


def known_sources(reg: dict) -> set[str]:
    out = set()
    for p in reg.get("projects", []):
        for s in p.get("source") or []:
            out.add(str(Path(s)).lower())
    return out


def scan() -> list[dict]:
    found = []
    for root in SUPPLIERS:
        if not root.exists():
            continue
        for f in root.glob("*"):
            if f.is_file() and f.suffix.lower() in EXTS:
                found.append({"path": str(f), "name": f.name, "size": f.stat().st_size,
                              "mtime": datetime.fromtimestamp(f.stat().st_mtime).astimezone().isoformat(timespec="seconds")})
    return sorted(found, key=lambda x: x["mtime"], reverse=True)


def report(dry_run=True) -> dict:
    reg = json.loads(REG.read_text(encoding="utf-8")) if REG.exists() else {"projects": []}
    known = known_sources(reg)
    files = scan()
    new = [f for f in files if f["path"].lower() not in known]
    out = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "scanned": len(files), "known": len(known), "new": new, "dry_run": dry_run}
    if not dry_run and new:
        for f in new:
            pid = "intake_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + str(len(reg["projects"]))
            reg["projects"].append({"project_id": pid, "name": f["name"], "type": "auto_intake",
                                    "status": "intake_pending", "owner": "DATA1",
                                    "source": [f["path"]], "discovered_at": f["mtime"]})
        reg["generated_at"] = out["generated_at"]
        REG.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="登记新项目（默认 dry-run）")
    args = ap.parse_args(argv)
    rep = report(dry_run=not args.write)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
