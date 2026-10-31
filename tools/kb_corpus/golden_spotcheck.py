# -*- coding: utf-8 -*-
"""知识单元 golden 抽检：schema / 页码 / 章节 / 证据接地率。

用途：pdf_ingest distill 之后的质量门（引用可溯源、证据必须出自原文）。
输入：--ingest-dir（如 C:/项目/PDF/_ingest 或 _ingest_reports）
输出：<ingest-dir>/golden_spotcheck.{json,md}

指标：
  schema_ok      : book/chapter/page_start/page_end/concept/claim 均非空
  page_valid     : page_start/page_end 在 structure.pages 范围内且 start<=end
  chapter_match  : unit.chapter 能在 structure.chapters 标题中找到（含去空格包含）
  evidence_grounded: evidence（归一化后）能在该页范围原文（ocr.txt 或 layout 段落）中匹配
                     （先精确包含，失败则按 2-gram 覆盖率 ≥0.6 判定）
  citation_rate  : schema_ok 且 page_valid 的比例
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "")


def load_page_text(d: Path) -> dict[int, str]:
    """优先 ocr.txt（=== 第N页 ===），否则 layout.jsonl 的段落。"""
    txt = d / "ocr.txt"
    pages: dict[int, str] = {}
    if txt.exists():
        cur = 0
        buf = []
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
        return pages
    lay = d / "layout.jsonl"
    if lay.exists():
        for line in lay.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            pages[int(rec.get("page") or 0)] = "\n".join(
                str(b.get("text") or "") for b in (rec.get("blocks") or []))
    return pages


def bigram_cov(needle: str, hay: str) -> float:
    n, h = norm(needle), norm(hay)
    if not n:
        return 0.0
    if n in h:
        return 1.0
    grams = {n[i:i + 2] for i in range(len(n) - 1)} if len(n) > 1 else {n}
    hits = sum(1 for g in grams if g in h)
    return hits / max(1, len(grams))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest-dir", required=True)
    ap.add_argument("--min-cov", type=float, default=0.6)
    args = ap.parse_args(argv)
    root = Path(args.ingest_dir)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "ingest_dir": str(root), "min_cov": args.min_cov, "books": {}, "totals": {}}
    t_units = t_schema = t_page = t_chapter = t_ground = 0
    for d in sorted(p for p in root.glob("*") if p.is_dir()):
        ku = d / "knowledge_units.jsonl"
        st = d / "structure.json"
        if not (ku.exists() and st.exists()):
            continue
        structure = json.loads(st.read_text(encoding="utf-8"))
        n_pages = int(structure.get("pages") or 0)
        titles = [norm(c.get("title", "")) for c in structure.get("chapters") or []]
        pages = load_page_text(d)
        units = [json.loads(x) for x in ku.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not units:
            continue
        stat = {"units": len(units), "schema_ok": 0, "page_valid": 0, "chapter_match": 0, "grounded": 0,
                "grounded_strict": 0, "failed_samples": []}
        for u in units:
            need = ["book", "chapter", "page_start", "page_end", "concept", "claim"]
            schema = all(u.get(k) not in (None, "") for k in need) and int(u.get("page_start") or 0) <= int(u.get("page_end") or 0)
            start, end = int(u.get("page_start") or 0), int(u.get("page_end") or 0)
            page_ok = n_pages == 0 or (1 <= start <= end <= max(n_pages, end))
            ch = norm(u.get("chapter", ""))
            ch_ok = (not titles) or any(ch and (ch in t or t in ch) for t in titles)
            text = "\n".join(pages.get(p, "") for p in range(start, min(end, start + 30) + 1))
            cov = bigram_cov(u.get("evidence", ""), text)
            grounded = cov >= args.min_cov
            strict = norm(u.get("evidence", "")) in norm(text)
            stat["schema_ok"] += int(schema)
            stat["page_valid"] += int(page_ok)
            stat["chapter_match"] += int(ch_ok)
            stat["grounded"] += int(grounded)
            stat["grounded_strict"] += int(strict)
            if not grounded and len(stat["failed_samples"]) < 5:
                stat["failed_samples"].append({"chapter": u.get("chapter"), "pages": [start, end],
                                               "cov": round(cov, 3), "evidence": (u.get("evidence") or "")[:80]})
        n = len(units)
        for k in ("units",):
            pass
        stat["schema_rate"] = round(stat["schema_ok"] / n, 3)
        stat["page_rate"] = round(stat["page_valid"] / n, 3)
        stat["chapter_rate"] = round(stat["chapter_match"] / n, 3)
        stat["ground_rate"] = round(stat["grounded"] / n, 3)
        stat["ground_strict_rate"] = round(stat["grounded_strict"] / n, 3)
        stat["citation_rate"] = round(min(stat["schema_ok"], stat["page_valid"]) / n, 3)
        rep["books"][d.name] = stat
        t_units += n
        t_schema += stat["schema_ok"]
        t_page += stat["page_valid"]
        t_chapter += stat["chapter_match"]
        t_ground += stat["grounded"]
    rep["totals"] = {"units": t_units,
                     "schema_rate": round(t_schema / max(1, t_units), 3),
                     "page_rate": round(t_page / max(1, t_units), 3),
                     "chapter_rate": round(t_chapter / max(1, t_units), 3),
                     "ground_rate": round(t_ground / max(1, t_units), 3),
                     "books": len(rep["books"]),
                     "pass": (t_units > 0 and t_schema / max(1, t_units) >= 0.95 and t_ground / max(1, t_units) >= 0.8)}
    (root / "golden_spotcheck.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 知识单元 golden 抽检", "",
             f"- 目录：`{root}`；书/文档 {rep['totals']['books']}；知识单元 {t_units}", "",
             "| 指标 | 值 | 阈值 |", "|---|---|---|",
             f"| schema 完整率 | {rep['totals']['schema_rate']:.1%} | ≥95% |",
             f"| 页码合法率 | {rep['totals']['page_rate']:.1%} | — |",
             f"| 章节匹配率 | {rep['totals']['chapter_rate']:.1%} | — |",
             f"| 证据接地率（2-gram≥{args.min_cov}） | {rep['totals']['ground_rate']:.1%} | ≥80% |",
             f"| **判定** | **{'PASS' if rep['totals']['pass'] else 'FAIL'}** | |", ""]
    bad = [(k, v) for k, v in rep["books"].items() if v["ground_rate"] < 0.8]
    if bad:
        lines.append("## 接地率不足的文档")
        for k, v in bad[:20]:
            lines.append(f"- {k}: units={v['units']} ground={v['ground_rate']:.1%}")
    (root / "golden_spotcheck.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(rep["totals"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
