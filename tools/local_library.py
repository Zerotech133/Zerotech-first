#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地个人库读取/本地总结适配器（云边协同 v1）。

原则：
- 只读本地目录，不写用户库；不跟随符号链接；不越出 root；
- 生成 LocalBrief：元数据 + 摘录 + 稳定摘要 + 证据 hash；
- 默认本地输出，不上传原始内容；云端只应消费摘要/证据指针（由上层另行授权）。

用法：
  python -X utf8 tools/local_library.py --user tf --root C:\\项目\\_local_libraries\\tf --out C:\\Temp\\tf_local_brief.json
  python -X utf8 tools/local_library.py --user tf --root ... --json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

SCHEMA = "hq/local_brief/v1"
MANIFEST_SCHEMA = "hq/local_library/v1"
CLOUD_SCHEMA = "hq/local_brief_cloud/v1"
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log"}
MAX_FILES = 200
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 5_000_000


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_manifest(root: Path) -> dict | None:
    p = Path(root) / "library.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ValueError("manifest_invalid: %s" % str(e)[:120]) from e
    if not isinstance(d, dict):
        raise ValueError("manifest_invalid")
    if d.get("schema") not in (None, MANIFEST_SCHEMA):
        raise ValueError("manifest_schema")
    return d


def _safe_files(root: Path, manifest: dict | None = None) -> list[Path]:
    root = Path(root)
    sources = (manifest or {}).get("sources") or ["."]
    excludes = (manifest or {}).get("exclude") or []
    if not isinstance(sources, list) or not sources:
        sources = ["."]
    if not isinstance(excludes, list):
        excludes = []
    roots = []
    for src in sources:
        src = str(src).strip()
        if not src or os.path.isabs(src) or ".." in Path(src).parts:
            raise ValueError("manifest_source_invalid")
        cand = (root / src).resolve()
        if not _inside(root, cand) or not cand.is_dir():
            raise ValueError("manifest_source_outside")
        roots.append(cand)
    ex_parts = [str(x).replace("\\", "/").strip("/") for x in excludes if str(x).strip()]
    out = []
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not d.startswith("__")]
            for name in filenames:
                p = Path(dirpath) / name
                if p.is_symlink() or p.name.startswith(".") or name == "library.json":
                    continue
                if p.suffix.lower() not in TEXT_EXT:
                    continue
                if not _inside(root, p):
                    continue
                rel = str(p.resolve().relative_to(root.resolve())).replace("\\", "/")
                if any(rel == x or rel.startswith(x + "/") for x in ex_parts):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size <= 0 or st.st_size > MAX_FILE_BYTES:
                    continue
                out.append(p)
                if len(out) >= MAX_FILES:
                    return sorted(set(out), key=lambda x: str(x).lower())
    return sorted(set(out), key=lambda x: str(x).lower())


def _doc(root: Path, p: Path) -> dict | None:
    try:
        raw = p.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    rel = str(p.relative_to(root)).replace("\\", "/")
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    heads = [x.lstrip("#").strip() for x in lines if x.startswith("#")][:8]
    title = heads[0] if heads else (p.stem if p.suffix.lower() not in (".json", ".csv") else p.stem)
    excerpt = "\n".join(lines[:8])[:600]
    return {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "mtime": datetime.datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "title": title[:120], "headings": heads, "excerpt": excerpt}


def build_brief(user: str, root: Path, manifest: dict | None = None) -> dict:
    root = Path(root)
    if not user or not all(c.isalnum() or c in "-_." for c in user):
        raise ValueError("bad_user")
    if not root.is_dir():
        raise ValueError("local_library_missing")
    mf = manifest if manifest is not None else load_manifest(root)
    if mf is not None and mf.get("user_id") and str(mf.get("user_id")) != str(user):
        raise ValueError("manifest_user_mismatch")
    if mf is not None and mf.get("enabled") is False:
        raise ValueError("manifest_disabled")
    docs, total = [], 0
    for p in _safe_files(root, mf):
        d = _doc(root, p)
        if not d:
            continue
        if total + d["bytes"] > MAX_TOTAL_BYTES:
            break
        total += d["bytes"]
        docs.append(d)
    by_kind = {}
    for d in docs:
        k = Path(d["path"]).suffix.lower() or "other"
        by_kind[k] = by_kind.get(k, 0) + 1
    titles = [d["title"] for d in docs[:10]]
    summary = "本地库共 %d 个文档；主题：%s" % (len(docs), "、".join(titles) if titles else "暂无文档")
    return {
        "schema": SCHEMA,
        "user_id": user,
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "privacy": {"mode": "local_only",
                    "upload": (mf or {}).get("upload", {}).get("mode", "summary_only") if isinstance((mf or {}).get("upload"), dict) else "summary_only",
                    "raw_uploaded": False},
        "manifest": {"present": mf is not None, "sources": (mf or {}).get("sources")},
        "root": str(root),
        "stats": {"files": len(docs), "bytes": total, "by_kind": by_kind},
        "documents": docs,
        "summary": summary,
        "notice": "本文件仅本地生成；上云需用户显式授权，且只应上传 summary/evidence，不上传原始文档。",
    }


def cloud_payload(brief: dict) -> dict:
    """只导出可上云摘要/证据指针：无原文、无本地路径、无 root。"""
    docs = brief.get("documents") or []
    return {
        "schema": CLOUD_SCHEMA,
        "user_id": brief.get("user_id"),
        "generated_at": brief.get("generated_at"),
        "privacy": {"mode": "cloud_safe", "raw_uploaded": False, "confirmed": False},
        "stats": brief.get("stats"),
        "summary": brief.get("summary"),
        "evidence": [{"sha256": d.get("sha256"), "title": d.get("title"),
                      "headings": (d.get("headings") or [])[:8], "bytes": d.get("bytes")}
                     for d in docs],
        "notice": "仅 summary/evidence 可上云；原文、路径、root 不上传；confirmed 需用户显式授权后置 true。",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="本地个人库读取/总结")
    ap.add_argument("--user", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--export-cloud", default="")
    ap.add_argument("--authorize", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        d = build_brief(a.user, Path(a.root))
    except Exception as e:  # noqa: BLE001
        print("local_library_error: %s" % str(e)[:300], file=sys.stderr)
        return 2
    if a.export_cloud:
        if not a.authorize:
            print("cloud_export_requires_authorize", file=sys.stderr)
            return 2
        cp = cloud_payload(d)
        cp["privacy"]["confirmed"] = True
        Path(a.export_cloud).parent.mkdir(parents=True, exist_ok=True)
        Path(a.export_cloud).write_text(json.dumps(cp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote " + a.export_cloud)
        return 0
    text = json.dumps(d, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print("wrote " + a.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
