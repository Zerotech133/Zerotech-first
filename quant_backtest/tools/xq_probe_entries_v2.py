# -*- coding: utf-8 -*-
"""入口探针二期：Playwright 无头真浏览器过雪球 WAF 通道预备（只读）。

定谳背景（一期 tools/xq_probe_entries.py，2026-10-18 晚）：
  category feed 源停更于 2023-11-11（源死）；hot listV2 FAIL；
  search.json 404；user_timeline 匿名/带 cookie 均被 WAF JS 挑战
  （renderData textarea 形态）。
二期回答：无头真浏览器能否过 WAF、哪个入口可达。

合规铁律：
  - 只读 GET；显式导航总请求 ≤5；导航间隔 ≥2s；
  - cookie 仅浏览器内存态（非 persistent context），绝不落盘；
  - 不写库、不改任何现有文件；
  - 显式禁用系统代理后直连（WorkBuddy 宿主代理判例：对 127.0.0.1 返假 502）。

用法：
  python tools/xq_probe_entries_v2.py --dry
  python tools/xq_probe_entries_v2.py --live --entry user_timeline|hot|stock

三态判定：PASS（拿到 JSON/HTML 正常内容）/ WAF_CHALLENGE（renderData 等
JS 挑战特征）/ FAIL（403/404/超时/非预期内容）。结论行结构化输出到 stdout，
绝不落库。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
import logging

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_probe_entries_v2.py", exc)

SOCIAL = "https://xueqiu.com"
UA_FALLBACK = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1440, "height": 900}
MAX_REQUESTS = 5  # 显式导航硬上限（挑战页 JS 自动续跳不计、但总量仍留裕度）
GAP_SECONDS = 2.2  # 导航间隔下限
NAV_TIMEOUT_MS = 20000
CHALLENGE_SETTLE_S = 3.5  # 挑战页 JS 自动重载的等待窗口

# 一期同款入口（user_id 取一期样本 9222060866）
ENTRIES = {
    "user_timeline": {
        "url": SOCIAL + "/v4/statuses/user_timeline.json",
        "params": {"user_id": 9222060866, "count": 5, "page": 1},
        "kind": "json",
        "warmup": True,  # 先访问首页过挑战拿内存 cookie，再打 API
    },
    "hot": {
        "url": SOCIAL + "/v4/statuses/hot/listV2.json",
        "params": {"count": 30, "page": 1, "source": "user"},
        "kind": "json",
        "warmup": True,
    },
    "stock": {
        "url": SOCIAL + "/S/SH600519",
        "params": {},
        "kind": "html",
        "warmup": False,  # 个股页本身就是完整 HTML 页面，直接导航
    },
}

# WAF/JS 挑战特征（一期判例：renderData textarea 形态；二期实测新增：访问验证页）
WAF_MARKERS = (
    "renderData",
    "<textarea",
    "acw_sc__v2",
    "jsl_clearance",
    "滑动验证",
    "请开启JavaScript",
    "Just a moment",
    "访问验证",
    "为了更好的访问体验，请进行验证",
)


def clear_proxy_env() -> list:
    """清空代理环境变量（返回被清除项，供报告）。"""
    removed = []
    for k in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        if k in os.environ:
            removed.append("%s=%s" % (k, os.environ[k]))
            os.environ.pop(k, None)
    return removed


class Budget:
    """显式导航计数器，硬上限 MAX_REQUESTS。"""

    def __init__(self):
        self.n = 0

    def goto(self, page, url):
        if self.n >= MAX_REQUESTS:
            raise RuntimeError("request budget exhausted (%d)" % MAX_REQUESTS)
        self.n += 1
        return page.goto(url, wait_until="load", timeout=NAV_TIMEOUT_MS)


def read_body(page) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def classify(kind: str, status, body: str) -> tuple:
    """→ (verdict, detail)。verdict ∈ PASS / WAF_CHALLENGE / FAIL。"""
    if status is not None and int(status) >= 400:
        return "FAIL", "HTTP %d" % int(status)
    head = body[:4000].replace("\n", " ")
    for m in WAF_MARKERS:
        if m in head:
            return "WAF_CHALLENGE", "marker=%r head=%r" % (m, head[:120])
    if kind == "json":
        txt = body.strip()
        if txt.startswith("{") or txt.startswith("["):
            try:
                data = json.loads(txt)
                n = len(data.get("items", [])) if isinstance(data, dict) else len(data)
                return "PASS", "json ok items=%d" % n
            except ValueError:
                return "FAIL", "json parse error head=%r" % head[:120]
        return "FAIL", "not json head=%r" % head[:120]
    # html 个股页
    if "SH600519" in body or "雪球" in body:
        return "PASS", "html ok len=%d" % len(body)
    return "FAIL", "html unexpected head=%r" % head[:120]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="雪球入口探针二期（Playwright 通道，只读）"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--dry",
        action="store_true",
        help="只启动浏览器+访问 about:blank，验证通道，零雪球访问",
    )
    g.add_argument("--live", action="store_true", help="打真实入口")
    ap.add_argument("--entry", choices=sorted(ENTRIES), default="user_timeline")
    args = ap.parse_args()

    removed = clear_proxy_env()
    print("[i] proxy env cleared: %s" % (removed or "(none set)"))

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-proxy-server"
            ],  # 判例：direct:// 挡不住 Windows 系统代理，须命令行级禁用
        )
        try:
            # --- 探测浏览器自身 UA（零网络请求），HeadlessChrome 掩蔽为 Chrome ---
            probe_ctx = browser.new_context()
            probe_page = probe_ctx.new_page()
            probe_page.goto("about:blank")
            ua = probe_page.evaluate("navigator.userAgent") or UA_FALLBACK
            probe_ctx.close()
            if "HeadlessChrome" in ua:
                ua = ua.replace("HeadlessChrome", "Chrome")
            print("[i] UA: %s" % ua)

            ctx = browser.new_context(
                user_agent=ua,
                viewport=VIEWPORT,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = ctx.new_page()
            budget = Budget()

            if args.dry:
                page.goto("about:blank")
                ok = page.evaluate("() => document.title === ''")
                print(
                    json.dumps(
                        {
                            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "mode": "dry",
                            "verdict": "PASS" if ok else "FAIL",
                            "detail": "chromium headless + about:blank 通道可用",
                            "requests": budget.n,
                        },
                        ensure_ascii=False,
                    )
                )
                return 0 if ok else 2

            # --- live ---
            ent = ENTRIES[args.entry]
            time.sleep(GAP_SECONDS)
            if ent["warmup"] and budget.n < MAX_REQUESTS:
                resp = budget.goto(
                    page, SOCIAL + "/"
                )  # 请求 1：首页过挑战/拿内存 cookie
                time.sleep(CHALLENGE_SETTLE_S)
                w1, d1 = classify(
                    "html", resp.status if resp else None, read_body(page)
                )
                print("[i] warmup %s %s" % (w1, d1))
                time.sleep(GAP_SECONDS)

            url = ent["url"]
            if ent["params"]:
                url += "?" + "&".join("%s=%s" % kv for kv in ent["params"].items())
            resp = budget.goto(page, url)  # 请求 2：目标入口
            status = resp.status if resp else None

            verdict, detail = "FAIL", "no body"
            for attempt in range(2):  # 挑战页可能 JS 自动重载后复活
                verdict, detail = classify(ent["kind"], status, read_body(page))
                if verdict != "WAF_CHALLENGE" or attempt == 1:
                    break
                time.sleep(CHALLENGE_SETTLE_S)

            rec = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": "live",
                "entry": args.entry,
                "url": url,
                "http": status,
                "verdict": verdict,
                "detail": detail,
                "requests": budget.n,
            }
            print(json.dumps(rec, ensure_ascii=False))
            return 0 if verdict == "PASS" else 2
        finally:
            browser.close()  # 内存态 cookie 随进程销毁，无落盘


if __name__ == "__main__":
    sys.exit(main())
