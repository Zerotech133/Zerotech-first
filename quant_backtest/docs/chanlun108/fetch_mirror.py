# -*- coding: utf-8 -*-
"""从 GitHub 镜像 chzhshch-108-plus 批量下载 108 课 md → mirror/。

输入：gh_108_list.json（API contents 清单）。
输出：mirror/NNN_课名.md（课号取文件名尾 -NNN，标题取文内首行）。
"""
import glob
import io
import json
import os
import re
import subprocess
import time

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "mirror")


def lesson_no(name):
    m = re.search(r"-(\d{3})\.md$", name)
    return int(m.group(1)) if m else 0


def title_of(text):
    m = re.search(r"^#\s+(.+)$", text, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"【教你炒股票[^\n]{0,60}", text)
    return m.group(0)[:50] if m else ""


def main():
    os.makedirs(OUT, exist_ok=True)
    items = json.load(io.open(os.path.join(BASE, "gh_108_list.json"),
                              encoding="utf-8"))
    ok, fail = 0, 0
    for it in items:
        if not it["name"].endswith(".md"):
            continue
        no = lesson_no(it["name"])
        fp = os.path.join(OUT, "%03d.md" % no)
        if os.path.exists(fp) and os.path.getsize(fp) > 2000:
            ok += 1
            continue
        url = it["download_url"]
        rc = subprocess.call(["curl", "-sL", "--max-time", "30", "-o", fp, url])
        size = os.path.getsize(fp) if os.path.exists(fp) else 0
        if rc != 0 or size < 1000:
            print("[FAIL] %03d %s rc=%s size=%d" % (no, it["name"], rc, size),
                  flush=True)
            fail += 1
        else:
            ok += 1
        time.sleep(0.3)
    print("== ok=%d fail=%d / %d ==" % (ok, fail, len(items)), flush=True)
    # 汇总标题
    for fp in sorted(glob.glob(os.path.join(OUT, "*.md"))):
        t = io.open(fp, encoding="utf-8", errors="ignore").read()
        print(os.path.basename(fp), title_of(t)[:48], len(t), flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
