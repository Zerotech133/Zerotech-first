# -*- coding: utf-8 -*-
"""K266 M1 备样：下载研报/公告 PDF → PyMuPDF 渲染成图 → pypdf 同页文本存真值。

产出 data/k266_m1/
  samples/*.png   （zoom=2，≈144dpi 模拟截图）
  truth/*.txt     （pypdf 数字文本，逐页）
  manifest.json   （页号/来源/真值字符数）
"""

import io
import json
import os
import re

import fitz  # PyMuPDF
import requests
from pypdf import PdfReader

ROOT = r"C:\项目\老田的AI助手\quant_backtest\data\k266_m1"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
}  # dfcfw 直链需 Referer，否则返回 HTML 报错页

# 样本计划：巨潮公告（dfcfw 被 EO_Bot JS 挑战拦截，M1 发现，研报源后补）
# (doc_key, 标题关键词, 渲染页号 0-based)
PLANS = [
    ("ann_irm", "投资者关系活动记录表", [0, 1]),  # 问答文本
    ("ann_board", "董事会决议", [0, 1]),  # 纯文本决议
    ("ann_summary", "摘要", [0, 1, 2]),  # 股权激励摘要（含考核/价格表格页）
    ("ann_mna", "收购报告书摘要", [0, 1]),  # 收购摘要（股权结构/财务表格页）
]

CNINFO_Q = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_PDF = "http://static.cninfo.com.cn/"


def pick_pdfs():
    """巨潮查询近期公告，按标题关键词各取一篇。"""
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "http://www.cninfo.com.cn/",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    q = {
        "pageNum": 1,
        "pageSize": 30,
        "column": "szse",
        "tabName": "fulltext",
        "plate": "",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "trade": "",
        "category": "",
        "seDate": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    out = []
    for key, kw, pages in PLANS:
        q["searchkey"] = kw
        q["pageNum"] = 1
        r = s.post(CNINFO_Q, data=q, timeout=30)
        anns = r.json().get("announcements") or []
        hit = next(
            (
                a
                for a in anns
                if kw in re.sub(r"</?em>", "", a.get("announcementTitle") or "")
                and (a.get("adjunctUrl") or "").lower().endswith(".pdf")
            ),
            None,
        )
        if hit:
            out.append(
                (
                    key,
                    CNINFO_PDF + hit["adjunctUrl"],
                    pages,
                    hit["announcementTitle"][:40],
                )
            )
        else:
            print(f"  [warn] no hit for {kw}")
    return out


def main():
    for d in ("samples", "truth"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    manifest = []
    for key, url, pages, title in pick_pdfs():
        print(f"[doc] {key}: {title}")
        r = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://www.cninfo.com.cn/",
            },
            timeout=60,
        )
        r.raise_for_status()
        pdf = r.content
        doc = fitz.open(stream=pdf, filetype="pdf")
        reader = PdfReader(io.BytesIO(pdf))
        for pno in pages:
            if pno >= len(doc):
                continue
            png = os.path.join(ROOT, "samples", f"{key}_p{pno + 1}.png")
            pix = doc[pno].get_pixmap(matrix=fitz.Matrix(2, 2))  # zoom2 ≈144dpi
            pix.save(png)
            txt = (reader.pages[pno].extract_text() or "").strip()
            tf = os.path.join(ROOT, "truth", f"{key}_p{pno + 1}.txt")
            with open(tf, "w", encoding="utf-8") as f:
                f.write(txt)
            manifest.append(
                {
                    "img": os.path.basename(png),
                    "key": key,
                    "page": pno + 1,
                    "truth_chars": len(txt),
                    "url": url,
                }
            )
            print(f"  {key}_p{pno + 1}: {pix.width}x{pix.height}px truth={len(txt)}c")
        doc.close()
    with open(os.path.join(ROOT, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"[done] {len(manifest)} samples -> {ROOT}")


if __name__ == "__main__":
    main()
