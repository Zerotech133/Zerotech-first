# -*- coding: utf-8 -*-
"""K266 M3 装料：reportapi 个股研报列表 → curl_cffi 下载 PDF → inbox/pending 投喂。
依赖：反爬解法 curl_cffi impersonate=chrome131（2026-10-07 实证）。
用法: python k266_seed_reports.py [--count 100]"""

import os
import sys
import time
from curl_cffi import requests as creq

PENDING = r"C:\项目\老田的AI助手\inbox\docs\pending"
UA_TZ = {
    "headers": {"Referer": "https://data.eastmoney.com/report/"},
    "impersonate": "chrome131",
    "timeout": 30,
}


def fetch_list(page, size=50):
    api = (
        "https://reportapi.eastmoney.com/report/list"
        "?industryCode=*&pageSize=%d&industry=*&rating=*&ratingChange=*"
        "&beginTime=2026-09-20&endTime=2026-10-07&pageNo=%d&fields=&qType=1"
        % (size, page)
    )
    r = creq.get(api, timeout=20)
    r.raise_for_status()
    return r.json().get("data", [])


def main():
    want = (
        int(sys.argv[sys.argv.index("--count") + 1]) if "--count" in sys.argv else 100
    )
    got = skip = fail = 0
    page = 1
    while got < want and page <= 6:
        rows = fetch_list(page)
        if not rows:
            break
        for d in rows:
            if got >= want:
                break
            code = d.get("infoCode", "")
            if not code:
                continue
            dest = os.path.join(PENDING, "rp_%s.pdf" % code)
            if os.path.exists(dest):
                skip += 1
                continue
            try:
                pr = creq.get("https://pdf.dfcfw.com/pdf/H3_%s_1.pdf" % code, **UA_TZ)
                if pr.content[:4] != b"%PDF":
                    raise ValueError("not pdf %dB" % len(pr.content))
                with open(dest, "wb") as f:
                    f.write(pr.content)
                got += 1
            except Exception as e:
                fail += 1
                print(f"  fail {code}: {str(e)[:60]}", flush=True)
            time.sleep(0.8)  # 温和节流
        print(f"page {page}: got={got} skip={skip} fail={fail}", flush=True)
        page += 1
    print(f"done got={got} skip={skip} fail={fail} -> {PENDING}")


if __name__ == "__main__":
    main()
