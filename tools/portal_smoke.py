# -*- coding: utf-8 -*-
"""门户 8091 全路由只读冒烟自检（P4 基座拆分跨域回归固化）。

背景：P4 基座拆分后，门户（portal_api.py，8091）下各路由分属不同
模块/域名，历史上出过 K-STOCK-141 /council 返回内容判型错误导致
下游静默走错分支的教训。本工具把"门户全路由探活"固化为一条命令，
供发布后/晨检/跨域回归时快速确认 16 个只读面全部在线。

原则：
1. 纯只读——只发 GET，绝不 POST，绝不改 portal/ 下任何文件；
2. 禁代理直连——本机 http_proxy 会劫持 127.0.0.1 请求（实测踩坑），
   必须用 build_opener(ProxyHandler({})) 强制直连，并同时关闭
   自动重定向以便如实观测 302（根路径 / 302 算通过）；
3. 退出码：全绿=0，任一失败=1，可直接挂晨检/CI。

用法：
    python tools/portal_smoke.py [--base URL] [--timeout S]
                                 [--json] [--fail-fast]

--json 输出单行机器可读结果（对齐 tools/check_ports.py --json 先例，
人读横幅关闭）；--fail-fast 遇首个失败立即停止并返回 1。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# 16 个只读探活面（GET，预期 200；根路径 302 也算通过）
ROUTES = [
    "/health", "/council", "/council?q=a", "/sim", "/debate",
    "/screen", "/kg", "/bridge", "/intel", "/gzh", "/audit-ai",
    "/devops", "/org", "/aim", "/vd", "/",
]
ROOT_302_OK = "/"  # 仅根路径允许 302（跳首页）


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向，让 302 如实暴露给探活判定。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener():
    """禁代理直连 + 禁自动重定向（http_proxy 劫持 127.0.0.1 是实测坑）。"""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect)


def probe(opener, base, path, timeout):
    """对单个路由发 GET，返回 (status, nbytes, ms, err)。

    status：HTTP 状态码（302 如实返回）；EX 类型时 status 为异常类名。
    """
    url = base.rstrip("/") + path
    t0 = time.perf_counter()
    try:
        with opener.open(url, timeout=timeout) as resp:
            body = resp.read()
            ms = (time.perf_counter() - t0) * 1000.0
            return resp.status, len(body), ms, None
    except urllib.error.HTTPError as e:  # _NoRedirect 把 3xx 抛成 HTTPError
        try:
            n = len(e.read())
        except Exception:
            n = 0
        ms = (time.perf_counter() - t0) * 1000.0
        return e.code, n, ms, None
    except Exception as e:  # URLError/TimeoutError/ConnectionError...
        ms = (time.perf_counter() - t0) * 1000.0
        return "EX:%s" % type(e).__name__, 0, ms, str(e)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="门户 8091 全路由只读冒烟自检（仅 GET）")
    ap.add_argument("--base", default="http://127.0.0.1:8091",
                    help="门户基址（默认 http://127.0.0.1:8091）")
    ap.add_argument("--timeout", type=float, default=60,
                    help="单面超时秒数（默认 60）")
    ap.add_argument("--json", action="store_true",
                    help="输出单行 JSON（机器可读，人读横幅关闭）")
    ap.add_argument("--fail-fast", action="store_true",
                    help="遇首个失败立即停止")
    args = ap.parse_args(argv)

    say = (lambda *a, **k: None) if args.json else print
    opener = build_opener()
    results = []
    failed = []
    say("== 门户只读冒烟 %s（%d 面，仅 GET，禁代理直连）=="
        % (args.base, len(ROUTES)))
    for path in ROUTES:
        status, nbytes, ms, err = probe(opener, args.base, path,
                                        args.timeout)
        ok = (status == 200) or (status == 302 and path == ROOT_302_OK)
        results.append({"path": path, "status": status,
                        "bytes": nbytes, "ms": round(ms, 1), "ok": ok})
        say("  %-14s %-10s %7d B  %8.1f ms  %s"
            % (path, status, nbytes, ms, "✓" if ok else "✗ FAIL"))
        if not ok:
            failed.append(path)
            if err:
                say("      ↳ %s" % err)
            if args.fail_fast:
                say("  --fail-fast：首败即停，剩余面不探。")
                break
    ok_n = len(ROUTES) - len(failed) if not args.fail_fast else \
        sum(1 for r in results if r["ok"])
    say("SMOKE ok=%d fail=[%s]" % (ok_n, ",".join(failed)))
    if args.json:
        print(json.dumps({
            "base": args.base,
            "results": results,
            "summary": {"total": len(ROUTES), "ok": ok_n,
                        "fail": failed},
            "rc": 1 if failed else 0,
        }, ensure_ascii=False, separators=(",", ":")))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
