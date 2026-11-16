# -*- coding: utf-8 -*-
"""候风发布集锁校验（K-HF-PUB-20261116）。

存在理由
--------
PUBLISH_SET.json 里 publish_set 三页各记 md5 + size。那对字段是「锁」本身，
但截至 2026-11-16 没有任何程序读过它们 —— 只有 build_houfeng_index.py 与
build_houfeng_today.py 读 excluded 的说明文字。没人校验的锁只是一份记录：
页面被悄悄改掉，锁不会响。

本脚本只读，不写任何文件，不改页面、不改 PUBLISH_SET.json。

五类检查（任一失败即非零退出）
------------------------------
1 LOCK   发布集三页的 md5 / size 与登记值逐字节一致
         —— 不一致 = 发布集被改动过。这不是 bug，是需要重新批准并更新登记的事实。
2 COVER  目录内每个 .html 都必须出现在 publish_set 或 excluded 里
         —— 「未登记的新页面」是这个平台最容易发生的漏：加了页没人说它对外不对。
3 DUP    同一页不得同时出现在 publish_set 与 excluded
         —— 既对外又不对外，说明口径自相矛盾。
4 LINK   入口页 index.html 的内链必须都能落到真实文件
         —— 断链比 404 更坏：读者点进去看到「未挂载」会以为是没上线。
5 DATE   入口页上写的每个「数据算到 YYYY-MM-DD」都必须能在真源里找到同值
         —— 页面不得写出真源里不存在的日期（写死日期比没有日期更糟）。

退出码：0 全部通过；1 有 FAIL。

落库记录（勘误，append-only 补记，2026-11-16）
---------------------------------------------
本文件首次进入仓库时，实际是被并行会话的 40c0652b1「投研派活器」连带提交的，
那个提交的 message 里没有一个字提到发布集校验器 —— 我 git add 之后、git commit
之前跑了变异检验，中间那段窗口被撞上了。git log -- portal/check_houfeng_publish.py
会指向 40c0652b1，读日志的人会以为这是投研派活器的一部分。

不复写历史：40c0652b1 是另一席位的活、可能已被引用。本段只为让后来者知道
「这个文件为什么出现在那个提交里」，以及它真正要解决的问题写在上面「存在理由」里。
"""
import hashlib
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HF = os.path.join(HERE, "houfeng")
PSET = os.path.join(HF, "PUBLISH_SET.json")
INDEX = os.path.join(HF, "index.html")

# 页面数据日必须能在这些真源的 window.end 里找到同值
DATE_SOURCES = [
    "houfeng_arena_36m.json",
    "houfeng_holdgrid.json",
    "houfeng_dividend.json",
    "houfeng_playbook.json",
]

HTML_RE = re.compile(r"[A-Za-z0-9_.\-]+\.html")
DATE_ON_PAGE_RE = re.compile(r"数据算到\s*(\d{4}-\d{2}-\d{2})")


def md5_of(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    fails, lines = [], []

    def note(ok, code, msg, warn=False):
        tag = "[WARN] " if warn else ("[OK]   " if ok else "[FAIL] ")
        lines.append(tag + code + " " + msg)
        if not ok and not warn:
            fails.append(code + " " + msg)

    if not os.path.isfile(PSET):
        print("[FAIL] PUB  发布集登记缺失 -> %s" % PSET)
        return 1
    if not os.path.isdir(HF):
        print("[FAIL] PUB  页面目录缺失 -> %s" % HF)
        return 1

    meta = json.load(io.open(PSET, encoding="utf-8"))
    pub = meta.get("publish_set") or []
    exc = meta.get("excluded") or {}

    # ── 1 LOCK ────────────────────────────────────────────────
    if not pub:
        note(False, "LOCK", "publish_set 为空：发布集等于没有锁")
    for it in pub:
        page, want_md5, want_sz = it.get("page"), it.get("md5"), it.get("size")
        fp = os.path.join(HF, page or "")
        if not page or not os.path.isfile(fp):
            note(False, "LOCK", "%s 不在磁盘上（登记在发布集里却找不到文件）" % page)
            continue
        got_md5 = md5_of(fp)
        got_sz = os.path.getsize(fp)
        if got_md5 == want_md5 and got_sz == want_sz:
            note(True, "LOCK", "%s md5/size 与登记一致（%s / %d B）"
                 % (page, got_md5[:8], got_sz))
        else:
            note(False, "LOCK",
                 "%s 与登记不符：md5 %s -> %s，size %s -> %d"
                 % (page, (want_md5 or "?")[:8], got_md5[:8], want_sz, got_sz))

    # ── 2 COVER / 3 DUP ───────────────────────────────────────
    on_disk = sorted(f for f in os.listdir(HF) if f.endswith(".html"))
    pub_pages = [it.get("page") for it in pub if it.get("page")]
    exc_pages = []
    for k in exc:
        exc_pages.extend(HTML_RE.findall(k))
    # excluded 的 value 里也可能点到别的页，但只认 key：key 才是「被排除的对象」

    dup = sorted(set(pub_pages) & set(exc_pages))
    note(not dup, "DUP", "无重复登记" if not dup
         else "同时出现在对外与排除：%s" % ", ".join(dup))

    unmapped = sorted(set(on_disk) - set(pub_pages) - set(exc_pages))
    note(not unmapped, "COVER",
         "全部 %d 个页面均已登记（对外 %d / 排除 %d）"
         % (len(on_disk), len(pub_pages), len(set(exc_pages)))
         if not unmapped else "未登记页面：%s" % ", ".join(unmapped))

    ghost = sorted((set(pub_pages) | set(exc_pages)) - set(on_disk))
    if ghost:
        note(False, "COVER", "登记了但磁盘上没有：%s" % ", ".join(ghost))

    # ── 4 LINK ────────────────────────────────────────────────
    if not os.path.isfile(INDEX):
        note(False, "LINK", "入口页 index.html 不存在（/houfeng/ 会退回 403）")
    else:
        t = io.open(INDEX, encoding="utf-8", errors="replace").read()
        hrefs = re.findall(r'href="([^"]+)"', t)
        targets, broken = [], []
        for h in hrefs:
            if h.startswith(("http://", "https://", "#", "mailto:", "javascript:")):
                continue
            name = h[len("/houfeng/"):] if h.startswith("/houfeng/") else h
            if name.startswith("/"):
                continue            # 站内其他路径，不属本目录
            name = name.split("#")[0].split("?")[0]
            if not name:
                continue
            targets.append(name)
            if not os.path.isfile(os.path.join(HF, name)):
                broken.append(name)
        note(not broken, "LINK",
             "入口页内链 %d 条，全部可达" % len(set(targets))
             if not broken else "断链：%s" % ", ".join(sorted(set(broken))))

    # ── 5 DATE ────────────────────────────────────────────────
    real = set()
    for f in DATE_SOURCES:
        fp = os.path.join(HF, f)
        if not os.path.isfile(fp):
            continue
        try:
            d = json.load(io.open(fp, encoding="utf-8"))
            end = (d.get("window") or {}).get("end") or (d.get("meta") or {}).get("asof")
            if end:
                e = str(end).replace("-", "")
                real.add("%s-%s-%s" % (e[:4], e[4:6], e[6:8]))
        except Exception:
            continue
    if not real:
        note(False, "DATE", "真源里一个数据日都没读到：无法校验，按失败处理（不静默放过）")
    else:
        shown = set()
        if os.path.isfile(INDEX):
            # 必须先把标签剥掉再抽日期：页面里是「数据算到 <b>2026-11-06</b>」，
            # 按纯文本正则匹配会一个都抽不到，从而误报「没有日期」。
            plain = re.sub(r"<[^>]+>", "", io.open(
                INDEX, encoding="utf-8", errors="replace").read())
            shown = set(DATE_ON_PAGE_RE.findall(plain))
        if not shown:
            note(False, "DATE", "入口页没有任何「数据算到」日期：读者无从判断新旧")
        else:
            bogus = shown - real
            note(not bogus, "DATE",
                 "页面数据日 %s 均出自真源（真源 %s）"
                 % ("/".join(sorted(shown)), "/".join(sorted(real)))
                 if not bogus else
                 "页面写出真源里没有的日期：%s（真源只有 %s）"
                 % (", ".join(sorted(bogus)), ", ".join(sorted(real))))

    print("\n".join(lines))
    verdict = "PASS" if not fails else "FAIL"
    print("\n结论 %s：%d 项通过 / %d 项失败"
          % (verdict, sum(1 for l in lines if l.startswith("[OK]")), len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
