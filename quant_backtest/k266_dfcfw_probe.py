# -*- coding: utf-8 -*-
"""dfcfw EO_Bot 反爬实验：报告列表 JSON → PDF 直链 → 请求头组合矩阵"""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# --- Step 1: reportapi JSON 列表（东财研报前端接口，qType=1 个股） ---
api = ("https://reportapi.eastmoney.com/report/list"
       "?industryCode=*&pageSize=5&industry=*&rating=*&ratingChange=*"
       "&beginTime=2026-09-25&endTime=2026-10-07&pageNo=1&fields=&qType=1")
r = requests.get(api, headers={"User-Agent": UA}, timeout=20)
data = r.json()
codes = [(d["infoCode"], d["title"][:20]) for d in data.get("data", [])][:5]
print(f"[1] reportapi JSON: rc_ok, {len(codes)} 篇")
for c, t in codes:
    print(f"    {c}  {t}")

if not codes:
    raise SystemExit("no codes")
info = codes[0][0]
url = f"https://pdf.dfcfw.com/pdf/H3_{info}_1.pdf"

# --- Step 2: 头组合矩阵 ---
full_headers = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/report/",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-site",
    "Upgrade-Insecure-Requests": "1",
}
combos = [
    ("裸requests(默认UA)", {}),
    ("仅浏览器UA", {"User-Agent": UA}),
    ("UA+Referer", {"User-Agent": UA, "Referer": "https://data.eastmoney.com/report/"}),
    ("全浏览器头组", full_headers),
]
print(f"[2] PDF 矩阵: {url}")
for name, hdr in combos:
    try:
        rr = requests.get(url, headers=hdr, timeout=25, allow_redirects=True)
        head = rr.content[:8]
        ok = rr.content[:4] == b"%PDF"
        print(f"    {name:<14} -> {rr.status_code} {len(rr.content):>8}B head={head!r} PDF={ok}")
        if ok:
            # 存一份验证可读性
            import fitz
            doc = fitz.open(stream=rr.content, filetype="pdf")
            print(f"    *** PASS: PyMuPDF 打开成功 {doc.page_count} 页, 首页文本 {len(doc[0].get_text())} 字符")
            break
    except Exception as e:
        print(f"    {name:<14} -> EXC {type(e).__name__}: {str(e)[:80]}")
