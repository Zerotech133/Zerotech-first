# -*- coding: utf-8 -*-
"""从本机浏览器抓雪球登录态 cookie 灌 token 池（xq_grab_browser.py）。

场景（2026-10-17 老田通道）：用户在 Edge/Chrome 各登录一个雪球账号，
本脚本按浏览器分别提取 xq_a_token → token_probe 验活 → 按名灌池。

用法（py311）：
  python xq_grab_browser.py --edge-name tok-u-150 --chrome-name tok-u-1898
  （只抓已登录的：某浏览器未登录/失败则跳过并提示）
前提：目标浏览器已完全关闭（cookie DB 锁定会导致抓取失败）。

判例：登录态 token 靠 token_probe 验活区分（浏览器匿名也有 xq_a_token，
probe valid=False 的丢弃不灌池）；绝不打印 token 明文。
"""

import argparse
import os
import sys
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def grab(browser: str) -> str:
    """rookiepy 抓指定浏览器 .xueqiu.com 的 xq_a_token。"""
    import rookiepy

    getter = getattr(rookiepy, browser)
    cookies = getter([".xueqiu.com"])
    if not isinstance(cookies, list):
        return ""
    return next(
        (
            str(c.get("value", ""))
            for c in cookies
            if c.get("name") == "xq_a_token" and c.get("value")
        ),
        "",
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xq_grab_browser.py", exc)
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge-name", default="")
    ap.add_argument("--chrome-name", default="")
    ap.add_argument("--firefox-name", default="")
    a = ap.parse_args()

    plan = [
        ("edge", a.edge_name),
        ("chrome", a.chrome_name),
        ("firefox", a.firefox_name),
    ]
    ok_any = False
    for browser, name in plan:
        if not name:
            continue
        try:
            tok = grab(browser)
        except Exception as e:
            print(
                "[%s] 抓取失败：%s（浏览器是否未完全关闭？）" % (browser, str(e)[:100])
            )
            continue
        if not tok:
            print("[%s] 无 xq_a_token（未登录或 cookie 已清）" % browser)
            continue
        print("[%s] 抓到 xq_a_token=%s" % (browser, tok[:4] + "…(len=%d)" % len(tok)))
        from xq_login import verify_token, save_to_pool

        tp = verify_token(tok)
        print(
            "[%s] token_probe ok=%s valid=%s latency=%dms err=%s"
            % (browser, tp["ok"], tp.get("valid"), tp["latency_ms"], tp["err"] or "-")
        )
        if tp["ok"] and tp.get("valid"):
            save_to_pool(name, tok)
            ok_any = True
        else:
            print("[%s] 验活未通过（匿名 cookie 或失效），不灌池" % browser)
    print("[done] %s" % ("至少一个账号已入池" if ok_any else "无账号入池"))
    return 0 if ok_any else 1


if __name__ == "__main__":
    sys.exit(main())
