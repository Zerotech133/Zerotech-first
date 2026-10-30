# -*- coding: utf-8 -*-
"""知识库派生层备份（Phase6，2026-10-30）。

备份对象：C:/项目/知识库 的派生层（10_规范化 parquet、20_清洗、40_索引、50_RAG 精简、90_报告、30_知识单元）
           + 原始包 SHA256 清单（不备份 2.7GB 原始 ZIP，仅清单）
排除：01_数据分包 原始 ZIP；comments.jsonl（与 parquet 重复）；packed_full（可由 shards 重建）
保留：_vault/kb_corpus 下最近 3 套
输出：C:/项目/_vault/kb_corpus/<stamp>/ + receipt.json + RESULT: OK
失败：C:/Temp/KB_CORPUS_BACKUP_FAILED.flag
用法：python jev-eval/backup_kb_corpus.py [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

KB = Path("C:/项目/知识库")
VAULT = Path("C:/项目/_vault/kb_corpus")
FLAG = Path("C:/Temp/KB_CORPUS_BACKUP_FAILED.flag")
LOG = Path("C:/Temp/kb_corpus_backup.log")
KEEP = 3


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def copy_tree(src: Path, dst: Path, skip: set[str]) -> dict:
    stats = {"files": 0, "bytes": 0}
    if not src.exists():
        return stats
    for item in src.rglob("*"):
        if not item.is_file():
            continue
        if item.name in skip:
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        stats["files"] += 1
        stats["bytes"] += item.stat().st_size
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = VAULT / stamp
    plan = {
        "10_规范化": {"skip": {"comments.jsonl", "videos.jsonl", "video_channel_map.jsonl"}},
        "20_清洗": {"skip": set()},
        "30_知识单元": {"skip": set()},
        "40_索引": {"skip": set()},
        "50_RAG": {"skip": set()},
        "90_报告": {"skip": set()},
    }
    result = {"stamp": stamp, "dry_run": bool(args.dry_run), "layers": {}, "raw_manifest": None}
    try:
        # 原始包 sha256 清单
        raw_dir = KB / "01_数据分包"
        manifest = dest / "raw_sha256_manifest.txt"
        lines = []
        for f in sorted(raw_dir.rglob("*.sha256")):
            lines.append("%s  %s" % (f.name.replace(".sha256", ""), f.read_text(encoding="utf-8", errors="replace").strip().split()[0]))
        result["raw_manifest"] = {"files": len(lines), "sample": lines[:2]}
        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0
        dest.mkdir(parents=True, exist_ok=True)
        if lines:
            manifest.write_text("\n".join(lines), encoding="utf-8")
        for layer, cfg in plan.items():
            src = KB / layer
            sub = dest / layer
            stats = copy_tree(src, sub, cfg["skip"])
            # 50_RAG 精简：排除大体积可重建的 packed_full 与 packed/*.f32? 保留 packed（20MB）
            if layer == "50_RAG":
                extra = sub / "packed_full"
                if extra.exists():
                    shutil.rmtree(extra, ignore_errors=True)
                    stats["note"] = "packed_full excluded (rebuildable)"
            result["layers"][layer] = stats
            log("layer %s: %s" % (layer, stats))
        # 关键文件哈希
        key_files = []
        for rel in ("10_规范化/videos.parquet", "50_RAG/rag_index_corpus.json", "40_索引/full/progress.json"):
            p = dest / rel
            if p.exists():
                key_files.append({"file": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size})
        total_files = sum(v.get("files", 0) for v in result["layers"].values())
        total_bytes = sum(v.get("bytes", 0) for v in result["layers"].values())
        result.update({"total_files": total_files, "total_bytes": total_bytes,
                       "key_files": key_files, "result": "OK"})
        (dest / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        log("RESULT: OK files=%d bytes=%d dest=%s" % (total_files, total_bytes, dest))
        # 保留 3 套
        sets = sorted([d for d in VAULT.iterdir() if d.is_dir()])
        for old in sets[:-KEEP]:
            shutil.rmtree(old, ignore_errors=True)
            log("pruned %s" % old.name)
        print(json.dumps({"result": "OK", "files": total_files, "bytes": total_bytes, "dest": str(dest)},
                         ensure_ascii=False, indent=1))
        return 0
    except Exception as e:  # noqa: BLE001
        FLAG.parent.mkdir(parents=True, exist_ok=True)
        FLAG.write_text("KB_CORPUS_BACKUP_FAILED %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), e), encoding="utf-8")
        log("RESULT: FAILED %s" % e)
        print(json.dumps({"result": "FAILED", "error": str(e)}, ensure_ascii=False), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
