# -*- coding: utf-8 -*-
"""用 Playwright 真浏览器抓取单课验证可行性。

用法：python fetch_pw_test.py <url> <输出路径>
"""
import io
import re
import sys
from playwright.sync_api import sync_playwright


def main(url, out):
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="zh-CN",
            viewport={"width": 414, "height": 896},
        )
        pg = ctx.new_page()
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3000)
        h = pg.content()
        ok = 'id="js_content"' in h
        io.open(out, "w", encoding="utf-8", errors="ignore").write(h)
        t = re.search(r"<title>(.*?)</title>", h, re.S)
        print("js_content:", ok, "| size:", len(h),
              "| title:", (t.group(1)[:50] if t else "N/A"))
        b.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
