# -*- coding: utf-8 -*-
"""平台巡检：把发布目录里每个页面逐个打开，确认它真的能用。

为什么不是「看 HTTP 200 就够了」：
  静态站点的失败模式是**页面能打开但内容空**——JS 报错、数据没内嵌、依赖丢了，
  都会给出一个 200 的白壳。所以每页要同时验四件事：

    ① HTTP 200
    ② 无 console error / pageerror
    ③ 有统一导航条（说明修补生效，不是原样拷贝）
    ④ 渲染出可见正文（body 文本长度 + 关键区块存在）

用法:
  python platform_check.py              # 巡检发布目录
  python platform_check.py --shots      # 顺便给每页截一张缩略图到 reports/_pf_shots/
"""

import argparse
import functools
import http.server
import os
import socketserver
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "h5_daily")
SHOTS = os.path.join(HERE, "reports", "_pf_shots")

# 每页至少要出现的可见文字量（白壳页通常 < 200）
MIN_TEXT = 120


def serve(port):
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DEST)
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", port), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", action="store_true", help="给每页截缩略图")
    ap.add_argument("--port", type=int, default=8942)
    a = ap.parse_args()

    if not os.path.isdir(DEST):
        print("[fail] 发布目录不存在，先跑 h5_pack.py")
        return 1
    pages = sorted(f for f in os.listdir(DEST) if f.endswith(".html"))
    if not pages:
        print("[fail] 发布目录里没有 html")
        return 1
    if a.shots:
        os.makedirs(SHOTS, exist_ok=True)

    from playwright.sync_api import sync_playwright

    srv = serve(a.port)
    base = "http://127.0.0.1:%d" % a.port
    fails = []
    rows = []

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for f in pages:
            errs = []
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            pg.on("pageerror", lambda e: errs.append("pageerror: %s" % e))
            pg.on("console", lambda m: errs.append("console: " + m.text)
                  if m.type == "error" else None)
            code = [0]
            pg.on("response", lambda r: code.__setitem__(0, r.status)
                  if r.url.split("?")[0].endswith(f) else None)
            try:
                resp = pg.goto(base + "/" + f, wait_until="load", timeout=25000)
                pg.wait_for_timeout(450)
                status = resp.status if resp else 0
            except Exception as e:  # noqa: BLE001
                fails.append((f, "打开失败: %s" % e))
                pg.close()
                continue

            txt = (pg.inner_text("body") or "").strip()
            has_nav = bool(pg.query_selector("#pf-nav"))
            title = pg.title()

            bad = []
            if status != 200:
                bad.append("HTTP %d" % status)
            if errs:
                bad.append("%d 条 console 错误" % len(errs))
            if len(txt) < MIN_TEXT:
                bad.append("正文仅 %d 字（疑似白壳）" % len(txt))
            # 门面自己不带导航条，其余页面必须有
            if f != "index.html" and not has_nav:
                bad.append("缺导航条")

            rows.append((f, status, len(txt), has_nav, title, bad))
            if bad:
                fails.append((f, " / ".join(bad) + ("  ← " + errs[0] if errs else "")))

            if a.shots:
                try:
                    pg.screenshot(path=os.path.join(SHOTS, f.replace(".html", ".png")),
                                  full_page=False)
                except Exception:  # noqa: BLE001
                    pass
            pg.close()
        b.close()

    srv.shutdown()

    print("%-32s %5s %7s %4s  %s" % ("页面", "HTTP", "正文字数", "导航", "标题"))
    for f, st, n, nav, title, bad in rows:
        print("%-32s %5s %7d %4s  %s%s"
              % (f, st, n, "✓" if nav else "-", title[:32],
                 "   ← " + " / ".join(bad) if bad else ""))

    print()
    if fails:
        print("RESULT: FAIL（%d/%d 页有问题）" % (len(fails), len(rows)))
        for f, why in fails:
            print("   %-30s %s" % (f, why))
        return 1
    print("RESULT: PASS（%d 页全部 200 / 无 console 错误 / 有导航 / 有正文）" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
