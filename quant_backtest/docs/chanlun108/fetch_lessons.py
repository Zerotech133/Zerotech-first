# -*- coding: utf-8 -*-
"""逐课下载微信文章 → raw/NNN_title.html。

铁律：长跑输出落文件（本脚本自己就是被 fetch_all 重定向落文件的）；
限速 1.2s/请求防封；断点续抓（已存在且 >50KB 跳过）。
"""
import io
import json
import os
import re
import sys
import time
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE, "raw")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def safe_name(no, title):
    t = re.sub(r'[\\/:*?"<>|\s]+', "_", title)[:60]
    return "%03d_%s.html" % (no, t)


def main():
    links = json.load(io.open(os.path.join(BASE, "links.json"), encoding="utf-8"))
    ok, skip, fail = 0, 0, 0
    for idx, it in enumerate(links):
        fp = os.path.join(RAW_DIR, safe_name(it["no"], it["title"]))
        if os.path.exists(fp) and os.path.getsize(fp) > 50000:
            skip += 1
            continue
        rc = subprocess.call(
            ["curl", "-sL", "--max-time", "40", "-A", UA,
             "-o", fp, it["url"]],
            shell=False)
        size = os.path.getsize(fp) if os.path.exists(fp) else 0
        if rc != 0 or size < 20000:
            print("[FAIL] %03d %s rc=%s size=%s" % (it["no"], it["title"][:30], rc, size),
                  flush=True)
            fail += 1
        else:
            ok += 1
            print("[ok] %03d %s %dKB" % (it["no"], it["title"][:30], size // 1024),
                  flush=True)
        time.sleep(1.2)
    print("== 完成: ok=%d skip=%d fail=%d / %d ==" % (ok, skip, fail, len(links)),
          flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
