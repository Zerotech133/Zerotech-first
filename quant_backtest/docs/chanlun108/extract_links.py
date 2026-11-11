# -*- coding: utf-8 -*-
"""从灵易录 108 课目录页提取课程链接 → links.json。

用法：python extract_links.py <目录页html路径>
铁律：长跑输出落文件；锚点断言 count 校验。
"""
import io
import json
import os
import re
import sys
import html as H

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "links.json")


def main(html_path):
    h = io.open(html_path, encoding="utf-8", errors="ignore").read()
    i = h.find('id="js_content"')
    assert i > 0, "js_content 未找到"
    seg = h[i:i + 3200000]

    links = []
    for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', seg, re.S):
        url = H.unescape(m.group(1)).strip()
        txt = re.sub(r"<br\s*/?>", "\n", m.group(2))
        txt = re.sub(r"<[^>]+>", "", txt)
        txt = H.unescape(txt)
        txt = re.sub(r"\s+", "", txt)
        if not txt or "mp.weixin.qq.com" not in url:
            continue
        links.append({"title": txt, "url": url})

    # 去重（保序）
    seen, uniq = set(), []
    for it in links:
        key = it["url"]
        if key not in seen:
            seen.add(key)
            uniq.append(it)

    # 标记课号（01~108；非编号课归 000 前奏）
    for it in uniq:
        m = re.search(r"教你炒股票\s*(?:系列)?(\d+)", it["title"])
        it["no"] = int(m.group(1)) if m else 0

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=1)

    n_lessons = len([x for x in uniq if x["no"] >= 1])
    print("总链接 %d，去重 %d，编号课 %d" % (len(links), len(uniq), n_lessons))
    miss = [n for n in range(1, 109) if n not in {x["no"] for x in uniq}]
    if miss:
        print("缺失课号:", miss)
    else:
        print("1~108 全部在位")


if __name__ == "__main__":
    main(sys.argv[1])
