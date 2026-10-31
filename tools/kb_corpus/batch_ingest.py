# -*- coding: utf-8 -*-
"""批量入库驱动：逐文件子进程隔离（防原生崩溃拖垮整批），可续跑。

用法：
  python tools/kb_corpus/batch_ingest.py --pdf-dir <dir> --out-dir <dir> [--stages ocr,structure,distill,index]
每个文件的 ocr/structure 在独立子进程中运行；失败记录后继续（已产出的文件天然跳过）。
产出 <out-dir>/_batch_report.json。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
CLI = REPO / "tools" / "kb_corpus" / "pdf_ingest.py"


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=str(REPO))
        tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
        return r.returncode, tail[0][:180]
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--stages", default="ocr,structure")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    pdfs = sorted(Path(args.pdf_dir).glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "pdf_dir": args.pdf_dir, "out_dir": args.out_dir, "files": {}, "fail": []}
    t0 = time.time()
    for i, pdf in enumerate(pdfs, 1):
        rec = {}
        for st in stages:
            cmd = [sys.executable, "-X", "utf8", str(CLI), st,
                   "--pdf-dir", args.pdf_dir, "--out-dir", args.out_dir, "--only", pdf.stem]
            rc, tail = run(cmd)
            rec[st] = {"rc": rc, "tail": tail}
            if rc != 0:
                report["fail"].append({"file": pdf.name, "stage": st, "rc": rc, "tail": tail})
                break
        report["files"][pdf.name] = rec
        if i % 10 == 0 or rec.get(stages[-1], {}).get("rc", 0) != 0:
            print(f"[{i}/{len(pdfs)}] {pdf.name} -> {rec.get(stages[-1], {}).get('rc')}  {time.time()-t0:.0f}s", flush=True)
    (Path(args.out_dir) / "_batch_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = len(pdfs) - len({f["file"] for f in report["fail"]})
    print(json.dumps({"files": len(pdfs), "ok": ok, "fail": len({f['file'] for f in report['fail']}),
                      "seconds": round(time.time() - t0, 1)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
