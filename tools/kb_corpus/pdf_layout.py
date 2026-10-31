# -*- coding: utf-8 -*-
"""MinerU-lite · 布局感知 PDF 解析（PyMuPDF，无需 torch/新依赖）。

目标：对**有文本层**的 PDF（研报/白皮书/公告）提取：
  - 阅读顺序段落（text blocks）
  - 标题层级（按字号+加粗启发式）
  - 表格（page.find_tables() → markdown 表格）
  - 页码/图表题注（caption 启发式）
输出：<out>/<stem>.layout.jsonl（逐页 blocks/tables）+ .layout.md（markdown）
与 OCR 通道对比：chars/页、表格数、标题数；给 pdf_ingest 未来升级提供依据。

用法：
  python tools/kb_corpus/pdf_layout.py --pdf "C:/项目/老田的AI助手/inbox/docs/done/rp_AP202609301830015406.pdf"
  python tools/kb_corpus/pdf_layout.py --pdf-dir <dir> --limit 5
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF 2.x 新入口
except ImportError:  # noqa: BLE001
    import fitz

REPO = Path(r"C:\项目\老田的AI助手")
DEFAULT_OUT = REPO / "work" / "layout_out"


def page_to_blocks(page: fitz.Page) -> list[dict]:
    d = page.get_text("dict")
    blocks = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        lines = []
        max_size = 0.0
        bold = False
        for ln in b.get("lines", []):
            text = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            if not text.strip():
                continue
            for sp in ln.get("spans", []):
                max_size = max(max_size, float(sp.get("size", 0)))
                if "bold" in str(sp.get("font", "")).lower() or (sp.get("flags", 0) & 16):
                    bold = True
            lines.append(text.strip())
        if not lines:
            continue
        text = " ".join(lines).strip()
        if len(text) < 2:
            continue
        # 标题启发式：字号 ≥ 正文均字号+2 或（短行且加粗）
        blocks.append({"text": text, "size": round(max_size, 1), "bold": bold,
                       "y": round(float(b.get("bbox", [0, 0, 0, 0])[1]), 1)})
    return blocks


def page_tables(page: fitz.Page) -> list[list[list[str]]]:
    out = []
    try:
        tabs = page.find_tables()
        for t in tabs:
            data = t.extract()
            if data and len(data) >= 2 and len(data[0]) >= 2:
                out.append([[("" if c is None else str(c).replace("\n", " ").strip()) for c in row] for row in data])
    except Exception:  # noqa: BLE001
        pass
    return out


def classify(blocks: list[dict]) -> list[dict]:
    if not blocks:
        return blocks
    sizes = sorted(b["size"] for b in blocks)
    body = sizes[len(sizes) // 2] if sizes else 10.0
    for b in blocks:
        if b["size"] >= body + 3.5:
            b["kind"] = "h1"
        elif b["size"] >= body + 1.8:
            b["kind"] = "h2"
        elif b["bold"] and len(b["text"]) <= 30:
            b["kind"] = "h3"
        else:
            b["kind"] = "p"
    return blocks


def md_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    head = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * ncol) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join([head, sep, body])


def parse_pdf(pdf: Path, out: Path) -> dict:
    t0 = time.time()
    doc = fitz.open(str(pdf))
    jl = out / f"{pdf.stem}.layout.jsonl"
    md_lines = [f"# {pdf.stem}（MinerU-lite 布局解析）", ""]
    stats = {"pages": len(doc), "text_pages": 0, "chars": 0, "tables": 0, "headings": 0,
             "h1": 0, "h2": 0, "h3": 0}
    with jl.open("w", encoding="utf-8") as f:
        for pno in range(1, len(doc) + 1):
            page = doc[pno - 1]
            blocks = classify(page_to_blocks(page))
            tables = page_tables(page)
            chars = sum(len(b["text"]) for b in blocks)
            if chars >= 30:
                stats["text_pages"] += 1
            stats["chars"] += chars
            stats["tables"] += len(tables)
            for b in blocks:
                if b.get("kind") in ("h1", "h2", "h3"):
                    stats["headings"] += 1
                    stats[b["kind"]] += 1
            f.write(json.dumps({"page": pno, "blocks": blocks, "tables": tables}, ensure_ascii=False) + "\n")
            # markdown
            md_lines.append(f"\n## 第 {pno} 页\n")
            for b in blocks:
                k = b.get("kind")
                if k == "h1":
                    md_lines.append(f"### {b['text']}\n")
                elif k == "h2":
                    md_lines.append(f"#### {b['text']}\n")
                elif k == "h3":
                    md_lines.append(f"**{b['text']}**\n")
                else:
                    md_lines.append(b["text"] + "\n")
            for t in tables:
                md_lines.append("\n" + md_table(t) + "\n")
    (out / f"{pdf.stem}.layout.md").write_text("\n".join(md_lines), encoding="utf-8")
    stats["chars_per_page"] = round(stats["chars"] / max(1, stats["pages"]), 1)
    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--pdf-dir", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pdfs = []
    if args.pdf:
        pdfs = [Path(args.pdf)]
    elif args.pdf_dir:
        pdfs = sorted(Path(args.pdf_dir).rglob("*.pdf"))
        if args.limit:
            pdfs = pdfs[: args.limit]
    if not pdfs:
        print("no pdf", flush=True)
        return 1
    report = {}
    for pdf in pdfs:
        st = parse_pdf(pdf, out)
        report[pdf.name] = st
        print(json.dumps({"file": pdf.name, **st}, ensure_ascii=False), flush=True)
    (out / "_layout_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
