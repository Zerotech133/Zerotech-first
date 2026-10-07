# -*- coding: utf-8 -*-
"""K266 M2 统一摄取守护：inbox 三入口 → hash 去重 → OCR/pypdf 文本化 → doc_texts。

入口（全部汇入 INBOX/pending）：
  - 投递目录：手动丢文件
  - 门户上传：POST /kg/doc/upload（portal 写入 pending）
  - 剪贴板：k266_clip_watch.py 落图到 pending
状态机：pending → (done|reject)/，registry.jsonl 记 doc_id/sha256/status。
文本化策略：PDF 先 pypdf 数字文本，空则 PyMuPDF 渲染+OCR；图片直接 OCR。
用法：python k266_doc_ingest.py --once | --loop [间隔秒]  [--tables]

环境（沙箱内运行必备，M1 解法）：
  PADDLE_PDX_CACHE_HOME=C:\Temp\paddlex  PYTHONPYCACHEPREFIX=C:\Temp\pycache
"""
import hashlib
import json
import os
import re
import shutil
import sys
import time

INBOX = r"e:\老田的AI助手\inbox\docs"
REG = r"e:\老田的AI助手\quant_backtest\data\k266_docs"
TEXTS = os.path.join(REG, "doc_texts.jsonl")
REGFILE = os.path.join(REG, "registry.jsonl")
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

_ocr = None


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def get_ocr():
    """懒加载 PaddleOCR（M1 验收配置）。"""
    global _ocr
    if _ocr is None:
        from paddleocr import PaddleOCR
        _ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="ch",
            enable_mkldnn=False,  # paddle3.3 CPU oneDNN bug
        )
    return _ocr


def ocr_image(path):
    r = get_ocr().predict(path)
    res = r[0]
    texts = (res.get("rec_texts") if hasattr(res, "get")
             else getattr(res, "rec_texts", None)) or []
    return "\n".join(str(t) for t in texts)


def pdf_text(path):
    """PDF：pypdf 数字文本优先，空则逐页渲染 OCR（上限 8 页）。"""
    from pypdf import PdfReader
    txt = "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    if len(re.sub(r"\s", "", txt)) >= 50:
        return txt, "pdf"
    import fitz
    doc = fitz.open(path)
    parts = []
    for pno in range(min(8, len(doc))):
        png = os.path.join(REG, "_page.png")
        doc[pno].get_pixmap(matrix=fitz.Matrix(2, 2)).save(png)
        parts.append(ocr_image(png))
    if os.path.exists(os.path.join(REG, "_page.png")):
        os.remove(os.path.join(REG, "_page.png"))
    return "\n".join(parts), "pdf+ocr"


def load_registry():
    seen = {}
    if os.path.exists(REGFILE):
        for l in open(REGFILE, encoding="utf-8"):
            r = json.loads(l)
            seen[r["sha256"]] = r
    return seen


def process(path, src, tables=False):
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    reg = load_registry()
    if sha in reg:
        log(f"  DUP {os.path.basename(path)} == {reg[sha]['doc_id']}")
        shutil.move(path, os.path.join(INBOX, "reject",
                                       os.path.basename(path)))
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            text, how = pdf_text(path)
            # pypdf 偶发孤立代理对（数学字母区残半），落盘前清洗
            text = text.encode("utf-8", "ignore").decode("utf-8")
        elif ext in IMG_EXT:
            text, how = ocr_image(path), "ocr"
        else:
            log(f"  SKIP {os.path.basename(path)} (unsupport {ext})")
            shutil.move(path, os.path.join(INBOX, "reject",
                                           os.path.basename(path)))
            return None
    except Exception as e:
        log(f"  ERR {os.path.basename(path)}: {type(e).__name__}: {e}")
        shutil.move(path, os.path.join(INBOX, "reject",
                                       os.path.basename(path)))
        return None

    doc_id = "d" + sha[:12]
    rec = {"doc_id": doc_id, "sha256": sha, "name": os.path.basename(path),
           "kind": how, "src": src, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "status": "ok", "text_chars": len(text)}
    with open(TEXTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({**rec, "text": text}, ensure_ascii=False) + "\n")
    with open(REGFILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    shutil.move(path, os.path.join(INBOX, "done", os.path.basename(path)))
    log(f"  OK {doc_id} {os.path.basename(path)} {how} "
        f"{len(text)}c (src={src})")
    return rec


def scan_once(tables=False):
    pend = os.path.join(INBOX, "pending")
    files = sorted(os.path.join(pend, f) for f in os.listdir(pend)
                   if not f.startswith("."))
    if not files:
        return 0
    log(f"scan: {len(files)} pending")
    n = 0
    for p in files:
        src = "drop"
        if os.path.basename(p).startswith("clip_"):
            src = "clip"
        elif os.path.basename(p).startswith("up_"):
            src = "portal"
        if process(p, src, tables):
            n += 1
    return n


def main():
    args = sys.argv[1:]
    loop = "--loop" in args
    tables = "--tables" in args
    for d in ("pending", "done", "reject"):
        os.makedirs(os.path.join(INBOX, d), exist_ok=True)
    os.makedirs(REG, exist_ok=True)
    if not loop:
        scan_once(tables)
        return
    ivl = 10
    if len(args) > args.index("--loop") + 1 and args[args.index("--loop") + 1].isdigit():
        ivl = int(args[args.index("--loop") + 1])
    log(f"daemon loop={ivl}s  inbox={INBOX}")
    idle = 0
    while True:
        n = scan_once(tables)
        idle = idle + 1 if not n else 0
        if idle in (6, 60, 180):  # 1/10/30 分钟心跳
            log(f"idle x{idle}")
        time.sleep(ivl)


if __name__ == "__main__":
    main()
