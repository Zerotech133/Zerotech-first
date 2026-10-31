# -*- coding: utf-8 -*-
"""剩余研报知识单元蒸馏（跳过已蒸馏），后台批跑；失败记录不中断。"""
import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:/项目/老田的AI助手")
OUT = Path(r"C:/项目/PDF/_ingest_reports")
PDFDIR = REPO / "inbox" / "docs" / "done"
CLI = REPO / "tools" / "kb_corpus" / "pdf_ingest.py"
PY = r"C:/项目/py312/python.exe"

manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
items = sorted([i for i in manifest["items"] if i.get("pages")], key=lambda x: -int(x["pages"]))
todo = []
for it in items:
    stem = Path(it["file"]).stem
    ku = OUT / stem / "knowledge_units.jsonl"
    if ku.exists() and ku.stat().st_size > 0:
        continue
    if not (OUT / stem / "structure.json").exists():
        continue
    todo.append(stem)
print("todo:", len(todo), flush=True)
fails = []
for n, stem in enumerate(todo, 1):
    r = subprocess.run([PY, "-X", "utf8", "-u", str(CLI), "distill", "--pdf-dir", str(PDFDIR),
                        "--out-dir", str(OUT), "--only", stem, "--chapters", "2"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    ok = r.returncode == 0
    if not ok:
        fails.append(stem)
    if n % 5 == 0 or not ok:
        print(f"[{n}/{len(todo)}] {stem} rc={r.returncode}", flush=True)
print(json.dumps({"todo": len(todo), "fails": fails}, ensure_ascii=False), flush=True)
