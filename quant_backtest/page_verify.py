# -*- coding: utf-8 -*-
"""page_verify.py —— 门户页面关键词验证工具

背景（2026-10-02 K33 判例）：PS5 内联 `python -c "..."` 双引号转义会把
代码里的 `<` 解析为重定向操作符；here-string 管道喂 stdin 又踩 PS5
$OutputEncoding 默认 ASCII 的坑（脚本内中文变 `?`）。固化为本脚本
后两条路都不走，直接文件执行，编码/转义零问题。

用法：
    python page_verify.py /bt-9dim "K33 已重定位" "<th>分段</th>"
    python page_verify.py /bt-9dim --count ">中段<"   # 顺带统计出现次数
    python page_verify.py /kg                          # 只看 200/长度

参数：
    url          页面路径（默认 /bt-9dim，自动补 http://127.0.0.1:8091）
    keywords...  依次检查是否出现在页面 HTML 中
    --host       覆盖默认 127.0.0.1:8091
    --count      额外统计这些子串的出现次数（可与关键词检查同用）
"""
import argparse
import sys
import urllib.request


def main():
    ap = argparse.ArgumentParser(description="门户页面关键词验证")
    ap.add_argument("url", nargs="?", default="/bt-9dim",
                    help="页面路径，如 /bt-9dim（默认）")
    ap.add_argument("keywords", nargs="*", help="需命中的关键词列表")
    ap.add_argument("--host", default="127.0.0.1:8091")
    ap.add_argument("--count", nargs="*", default=[],
                    help="额外统计出现次数的子串")
    args = ap.parse_args()

    full = args.url if args.url.startswith("http") \
        else "http://%s%s" % (args.host, args.url)
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({}))          # 显式绕过系统代理
    try:
        h = op.open(full, timeout=30).read().decode("utf-8")
    except Exception as ex:
        print("FAIL %s: %s" % (full, ex))
        sys.exit(2)
    print("HTTP 200  %s  LEN=%d" % (full, len(h)))

    miss = 0
    for kw in args.keywords:
        ok = kw in h
        miss += 0 if ok else 1
        print("%s %s" % ("OK  " if ok else "MISS", kw))
    for kw in args.count:
        print("COUNT %-12s x %d" % (kw, h.count(kw)))
    sys.exit(1 if miss else 0)


if __name__ == "__main__":
    main()
