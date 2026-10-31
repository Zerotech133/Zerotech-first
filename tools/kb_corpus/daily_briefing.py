# -*- coding: utf-8 -*-
"""每日研报简报生成器：从研报知识库取最新内容，生成简报 md + JSON（供飞书/门户推送）。

用法：python tools/kb_corpus/daily_briefing.py [--top 6]
产出：
  C:/项目/知识库/70_简报/briefing_YYYYMMDD.md
  portal/static/kb_daily_briefing.json
飞书推送：由推送任务读取 JSON 发送（本脚本只生成，不直发）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
REPORTS = Path(r"C:/项目/PDF/_ingest_reports")
OUT_DIR = Path(r"C:/项目/知识库/70_简报")
OUT_JSON = REPO / "portal" / "static" / "kb_daily_briefing.json"


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=6)
    args = ap.parse_args(argv)
    # 取页数最大/最新文档的章节标题 + 知识单元
    docs = []
    for d in sorted(p for p in REPORTS.glob("*") if p.is_dir()):
        st = read_json(d / "structure.json") or {}
        ku = d / "knowledge_units.jsonl"
        units = []
        if ku.exists():
            for line in ku.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        units.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        pass
        if st or units:
            docs.append({"doc": d.name, "pages": int(st.get("pages") or 0),
                         "chapters": [c.get("title") for c in (st.get("chapters") or [])][:3],
                         "units": units[:3]})
    docs = sorted(docs, key=lambda x: -x["pages"])[: args.top]
    stamp = datetime.now().strftime("%Y%m%d")
    md = [f"# 研报简报 {stamp}", "", f"> 来源：研报知识库 {len(docs)} 篇（按页数）+ 知识单元抽取；仅内部研究。", ""]
    for i, doc in enumerate(docs, 1):
        md.append(f"## {i}. {doc['doc']}（{doc['pages']} 页）")
        if doc["chapters"]:
            md.append("- 章节：" + " / ".join(str(c) for c in doc["chapters"] if c))
        for u in doc["units"]:
            md.append(f"- **{u.get('concept') or u.get('chapter') or '要点'}**（p{u.get('page_start')}-{u.get('page_end')}）：{u.get('claim','')}")
        md.append("")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / f"briefing_{stamp}.md"
    p.write_text("\n".join(md), encoding="utf-8")
    payload = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "date": stamp, "docs": docs[: args.top], "file": str(p),
               "feishu_ready": True, "note": "由推送任务/人工读取本 JSON 发送，脚本不直发"}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"简报 {p} | docs={len(docs)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
