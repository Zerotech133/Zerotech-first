# -*- coding: utf-8 -*-
"""知识单元人工复核表生成器：随机抽样 N 条，附原页文本摘录，供人工对照。

用法：python tools/kb_corpus/review_sheet.py [--n 20] [--seed 20261101]
产出：
  C:/项目/知识库/80_复核/review_YYYYMMDD.md（表格：卡号/出处/claim/原文摘录/结论勾选）
  C:/项目/知识库/80_复核/review_YYYYMMDD.json（结构化，verdict 字段留空待填）
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
ROOTS = [Path(r"C:/项目/PDF/_ingest"), Path(r"C:/项目/PDF/_ingest_reports")]
OUT_DIR = Path(r"C:/项目/知识库/80_复核")


def page_text(d: Path, start: int, end: int, limit: int = 600) -> str:
    txt = d / "ocr.txt"
    if txt.exists():
        pages = {}
        cur, buf = 0, []
        for line in txt.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^=== 第(\d+)页 ===$", line.strip())
            if m:
                if cur:
                    pages[cur] = "\n".join(buf)
                cur, buf = int(m.group(1)), []
            elif cur:
                buf.append(line)
        if cur:
            pages[cur] = "\n".join(buf)
        s = "\n".join(pages.get(p, "") for p in range(start, min(end, start + 3) + 1))
        return re.sub(r"\s+", " ", s)[:limit]
    lay = d / "layout.jsonl"
    if lay.exists():
        parts = []
        for line in lay.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if start <= int(rec.get("page") or 0) <= end:
                parts += [str(b.get("text") or "") for b in (rec.get("blocks") or [])]
        return re.sub(r"\s+", " ", " ".join(parts))[:limit]
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261101)
    args = ap.parse_args(argv)
    pool = []
    for root in ROOTS:
        if not root.exists():
            continue
        for d in sorted(p for p in root.glob("*") if p.is_dir()):
            ku = d / "knowledge_units.jsonl"
            if not ku.exists():
                continue
            for line in ku.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    u = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                u["_dir"] = str(d)
                pool.append(u)
    if not pool:
        print("no units", flush=True)
        return 1
    random.seed(args.seed)
    sample = random.sample(pool, min(args.n, len(pool)))
    items = []
    for i, u in enumerate(sample, 1):
        d = Path(u["_dir"])
        start = int(u.get("page_start") or 0)
        end = int(u.get("page_end") or start)
        items.append({"no": i, "book": u.get("book") or d.name, "chapter": u.get("chapter", ""),
                      "pages": [start, end], "concept": u.get("concept", ""),
                      "claim": u.get("claim", ""), "evidence": u.get("evidence", ""),
                      "source_excerpt": page_text(d, start, end),
                      "verdict": "", "note": ""})
    stamp = datetime.now().strftime("%Y%m%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md = [f"# 知识单元人工复核表 {stamp}（{len(items)} 条）", "",
          "> 复核方法：对照「原文摘录」判断 claim 是否有据；判定填 `通过`/`需修正`/`不成立`；发现错误请在「备注」写明。", "",
          "| # | 出处 | 概念 | 断言（claim） | 原文摘录 | 判定 | 备注 |",
          "|---|---|---|---|---|---|---|"]
    for it in items:
        esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")[:300]
        md.append(f"| {it['no']} | {esc(it['book'])} · {esc(it['chapter'])} p{it['pages'][0]}-{it['pages'][1]} "
                  f"| {esc(it['concept'])} | {esc(it['claim'])} | {esc(it['source_excerpt'])} | ☐通过 ☐需修正 ☐不成立 |  |")
    p = OUT_DIR / f"review_{stamp}.md"
    p.write_text("\n".join(md), encoding="utf-8")
    (OUT_DIR / f"review_{stamp}.json").write_text(
        json.dumps({"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "seed": args.seed, "total_pool": len(pool), "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"复核表 {p} | 抽样 {len(items)}/{len(pool)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
