# -*- coding: utf-8 -*-
"""淘股吧只读探针：区分「真无新帖」与「解析失效伪成功」。

只读一页，不写盘、不改 state、不入库。用法：
    python news_collector/collect_tgb_probe.py 000858 600519 000001
"""
import sys

import requests

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from taoguba_collector import fetch_stock_page, parse_blocks  # noqa: E402


def main(codes):
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    for code in codes:
        html, st = fetch_stock_page(sess, code, 1)
        if html is None:
            print("%s HTTP=%s 请求失败" % (code, st))
            continue
        rows = parse_blocks(html, code)
        posts = [r for r in rows if r.get("kind") == "post"]
        latest = ""
        for r in rows:
            ts = r.get("ts") or ""
            if ts > latest:
                latest = ts
        print("%s HTTP=%s html=%dB 解析=%d 条（主帖 %d）最新=%s"
              % (code, st, len(html), len(rows), len(posts), latest or "—"))


if __name__ == "__main__":
    main(sys.argv[1:] or ["000858"])
