# -*- coding: utf-8 -*-
"""A4 基线产物清单 + 复算（P0⑤ · WO-LCS1-A4A6-20261027）。

给 ⑤ 全量跑批的产物做**可复算 sha256 清单**：
  · 文件：全文 sha256 + size；
  · 目录：小目录（≤256MB）对每个文件全文哈希后聚合；大目录用（相对路径+size）指纹；
  · 清单记录 git HEAD / 生成时间 / 工具命令，供 A4 验收与 BACK1/ACAD1 复核。

用法：
  python -X utf8 quant_backtest/baseline_manifest.py --out work/lcs1_baseline/full/MANIFEST.json \
      work/lcs1_baseline/full/baseline_summary.json work/lcs1_baseline/full/baseline_metrics.csv
  python -X utf8 quant_backtest/baseline_manifest.py --verify work/lcs1_baseline/full/MANIFEST.json
退出码：0 成功/复算一致；1 复算不一致；2 输入错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

SCHEMA = "baseline_manifest/v1"
BIG_DIR_BYTES = 256 * 1024 * 1024
BIG_DIR_FILES = 2000


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def entry_for(path: Path) -> dict:
    if path.is_file():
        return {"path": str(path), "type": "file", "size": path.stat().st_size,
                "files": 1, "sha256": sha256_file(path), "mode": "content"}
    if not path.is_dir():
        raise SystemExit(json.dumps({"ok": False, "error": "not_found: %s" % path},
                                    ensure_ascii=False))
    files = sorted(p for p in path.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    h = hashlib.sha256()
    big = total > BIG_DIR_BYTES or len(files) > BIG_DIR_FILES
    for p in files:
        rel = str(p.relative_to(path)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if big:
            h.update(str(p.stat().st_size).encode("ascii"))
        else:
            h.update(sha256_file(p).encode("ascii"))
        h.update(b"\n")
    return {"path": str(path), "type": "dir", "size": total, "files": len(files),
            "sha256": h.hexdigest(), "mode": "metadata" if big else "content"}


def build(paths: list[str], label: str = "", out: str = "") -> dict:
    entries = [entry_for(Path(p)) for p in paths]
    manifest = {"schema": SCHEMA, "label": label,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "git_head": _git_head(), "tool": "quant_backtest/baseline_manifest.py",
                "entries": entries}
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        manifest["written"] = str(p)
    return {"ok": True, "manifest": manifest}


def verify(manifest_path: str) -> dict:
    try:
        m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(json.dumps({"ok": False, "error": "manifest_read_failed: %s" % exc},
                                    ensure_ascii=False))
    problems = []
    for e in m.get("entries") or []:
        try:
            now = entry_for(Path(e["path"]))
        except SystemExit:
            problems.append({"path": e.get("path"), "error": "missing"})
            continue
        if now["sha256"] != e.get("sha256"):
            problems.append({"path": e.get("path"), "error": "sha256_mismatch",
                             "expect": e.get("sha256"), "actual": now["sha256"]})
        if now["size"] != e.get("size"):
            problems.append({"path": e.get("path"), "error": "size_mismatch"})
    return {"ok": not problems, "manifest": manifest_path,
            "entries": len(m.get("entries") or []), "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A4 基线产物 sha256 清单/复算")
    ap.add_argument("--out", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--verify", default="")
    ap.add_argument("paths", nargs="*")
    args = ap.parse_args(argv)
    if args.verify:
        res = verify(args.verify)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    if not args.paths:
        ap.error("至少给一个文件/目录（或 --verify）")
    res = build(args.paths, args.label, args.out)
    print(json.dumps({"ok": True, "entries": len(res["manifest"]["entries"]),
                      "written": res["manifest"].get("written", ""),
                      "manifest": res["manifest"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
