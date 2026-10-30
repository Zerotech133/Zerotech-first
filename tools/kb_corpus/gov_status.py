# -*- coding: utf-8 -*-
"""语料库治理状态 → 门户静态 JSON（零重启方案，2026-10-30）。

写 portal/static/kb_corpus_gov.json；由 backup_kb_corpus.py 每次备份后调用刷新。
页面 /kb_corpus.html 优先读该静态文件，读不到再回退 /api/kb_corpus/stats 的 governance 字段。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
KB = Path(r"C:\项目\知识库")
REPORT = KB / "90_报告"
VAULT = Path(r"C:\项目\_vault\kb_corpus")
OUT = REPO / "portal" / "static" / "kb_corpus_gov.json"


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def build_gov() -> dict:
    p0 = read_json(REPORT / "p0_verify_report.json") or {}
    p1 = read_json(REPORT / "p1_export_report.json") or {}
    p2 = read_json(REPORT / "p2_clean_report.json") or {}
    full = read_json(KB / "50_RAG" / "packed_full" / "meta.json") or {}
    backups = []
    try:
        backups = sorted([d for d in VAULT.iterdir() if d.is_dir()], key=lambda x: x.name)
    except Exception:  # noqa: BLE001
        backups = []
    latest = None
    if backups:
        rec = read_json(backups[-1] / "receipt.json") or {}
        latest = {"stamp": backups[-1].name, "result": rec.get("result"),
                  "files": rec.get("total_files"), "bytes": rec.get("total_bytes")}
    registered = False
    try:
        registered = "youtube_quant_corpus" in (REPO / "agent_platform" / "catalogs" / "data_catalog.yaml").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        registered = False
    c = p0.get("counts") or {}
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "verify": {"zips": c.get("zips"), "hash_ok": c.get("hash_ok"),
                   "hash_bad": c.get("hash_bad"), "snapshots": c.get("snapshots")},
        "normalized": {"videos": p1.get("videos"), "comments": p1.get("comments"),
                       "channels": p1.get("channels")},
        "cleaned": {"kept": p2.get("kept"), "retention_pct": p2.get("retention_pct"),
                    "pii_masked": p2.get("pii")},
        "index_full": {"rows": full.get("rows"), "dim": full.get("dim")},
        "backup": latest,
        "catalog_registered": registered,
        "lineage_manifest": "agent_platform/catalogs/youtube_quant_corpus_lineage.yaml",
    }


def main() -> int:
    gov = build_gov()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(gov, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "verify": gov["verify"], "backup": gov["backup"],
                      "catalog_registered": gov["catalog_registered"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
