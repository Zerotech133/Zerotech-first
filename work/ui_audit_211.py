# -*- coding: utf-8 -*-
"""全入口体检 v2（K-STOCK-211）：python 直连 8080 真实门户入口。
判读：HTTP 状态 + body 长度 + title + 是否空壳（正文特征）。
输出 work/ui_audit_211.json 供甄别。"""
import json
import re
import urllib.request

BASE = "http://127.0.0.1:8080"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# nav.js 全部内部路径
src = open("portal/static/nav.js", encoding="utf-8").read()
urls = {"/", "/portal", "/screen", "/paper", "/picks.html"}
for m in re.finditer(r'"/[a-z0-9_\-./]*"', src):
    u = m[0].strip('"')
    if not re.search(r"\.(js|css|json|png)$", u):
        urls.add(u)
# index.html 里的入口也纳入
idx = open("portal/index.html", encoding="utf-8").read()
for m in re.finditer(r'href="(/[a-z0-9_\-./]*)"', idx):
    u = m.group(1)
    if not re.search(r"\.(js|css|json|png)$", u):
        urls.add(u)

out = []
for u in sorted(urls):
    rec = {"url": u}
    try:
        r = op.open(BASE + u, timeout=12)
        body = r.read(60000).decode("utf-8", "replace")
        rec["status"] = r.status
        rec["bytes"] = len(body)
        t = re.search(r"<title>(.*?)</title>", body)
        rec["title"] = t.group(1)[:36] if t else "-"
        # 空壳特征：正文文本极少（剥掉 script/style 后）
        text = re.sub(r"<(script|style)[\s\S]*?</\1>", "", body)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        rec["textLen"] = len(text)
        rec["soon"] = ("研发中" in text) or ("敬请期待" in text) or ("coming" in text.lower())
    except Exception as e:
        rec["status"] = f"{type(e).__name__}: {str(e)[:60]}"
        rec["bytes"] = 0
        rec["title"] = "-"
        rec["textLen"] = 0
        rec["soon"] = False
    out.append(rec)
    print(f"{u:24s} {rec['status']} bytes={rec['bytes']:>6} text={rec['textLen']:>5} soon={rec['soon']} {rec['title']}")

json.dump(out, open("work/ui_audit_211.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved", len(out), "entries")
