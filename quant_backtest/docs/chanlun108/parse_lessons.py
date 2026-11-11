# -*- coding: utf-8 -*-
"""解析 raw/*.html → md/NNN_课名.md（纯文本正文）+ INDEX.md。

微信正文在 id=js_content；段落结构用块级标签近似还原。
"""
import glob
import io
import os
import re
import html as H

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE, "raw")
MD_DIR = os.path.join(BASE, "md")


def extract_text(seg):
    seg = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", seg, flags=re.S)
    out = []
    for m in re.finditer(
            r"<(h1|h2|h3|h4|p|section|li|blockquote|strong|span|td)[^>]*>(.*?)</\1>",
            seg, re.S):
        txt = re.sub(r"<br\s*/?>", "\n", m.group(2))
        txt = re.sub(r"<[^>]+>", "", txt)
        txt = H.unescape(txt)
        txt = re.sub(r"[ \t\u00a0\u200b]+", "", txt)
        txt = txt.strip()
        if txt and txt not in ("", "&nbsp;"):
            out.append(txt)
    # 相邻去重（span/strong 嵌套会重复出现）
    dedup = []
    for line in out:
        if not dedup or dedup[-1] != line:
            dedup.append(line)
    return "\n".join(dedup)


def parse_one(fp):
    h = io.open(fp, encoding="utf-8", errors="ignore").read()
    t = re.search(r'<h1[^>]*id="activity-name"[^>]*>(.*?)</h1>', h, re.S)
    title = ""
    if t:
        title = re.sub(r"<[^>]+>", "", t.group(1)).strip()
    author = re.search(r'<meta name="author" content="([^"]*)"', h)
    a = re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', h, re.S)
    acct = re.sub(r"<[^>]+>", "", a.group(1)).strip() if a else ""
    i = h.find('id="js_content"')
    if i < 0:
        return title, acct, None
    seg = h[i:]
    # js_content 结束：找平衡太麻烦，微信正文后面紧跟 id="js_tags" 或 script；
    # 用内容长度截断到下一个主结构标记
    for stop in ('id="js_tags"', 'id="js_pc_qr_code"', 'id="content_bottom_area"',
                 '<script'):
        j = seg.find(stop, 100)
        if j > 100:
            seg = seg[:j]
            break
    return title, acct, extract_text(seg)


def main():
    os.makedirs(MD_DIR, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.html")))
    idx, empty = [], []
    for fp in files:
        base = os.path.basename(fp)
        title, acct, text = parse_one(fp)
        if not text or len(text) < 300:
            empty.append((base, len(text) if text else 0))
            continue
        md = "# %s\n\n> 公众号：%s\n\n%s\n" % (title or base, acct, text)
        out_fp = os.path.join(MD_DIR, base.replace(".html", ".md"))
        io.open(out_fp, "w", encoding="utf-8", newline="\n").write(md)
        no = base[:3]
        idx.append((no, title or base, len(text)))
    idx.sort()
    with io.open(os.path.join(BASE, "INDEX.md"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("# 缠论 108 课本地语料索引\n\n| 课 | 标题 | 字数 | 文件 |\n|---|---|---|---|\n")
        for no, title, n in idx:
            f.write("| %s | %s | %d | md/%s |\n" % (no, title, n, no))
    print("解析 %d 篇，空/过短 %d 篇" % (len(idx), len(empty)))
    for b, n in empty:
        print("  [空] %s len=%s" % (b, n))
    total = sum(n for _, _, n in idx)
    print("总字数约 %d" % total)


if __name__ == "__main__":
    main()
