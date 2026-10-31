# -*- coding: utf-8 -*-
"""PDF 知识入库 · 标准能力 CLI（2026-10-31，工单 WO-PDF-INGEST-20261031）。

子命令：
  scan     PDF 类型探测（文本层/扫描版/混合；页数/大小/样本字数）→ manifest.json
  ocr      扫描版 OCR（PyMuPDF 渲染 200dpi + RapidOCR ONNX），逐页落盘可续跑
  structure 目录/章节抽取（从 OCR 文本识别章节标题与页码）→ structure.json
  distill  知识单元抽取（本地 qwen3.5；concept/claim/evidence/章节/页码）→ knowledge_units.jsonl
  verify   质量标准验收（覆盖率/OCR 质量/结构/引用完整率/schema）
  index    生成摘要 md 入 knowledge_docs 并重建 RAG 索引
  all      scan → ocr → structure → distill → index → verify

产物目录（默认 C:/项目/PDF/_ingest/<book>/）：
  manifest.json / ocr.jsonl / ocr.txt / structure.json / knowledge_units.jsonl / verify.json

用法：
  python tools/kb_corpus/pdf_ingest.py all --pdf-dir C:/项目/PDF [--chapters 10]
  python tools/kb_corpus/pdf_ingest.py verify --pdf-dir C:/项目/PDF
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
DEFAULT_PDF_DIR = Path(r"C:/项目/PDF")
DEFAULT_OUT = DEFAULT_PDF_DIR / "_ingest"
OLLAMA = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3:latest"
CHAT_MODEL = "qwen3.5:9b"
SCHEMA_VERSION = 1
CHAPTER_RE = re.compile(r"^(第[一二三四五六七八九十百零〇\d]+[章节篇讲]|序言|前言|后记|附录|Chapter\s+\d+)")
WATERMARK_MARKERS = ("摩登爆米花", "微信", "公众号")


# ---------------------------------------------------------------- 基础
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def book_out(pdf: Path, out_root: Path) -> Path:
    d = out_root / pdf.stem
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- scan
def pdf_type(pdf: Path, sample_pages: int = 5) -> dict:
    from pypdf import PdfReader
    r = PdfReader(str(pdf), strict=False)
    n = len(r.pages)
    text = ""
    for i in range(min(sample_pages, n)):
        try:
            text += r.pages[i].extract_text() or ""
        except Exception:  # noqa: BLE001
            pass
    chars = len(text.strip())
    watermark = any(m in text for m in WATERMARK_MARKERS)
    kind = "text" if chars >= 300 * min(sample_pages, n) else ("scanned" if chars < 100 * min(sample_pages, n) else "mixed")
    return {"file": pdf.name, "path": str(pdf), "pages": n, "bytes": pdf.stat().st_size,
            "sample_chars": chars, "kind": kind, "watermark": watermark,
            "sha256": sha256_file(pdf)}


def cmd_scan(args) -> int:
    pdf_dir = Path(args.pdf_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        try:
            info = pdf_type(pdf)
        except Exception as e:  # noqa: BLE001
            info = {"file": pdf.name, "path": str(pdf), "error": f"{type(e).__name__}: {e}"}
        items.append(info)
        log(f"scan {pdf.name}: {info.get('kind')} pages={info.get('pages')}")
    manifest = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "pdf_dir": str(pdf_dir), "count": len(items), "items": items}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"scanned": sum(1 for i in items if i.get("kind") == "scanned"),
                      "text": sum(1 for i in items if i.get("kind") == "text"),
                      "mixed": sum(1 for i in items if i.get("kind") == "mixed")}, ensure_ascii=False), flush=True)
    return 0


# ---------------------------------------------------------------- ocr
def cmd_ocr(args) -> int:
    import fitz
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    pdf_dir = Path(args.pdf_dir)
    out = Path(args.out_dir)
    engine = None
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        info = pdf_type(pdf)
        if info["kind"] == "text" and not args.force:
            d = book_out(pdf, out)
            layout = d / "layout.jsonl"
            if not layout.exists():
                try:
                    import pdf_layout
                    pdf_layout.parse_pdf(pdf, d)
                    src_j, src_m = d / f"{pdf.stem}.layout.jsonl", d / f"{pdf.stem}.layout.md"
                    if src_j.exists() and not layout.exists():
                        src_j.rename(layout)
                    if src_m.exists() and not (d / "layout.md").exists():
                        src_m.rename(d / "layout.md")
                    log(f"ocr→layout {pdf.name}（文本层，布局/表格通道）")
                except Exception as e:  # noqa: BLE001
                    log(f"layout ERR {pdf.name}: {type(e).__name__} {e}")
            else:
                log(f"ocr skip {pdf.name}（已有 layout.jsonl）")
            continue
        d = book_out(pdf, out)
        jl = d / "ocr.jsonl"
        done = set()
        if jl.exists():
            for line in jl.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        done.add(json.loads(line)["page"])
                    except Exception:  # noqa: BLE001
                        pass
        if engine is None:
            engine = RapidOCR()
        doc = fitz.open(str(pdf))
        n = len(doc)
        log(f"ocr {pdf.name}: {n} 页，已完成 {len(done)}")
        t0 = time.time()
        with jl.open("a", encoding="utf-8") as f:
            for pno in range(1, n + 1):
                if pno in done:
                    continue
                pix = doc[pno - 1].get_pixmap(dpi=200)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                if img.shape[2] == 4:
                    img = img[:, :, :3]
                try:
                    res, _ = engine(img)
                except Exception as e:  # noqa: BLE001
                    log(f"  page {pno} OCR ERR {e}")
                    res = None
                text = "\n".join(x[1] for x in res) if res else ""
                f.write(json.dumps({"page": pno, "text": text}, ensure_ascii=False) + "\n")
                f.flush()
                if pno % 50 == 0:
                    log(f"  {pdf.name} {pno}/{n} {time.time()-t0:.0f}s")
        pages = []
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                pages.append(json.loads(line))
        pages.sort(key=lambda x: x["page"])
        (d / "ocr.txt").write_text("\n\n".join(f"=== 第{p['page']}页 ===\n{p['text']}" for p in pages), encoding="utf-8")
        log(f"ocr {pdf.name} DONE chars={sum(len(p['text']) for p in pages)}")
    return 0


# ---------------------------------------------------------------- structure
def clean_line(ln: str) -> bool:
    return bool(ln.strip()) and not any(m in ln for m in WATERMARK_MARKERS)


def cmd_structure(args) -> int:
    out = Path(args.out_dir)
    pdf_dir = Path(args.pdf_dir)
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        d = book_out(pdf, out)
        layout = d / "layout.jsonl"
        if layout.exists():
            chapters = []
            seen = set()
            max_page = 0
            for line in layout.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                page = int(rec.get("page") or 0)
                max_page = max(max_page, page)
                for b in rec.get("blocks") or []:
                    if b.get("kind") not in ("h1", "h2"):
                        continue
                    title = str(b.get("text") or "").strip()
                    if not title or len(title) > 60 or title in seen:
                        continue
                    seen.add(title)
                    chapters.append({"title": title, "page": page})
            structure = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                         "book": pdf.stem, "pages": max_page or None, "source": "layout",
                         "chapters": chapters}
            (d / "structure.json").write_text(json.dumps(structure, ensure_ascii=False, indent=1), encoding="utf-8")
            log(f"structure(layout) {pdf.name}: pages={max_page} chapters={len(chapters)}")
            continue
        txt_file = d / "ocr.txt"
        if not txt_file.exists():
            log(f"structure skip {pdf.name}（缺 ocr.txt/layout.jsonl）")
            continue
        txt = txt_file.read_text(encoding="utf-8")
        pages = []
        # 逐页解析
        cur = {}
        for line in txt.splitlines():
            m = re.match(r"^=== 第(\d+)页 ===$", line.strip())
            if m:
                if cur:
                    pages.append(cur)
                cur = {"page": int(m.group(1)), "lines": []}
            elif cur and clean_line(line):
                cur["lines"].append(line.strip())
        if cur:
            pages.append(cur)
        chapters = []
        seen = set()
        last_page = -1
        toc_re = re.compile(r"[.·…]{2,}\s*\d+\s*$")
        for pg in pages:
            for ln in pg["lines"]:
                if len(ln) > 40 or not CHAPTER_RE.match(ln):
                    continue
                if toc_re.search(ln):  # 目录行（带点线页码）
                    continue
                title = re.sub(r"[.·…\s]+$", "", ln).strip()
                if not title or title in seen:
                    continue
                if pg["page"] < last_page:  # 只允许页码前向单调，过滤目录回跳
                    continue
                seen.add(title)
                last_page = pg["page"]
                chapters.append({"title": title, "page": pg["page"]})
        structure = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                     "book": pdf.stem, "pages": len(pages), "chapters": chapters}
        (d / "structure.json").write_text(json.dumps(structure, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"structure {pdf.name}: pages={len(pages)} chapters={len(chapters)}")
    return 0


# ---------------------------------------------------------------- distill
def chat(prompt: str, num: int = 1600, timeout: int = 300) -> str:
    import urllib.request
    body = {"model": CHAT_MODEL, "stream": False, "think": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.1, "num_predict": num}}
    req = urllib.request.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return ((json.loads(r.read().decode()).get("message") or {}).get("content") or "").strip()


def cmd_distill(args) -> int:
    out = Path(args.out_dir)
    pdf_dir = Path(args.pdf_dir)
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        d = book_out(pdf, out)
        st = d / "structure.json"
        txt_file = d / "ocr.txt"
        layout_file = d / "layout.jsonl"
        if not st.exists():
            log(f"distill skip {pdf.name}")
            continue
        structure = json.loads(st.read_text(encoding="utf-8"))
        chapters = structure.get("chapters") or []
        if args.chapters:
            chapters = chapters[: args.chapters]
        page_text = {}
        if txt_file.exists():
            pages = []
            cur = {}
            for line in txt_file.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^=== 第(\d+)页 ===$", line.strip())
                if m:
                    if cur:
                        pages.append(cur)
                    cur = {"page": int(m.group(1)), "text": []}
                elif cur:
                    cur["text"].append(line)
            if cur:
                pages.append(cur)
            page_text = {p["page"]: "\n".join(p["text"]) for p in pages}
        elif layout_file.exists():
            for line in layout_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                texts = [str(b.get("text") or "") for b in (rec.get("blocks") or [])
                         if b.get("kind") in ("p", "h3")]
                page_text[int(rec.get("page") or 0)] = "\n".join(t for t in texts if t.strip())
        else:
            log(f"distill skip {pdf.name}（无 ocr.txt / layout.jsonl）")
            continue
        units = []
        for i, ch in enumerate(chapters):
            start = ch["page"]
            end = chapters[i + 1]["page"] - 1 if i + 1 < len(chapters) else max(page_text) if page_text else start
            body = "\n".join(page_text.get(p, "") for p in range(start, min(end, start + 12) + 1))[:9000]
            if len(body.strip()) < 100:
                continue
            prompt = ("从以下书籍章节文本提取知识单元。只输出 JSON 数组（最多 5 条），每条字段："
                      '{"concept":"概念/术语","claim":"可操作或可验证的论点","evidence":"原文依据(<=100字)"}。'
                      "不要编造；无明确知识则输出 []。\n\n"
                      f"章节：{ch['title']}\n文本：\n{body}")
            try:
                raw = chat(prompt)
                m = re.search(r"\[.*\]", raw, re.S)
                items = json.loads(m.group(0)) if m else []
            except Exception as e:  # noqa: BLE001
                log(f"  distill ERR {ch['title'][:20]} {type(e).__name__}")
                items = []
            for it in items[:5]:
                claim = str(it.get("claim", "")).strip()
                if not claim:
                    continue
                units.append({"schema_version": SCHEMA_VERSION, "book": pdf.stem, "chapter": ch["title"],
                              "page_start": start, "page_end": min(end, start + 12),
                              "concept": str(it.get("concept", ""))[:80], "claim": claim[:400],
                              "evidence": str(it.get("evidence", ""))[:300],
                              "source": "ocr+llm", "confidence": "medium",
                              "sha1": hashlib.sha1((pdf.stem + claim).encode()).hexdigest()[:16]})
            log(f"  distill {ch['title'][:24]} units={len(units)}")
        (d / "knowledge_units.jsonl").write_text(
            "\n".join(json.dumps(u, ensure_ascii=False) for u in units), encoding="utf-8")
        log(f"distill {pdf.name}: units={len(units)}")
    return 0


# ---------------------------------------------------------------- verify
def cmd_verify(args) -> int:
    out = Path(args.out_dir)
    pdf_dir = Path(args.pdf_dir)
    units_min = int(getattr(args, "units_min", 10))
    chapters_min = int(getattr(args, "chapters_min", 5))
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "thresholds": {"ocr_chars_per_page": 100, "page_coverage": 0.95,
                             "chapters_min": chapters_min, "units_min": units_min, "unit_citation_rate": 1.0},
              "books": {}, "ok": True, "failures": []}
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        d = book_out(pdf, out)
        item = {"book": pdf.stem}
        info = pdf_type(pdf)
        item["kind"] = info["kind"]
        item["pages"] = info["pages"]
        # OCR
        ocr = d / "ocr.jsonl"
        pages = []
        if ocr.exists():
            for line in ocr.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        pages.append(json.loads(line))
                    except Exception:  # noqa: BLE001
                        pass
        chars = sum(len(p.get("text", "")) for p in pages)
        item["ocr_pages"] = len(pages)
        item["ocr_chars"] = chars
        item["ocr_chars_per_page"] = round(chars / max(1, len(pages)), 1) if pages else 0
        item["page_coverage"] = round(len(pages) / max(1, info["pages"]), 3)
        # 乱码率（替换字符/控制字符）
        bad = sum(p.get("text", "").count("\ufffd") for p in pages)
        item["garbled_ratio"] = round(bad / max(1, chars), 4)
        # structure
        st = d / "structure.json"
        item["chapters"] = len(json.loads(st.read_text(encoding="utf-8")).get("chapters", [])) if st.exists() else 0
        # units
        ku = d / "knowledge_units.jsonl"
        units = [json.loads(x) for x in ku.read_text(encoding="utf-8").splitlines() if x.strip()] if ku.exists() else []
        item["units"] = len(units)
        cited = sum(1 for u in units if u.get("book") and u.get("chapter") and u.get("page_start"))
        item["unit_citation_rate"] = round(cited / max(1, len(units)), 3)
        # 判决
        checks = {
            "page_coverage": item["page_coverage"] >= 0.95,
            "ocr_quality": item["ocr_chars_per_page"] >= 100 and item["garbled_ratio"] < 0.02,
            "structure": item["chapters"] >= chapters_min,
            "units": item["units"] >= units_min and item["unit_citation_rate"] >= 1.0,
        }
        item["checks"] = checks
        # 仅有文本层的书允许无 OCR：按文本层折算
        if info["kind"] == "text" and not pages:
            lay = d / "layout.jsonl"
            if lay.exists():
                # 文本层 PDF 走 layout 通道：按 blocks 计字符与页数
                chars = 0
                max_page = 0
                for line in lay.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    max_page = max(max_page, int(rec.get("page") or 0))
                    chars += sum(len(str(b.get("text") or "")) for b in (rec.get("blocks") or []))
                if max_page:
                    item["ocr_chars"] = chars
                    item["ocr_chars_per_page"] = round(chars / max_page, 1)
                    item.update({"ocr_pages": max_page, "page_coverage": 1.0, "garbled_ratio": 0.0})
                    checks["page_coverage"] = True
                    checks["ocr_quality"] = item["ocr_chars_per_page"] >= 100
            else:
                text_pages = 0
                try:
                    from pypdf import PdfReader
                    r = PdfReader(str(pdf), strict=False)
                    text = "".join((p.extract_text() or "") for p in r.pages)
                    text_pages = info["pages"]
                    item["ocr_chars"] = len(text)
                    item["ocr_chars_per_page"] = round(len(text) / max(1, text_pages), 1)
                    item.update({"ocr_pages": text_pages, "page_coverage": 1.0, "garbled_ratio": 0.0})
                    checks["page_coverage"] = True
                    checks["ocr_quality"] = item["ocr_chars_per_page"] >= 100
                except Exception:  # noqa: BLE001
                    pass
        if not all(checks.values()):
            report["ok"] = False
            report["failures"].append({"book": pdf.stem, "checks": checks, **item})
        report["books"][pdf.stem] = item
    (out / "verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "books": {k: v["checks"] for k, v in report["books"].items()}},
                     ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


# ---------------------------------------------------------------- index
def cmd_index(args) -> int:
    out = Path(args.out_dir)
    pdf_dir = Path(args.pdf_dir)
    kb_docs = REPO / "knowledge_docs"
    kb_docs.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        d = book_out(pdf, out)
        st = d / "structure.json"
        if not st.exists():
            continue
        structure = json.loads(st.read_text(encoding="utf-8"))
        units = []
        ku = d / "knowledge_units.jsonl"
        if ku.exists():
            units = [json.loads(x) for x in ku.read_text(encoding="utf-8").splitlines() if x.strip()]
        layout_note = ""
        lay = d / "layout.jsonl"
        if lay.exists():
            tables = 0
            heads = 0
            for line in lay.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                tables += len(rec.get("tables") or [])
                heads += sum(1 for b in (rec.get("blocks") or []) if b.get("kind") in ("h1", "h2", "h3"))
            layout_note = f"布局解析：{tables} 张表格 / {heads} 个标题（layout 通道）。"
        lines = [f"# {pdf.stem}（PDF 知识入库）", "",
                 f"> 来源：`{pdf}`；页数 {structure.get('pages')}；产物 `{d}`；{layout_note}"
                 f"知识单元 {len(units)} 条（含章节/页码引用）。仅内部研究使用。", "",
                 "## 章节结构", ""]
        for ch in structure.get("chapters", [])[:40]:
            lines.append(f"- 第 {ch['page']} 页：{ch['title']}")
        lines += ["", "## 知识单元（节选）", ""]
        for u in units[:40]:
            lines.append(f"- **{u.get('concept') or u.get('chapter')}**（p{u.get('page_start')}-{u.get('page_end')}）："
                         f"{u.get('claim')}  \n  > {u.get('evidence','')}")
        md = kb_docs / f"PDF入库_{pdf.stem}.md"
        md.write_text("\n".join(lines), encoding="utf-8")
        log(f"index {pdf.name} → {md}")
    if args.rebuild_rag:
        cmd = [r"C:\Program Files\Python311\python.exe", "-X", "utf8", str(REPO / "AI" / "111111" / "rag_index_cli.py"),
               "rebuild", "--docs-dir", str(kb_docs), "--index-path", str(kb_docs / ".rag_index.json")]
        r = subprocess.run(cmd, cwd=str(REPO / "AI" / "111111"), capture_output=True, text=True, encoding="utf-8", errors="replace")
        log(f"rag rebuild rc={r.returncode} {(r.stdout or r.stderr).strip()[-200:]}")
    return 0


# ---------------------------------------------------------------- all
def cmd_all(args) -> int:
    for step, fn in (("scan", cmd_scan), ("ocr", cmd_ocr), ("structure", cmd_structure),
                     ("distill", cmd_distill), ("index", cmd_index)):
        log(f"=== {step} ===")
        rc = fn(args)
        if rc != 0:
            return rc
    log("=== verify ===")
    return cmd_verify(args)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="PDF 知识入库标准能力")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("scan", cmd_scan), ("ocr", cmd_ocr), ("structure", cmd_structure),
                     ("distill", cmd_distill), ("verify", cmd_verify), ("index", cmd_index), ("all", cmd_all)):
        p = sub.add_parser(name)
        p.add_argument("--pdf-dir", default=str(DEFAULT_PDF_DIR))
        p.add_argument("--out-dir", default=str(DEFAULT_OUT))
        p.add_argument("--force", action="store_true")
        p.add_argument("--chapters", type=int, default=0, help="distill 最多处理章节数（0=全部）")
        p.add_argument("--rebuild-rag", action="store_true", help="index 后重建 RAG 索引")
        if name == "verify":
            p.add_argument("--units-min", type=int, default=10, help="知识单元数阈值（短文档可放宽，需在报告注明）")
            p.add_argument("--chapters-min", type=int, default=5, help="章节数阈值")
        p.set_defaults(fn=fn)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
