# -*- coding: utf-8 -*-
"""OCR 扫描版 PDF（C:/项目/PDF）：PyMuPDF 渲染 + RapidOCR(ONNX)。

产出：C:/项目/PDF/_ocr/<book>.jsonl（逐页 text）+ <book>.txt（全文）
可续跑：已有 jsonl 按 page 跳过。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from rapidocr_onnxruntime import RapidOCR

PDF_DIR = Path(r"C:/项目/PDF")
OUT = PDF_DIR / "_ocr"
OUT.mkdir(parents=True, exist_ok=True)
DPI = 200


def main() -> int:
    engine = RapidOCR()
    for pdf in sorted(PDF_DIR.glob("*.pdf")):
        book = pdf.stem
        jl = OUT / f"{book}.jsonl"
        done = {}
        if jl.exists():
            for line in jl.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    done[d["page"]] = d["text"]
        doc = fitz.open(str(pdf))
        n = len(doc)
        print(f"== {book}: {n} pages, done={len(done)}", flush=True)
        t0 = time.time()
        with jl.open("a", encoding="utf-8") as f:
            for pno in range(1, n + 1):
                if pno in done:
                    continue
                page = doc[pno - 1]
                pix = page.get_pixmap(dpi=DPI)
                img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
                if img.shape[2] == 4:
                    img = img[:, :, :3]
                try:
                    res, _ = engine(img)
                except Exception as e:  # noqa: BLE001
                    print(f"  page {pno} OCR ERR {e}", flush=True)
                    res = None
                text = "\n".join(x[1] for x in res) if res else ""
                f.write(json.dumps({"page": pno, "text": text}, ensure_ascii=False) + "\n")
                f.flush()
                if pno % 20 == 0:
                    print(f"  {book} {pno}/{n} {time.time()-t0:.0f}s", flush=True)
        # 合并全文
        pages = []
        if jl.exists():
            for line in jl.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    pages.append(json.loads(line))
        pages.sort(key=lambda x: x["page"])
        (OUT / f"{book}.txt").write_text("\n\n".join(f"=== 第{p['page']}页 ===\n{p['text']}" for p in pages), encoding="utf-8")
        print(f"== {book} DONE text_chars={sum(len(p['text']) for p in pages)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
