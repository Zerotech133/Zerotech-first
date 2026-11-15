# -*- coding: utf-8 -*-
"""缠论新域 I1a/I1b 上屏验收（自包含、不动任何线上服务）。

为什么存在这个脚本（2026-11-15 09:36）
======================================
代码、单测、路由基线、双推都已落地，唯一没拿到的是「真实 HTTP 层的 200 +
导航 + 结论渲染」这一层证据。当时 portal 8080/8091 无监听、nginx 容器
（ai-portal）未运行，而拉起服务属系统级改动，周末执行会污染 11-16 首个
真实交易日观察窗。

本脚本把「验收」与「重启服务」解耦：
  · --offline（默认）：在 127.0.0.1 的**临时端口**（port=0，由 OS 分配）起一个
    随进程退出的 HTTPServer，do_GET 直接调 api_chanlun_domain.handle_get，
    用 http.client 直连发真实请求。不占 8080/8091、不注册服务、不写系统配置、
    不 reload nginx、不启动 Docker。进程退出，服务即消失。
  · --online --base http://127.0.0.1:8080：同一套判据打到真实门户，供上屏后
    一次性复核（由运维在批准窗口内执行）。

三态纪律（沿用 verify_paper_trade 的 PASS/FAIL/PENDING 口径）
============================================================
  · 没评估过的判据一律 PENDING，绝不因为「没报错」就写 PASS；
  · FAIL 任意一条 -> rc=1；有 PENDING 但无 FAIL -> rc=0 + 醒目警告；
  · 全 PASS -> rc=0。
  · `nginx -t` / reload / 线上 200 / 老 8 页线上回归：nginx 只存在于容器
    ai-portal 内（PATH 无二进制是常态，不是缺陷），`nginx -t` 须经
    `docker exec`；当前 Docker daemon 未运行、容器未起，一律标 PENDING，**不假报**。

配置结构（CRLF、location 选择、set $h、非合并头）由既有
`portal/tests/test_i1b_nginx_route.py` 承担，本脚本不重复造轮子，只补 HTTP 层。

--self-test 是 falsifier：故意起一个「所有路径都返回 200 空壳页」的假服务，
同一套判据必须判 FAIL。若它全 PASS，说明判据恒真、验收脚本本身假绿，
此时脚本自己报错退出（补丁 rc=0 != 逻辑对）。
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "portal"
POOL = ROOT / "quant_backtest" / "data" / "chanlun_pool.jsonl"
PAPER = ROOT / "quant_backtest" / "data" / "chanlun_paper.json"

# 4 页 + 302 + 404。HAS_VERDICT 是「这一页的管线里是否有判定」的事实基线，
# 来自 I1a 步 3 与 I1b 的如实停工结论：action/learn 无判定，不许硬凑。
PAGES = {
    "/chanlun/": ("today", True),
    "/chanlun/radar": ("radar", True),
    "/chanlun/action": ("action", False),
    "/chanlun/learn": ("learn", False),
}
SLOW_LIMIT_S = 1.5
I1A_SLOW_LIMIT_S = 8.0

# I1a 步 2/3 的三个活页：上屏后与新域一起验，避免周一漏项。
# 三页结论标记不一致（实测）：pool/brief 用 ui_parts.conclusion() 组件，
# signals 是纯文本插入 <div class="meta">，没有 ui-conc-text ——
# 判据必须按页定制，用统一判据会造出假红/假绿。
I1A_PAGES = {
    "/bt-chanlun-pool": ("预警池", "component"),
    "/bt-brief": ("AI 晨报", "component"),
    "/bt-signals": ("信号页", "plaintext"),
}


# ── 三态账本 ────────────────────────────────────────────────────────
class Ledger:
    def __init__(self):
        self.items = []

    def add(self, name, ok, detail="", state=None):
        st = state or ("PASS" if ok else "FAIL")
        self.items.append((name, st, detail))
        return st

    def pending(self, name, why):
        self.items.append((name, "PENDING", why))

    def counts(self):
        out = {"PASS": 0, "FAIL": 0, "PENDING": 0}
        for _, st, _ in self.items:
            out[st] += 1
        return out

    def report(self, quiet=False):
        if not quiet:
            for name, st, detail in self.items:
                tail = (" · " + detail) if detail else ""
                print("  [%s] %s%s" % (st.ljust(7), name, tail))
        c = self.counts()
        print("SUMMARY PASS=%d FAIL=%d PENDING=%d" %
              (c["PASS"], c["FAIL"], c["PENDING"]))
        return c


# ── HTTP：http.client 直连，不经系统 HTTP_PROXY ──────────────────────
# 本机 HTTP_PROXY 指向本地代理端口，curl/urllib 访问 127.0.0.1 会拿到代理的
# 94 字节 502（观测面陷阱）。http.client.HTTPConnection 不读代理环境变量，
# 是这里唯一可靠的观测通道。
def request(host, port, path):
    conn = http.client.HTTPConnection(host, port, timeout=8)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, dict(resp.getheaders()), body
    finally:
        conn.close()


class _DomainHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        import api_chanlun_domain as cd
        path = self.path.split("?", 1)[0]
        cd.handle_get(self, path, "")

    def log_message(self, *_args):
        pass


class _FakeHandler(BaseHTTPRequestHandler):
    """falsifier 专用：所有路径返回 200 空壳，用来证明判据不是恒真。"""
    def do_GET(self):
        body = b"<!DOCTYPE html><html><body>empty</body></html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class EphemeralServer:
    def __init__(self, handler_cls):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


# ── HTML 判据 ────────────────────────────────────────────────────────
def _plain(fragment):
    return re.sub(r"<[^>]+>", " ", fragment)


def _conclusion_text(html):
    blocks = re.findall(
        r'<div class="ui-conc-text">(.*?)</div>', html, re.S)
    return _plain(" ".join(blocks))


def _check_page(led, path, tag, has_verdict, status, headers, body, elapsed):
    label = "HTTP %s" % path
    text = body.decode("utf-8", errors="replace")
    conc = _conclusion_text(text)

    led.add(label + " / 200", status == 200, "status=%s" % status)
    declared = headers.get("Content-Length")
    led.add(label + " / Content-Length 一致",
            declared is not None and int(declared) == len(body),
            "declared=%s actual=%d" % (declared, len(body)))
    led.add(label + " / HTML 文档", "<!DOCTYPE html>" in text)
    led.add(label + " / L2 导航", "nav.js" in text)
    led.add(label + " / asof 徽章", "ui-asof" in text)
    led.add(label + " / 结论槽存在", bool(conc.strip()),
            "conclusion_chars=%d" % len(conc.strip()))
    led.add(label + " / 首屏预算 < %.1fs" % SLOW_LIMIT_S,
            elapsed < SLOW_LIMIT_S, "%.3fs" % elapsed)

    if has_verdict:
        led.add(label + " / 有管线判定",
                "最新检出" in conc and "本页无管线判定" not in conc,
                conc[:60])
    else:
        led.add(label + " / 如实标注无判定（不硬凑）",
                "本页无管线判定" in conc and "最新检出" not in conc,
                conc[:60])


# ── 与源数据逐项对账（只在 offline 做，数据同机可读） ────────────────
def _source_numbers():
    rows = []
    with POOL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    days = [r.get("detect_date") for r in rows if r.get("detect_date")]
    last = max(days)
    day = [r for r in rows if r.get("detect_date") == last]
    strong = [r for r in day
              if r.get("type") == "1\u5356" and r.get("tbe") is True]
    shield = sum(1 for r in day if r.get("tradable") is False)
    paper = json.loads(PAPER.read_text(encoding="utf-8"))
    return {
        "last": last,
        "day_n": len(day),
        "strong_n": len(strong),
        "shield_n": shield,
        "open_n": len(paper.get("open") or []),
        "closed_n": len(paper.get("closed") or []),
    }


def _check_source_numbers(led, conc):
    try:
        src = _source_numbers()
    except Exception as exc:
        led.pending("结论数字与源数据对账", "源数据不可读：%s" % exc)
        return
    for key, cn in (("last", "检出日"), ("day_n", "入池条数"),
                    ("strong_n", "强预警只数"), ("shield_n", "涨停屏蔽条数"),
                    ("open_n", "持仓笔数"), ("closed_n", "已平笔数")):
        val = str(src[key])
        led.add("结论数字对账 / %s = %s" % (cn, val), val in conc,
                "取自结论文本，非页面自算")


def run_i1a_checks(led, fetch):
    """I1a 三活页上屏验收。只在 --online（真实门户）跑；--self-test 也跑，
    用假空壳页证明这套判据能证伪。隔离服务覆盖不了巨石路由链与静态产物。"""
    for path, (cn, kind) in I1A_PAGES.items():
        label = "I1a %s %s" % (cn, path)
        try:
            status, _headers, body, elapsed = fetch(path)
            text = body.decode("utf-8", errors="replace")
            led.add(label + " / 200", status == 200, "status=%s" % status)
            if kind == "component":
                led.add(label + " / 结论组件", "ui-conc-text" in text)
                led.add(label + " / asof 徽章", "ui-asof" in text)
                led.add(label + " / 一句话结论", "一句话结论" in text)
            else:
                # signals 结论是纯文本：用其独有的、页内可回查的措辞做判据
                led.add(label + " / 结论文本", "逐行核对" in text)
                led.add(label + " / 数据日", "数据日" in text)
                led.add(label + " / 共振口径", "共振 ≥3" in text)
            led.add(label + " / 首屏预算 < %.1fs" % I1A_SLOW_LIMIT_S,
                    elapsed < I1A_SLOW_LIMIT_S, "%.3fs" % elapsed)
        except Exception as exc:
            led.add(label + " / 200", False, "请求异常 %s" % exc)


# ── 主验收 ───────────────────────────────────────────────────────────
def run_checks(led, fetch, *, offline):
    """fetch(path) -> (status, headers, body, elapsed)。"""
    # 302：= /chanlun 或 portal_api 转发，两者都应给相对 Location
    try:
        status, headers, body, _ = fetch("/chanlun")
        loc = headers.get("Location", "")
        led.add("HTTP /chanlun / 302", status == 302, "status=%s" % status)
        led.add("HTTP /chanlun / Location 指向 /chanlun/",
                loc.endswith("/chanlun/"), "Location=%s" % loc)
    except Exception as exc:
        led.add("HTTP /chanlun / 302", False, "请求异常 %s" % exc)

    today_conc = ""
    for path, (tag, has_verdict) in PAGES.items():
        try:
            status, headers, body, elapsed = fetch(path)
            _check_page(led, path, tag, has_verdict,
                        status, headers, body, elapsed)
            if tag == "today":
                today_conc = _conclusion_text(
                    body.decode("utf-8", errors="replace"))
        except Exception as exc:
            led.add("HTTP %s / 200" % path, False, "请求异常 %s" % exc)

    try:
        status, _headers, body, _ = fetch("/chanlun/__no_such_page__")
        led.add("HTTP 未知路径 / 404", status == 404, "status=%s" % status)
        led.add("HTTP 未知路径 / 不伪装成页面",
                "无此页面" in body.decode("utf-8", errors="replace"))
    except Exception as exc:
        led.add("HTTP 未知路径 / 404", False, "请求异常 %s" % exc)

    if offline and today_conc:
        _check_source_numbers(led, today_conc)
    else:
        led.pending("结论数字与源数据对账",
                    "线上模式不假设同机数据；上屏后如需复核请跑 --offline")

    # 明确未评估项，绝不因「没报错」写成 PASS
    led.pending("nginx -t 配置解析",
                "nginx 只在容器 ai-portal 内，须 docker exec；当前 daemon 未运行")
    led.pending("reload / 容器重建 / 8080 上线",
                "系统级改动，需五要素批准后在选定窗口执行")
    led.pending("老 8 页线上回归",
                "依赖 8080 在线；离线相对关系已由 test_i1b_nginx_route.py 固定")


def main(argv=None):
    ap = argparse.ArgumentParser(description="缠论新域上屏验收（不动服务）")
    ap.add_argument("--offline", action="store_true",
                    help="临时端口起隔离服务（默认）")
    ap.add_argument("--online", action="store_true",
                    help="打真实门户，需配合 --base")
    ap.add_argument("--base", default="http://127.0.0.1:8080",
                    help="线上基址（仅 --online 使用）")
    ap.add_argument("--self-test", action="store_true",
                    help="falsifier：假服务必须被判 FAIL")
    args = ap.parse_args(argv)

    if args.online and args.offline:
        print("ERROR: --online 与 --offline 互斥")
        return 2
    online = args.online and not args.self_test

    if args.self_test:
        led = Ledger()
        with EphemeralServer(_FakeHandler) as srv:
            def fetch(path):
                t = time.perf_counter()
                st, hd, bd = request("127.0.0.1", srv.port, path)
                return st, hd, bd, time.perf_counter() - t
            run_checks(led, fetch, offline=True)
            n_before = len(led.items)
            run_i1a_checks(led, fetch)
            i1a_fail = sum(1 for _, st, _ in led.items[n_before:]
                           if st == "FAIL")

            # online 分支可达性 smoke：周一要用的是 --online，不能让它成为
            # 「写了但从没跑过」的死代码。这里用同一个假服务走 urlsplit +
            # offline=False 的判据组合；不用 subprocess（本机 capture 有
            # WinError 6 环境句柄坑），但覆盖了 online 的全部业务路径。
            u = urlsplit("http://127.0.0.1:%d" % srv.port)

            def online_fetch(path):
                t = time.perf_counter()
                st, hd, bd = request(u.hostname, u.port, path)
                return st, hd, bd, time.perf_counter() - t

            led2 = Ledger()
            run_checks(led2, online_fetch, offline=False)
            run_i1a_checks(led2, online_fetch)
            online_c = led2.counts()
            print("\n--self-test: online 分支 smoke PASS=%d FAIL=%d PENDING=%d"
                  % (online_c["PASS"], online_c["FAIL"], online_c["PENDING"]))
            assert online_c["PENDING"] > 0, "online 分支应保留未评估项"
            assert online_c["FAIL"] > 0, "假服务必须被 online 判据判 FAIL"
        c = led.counts()
        print("\n--self-test: 假服务（全部 200 空壳）判据结果")
        led.report()
        # 恒真检测：两套判据都必须抓得住坏页面，任何一套恒真即视为脚本假绿
        if c["FAIL"] == 0 or i1a_fail == 0:
            print("SELF-TEST BROKEN: 判据恒真，验收脚本自身假绿")
            return 3
        print("SELF-TEST OK: 两套判据都能证伪（FAIL=%d，其中 I1a=%d）"
              % (c["FAIL"], i1a_fail))
        return 0

    if online:
        u = urlsplit(args.base)
        host, port = u.hostname or "127.0.0.1", u.port or 80
        led = Ledger()
        print("MODE online  base=%s" % args.base)

        def fetch(path):
            t = time.perf_counter()
            st, hd, bd = request(host, port, path)
            return st, hd, bd, time.perf_counter() - t
        try:
            run_checks(led, fetch, offline=False)
            run_i1a_checks(led, fetch)
        except Exception as exc:
            print("ERROR: 线上不可达 %s" % exc)
            return 1
        led.report()
        c = led.counts()
        return 1 if c["FAIL"] else 0

    # 默认 offline
    sys.path.insert(0, str(PORTAL))
    led = Ledger()
    print("MODE offline  ephemeral 127.0.0.1:<os-assigned>  (不碰 8080/8091)")
    with EphemeralServer(_DomainHandler) as srv:
        print("PORT %d" % srv.port)

        def fetch(path):
            t = time.perf_counter()
            st, hd, bd = request("127.0.0.1", srv.port, path)
            return st, hd, bd, time.perf_counter() - t

        # 冷启动闸门同款：巨石 + 新模块同进程可导入，排除「重启被 smoke BLOCK」
        try:
            import portal_api  # noqa: F401
            import api_chanlun_domain as cd
            led.add("冷启动 import smoke", hasattr(cd, "handle_get")
                    and hasattr(cd, "render_page"))
        except Exception as exc:
            led.add("冷启动 import smoke", False, "导入异常 %s" % exc)

        run_checks(led, fetch, offline=True)
        led.pending("I1a 三活页上屏验收",
                    "依赖巨石路由链与静态产物，隔离服务覆盖不了；"
                    "上屏后由 --online 一次跑完")
    print("")
    led.report()
    c = led.counts()
    if c["PENDING"]:
        print("WARNING: %d 项判据未评估（PENDING），不得据此宣告上屏验收通过"
              % c["PENDING"])
    return 1 if c["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
