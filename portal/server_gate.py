# -*- coding: utf-8 -*-
"""今日选股公开访问壳：访问码 Cookie 门禁 + 路径白名单（源头版，进 git）。

K-STOCK-408 起本文件是唯一源头，sync_publish.py 映射为
picks_public/server.py（gitignored 快照）随 deploy 上线——原先
server.py 游离于版本管理外的状态终结（改动可评审可追溯）。

发布形态：单端口 HTTP 服务（读 $PORT，绑 0.0.0.0）。
只暴露 picks.html 所需资源，绝不透传目录其他文件：
  /                → picks.html
  /picks.html
  /static/tokens.css
  /data/todays_picks.json
  /data/pick_details/<code>.json   （仅 [A-Za-z0-9_.-]+.json，天然防目录穿越）

鉴权（2026-10-08 实测：反向代理会剥 Authorization 头，Basic Auth 不可用）：
  1) 首次进入 /?key=<访问码> → 校验通过则 Set-Cookie picks_key（30 天）
  2) 之后所有请求（含页面内 fetch）自动带 Cookie
  3) /login 提供访问码输入页；Basic Auth 仍兼容保留（curl 调试用）
口令来源：环境变量 PICKS_PW / PICKS_PW_OLD > 代码默认（轮换即改默认值
+重新 deploy；私有仓=部署事实记录，泄露应急改值+deploy 即时作废）。

K-STOCK-408 双口令过渡（14 天兼容期）：
  - 已发卡按钮 URL 带旧码 ?key=…、已存设备 Cookie 也是旧码——旧码命中
    一律放行，且凡经 ?key= 进入顺手种**新码** Cookie（用户无感换轨）；
  - 兼容期截止（OLD_PW_EXPIRE）后：删 OLD_PW 默认值与 expire 注释，
    sync + deploy 一次即作废旧码（工单随轮换执行补记）。
"""

import base64
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import logging

PORT = int(os.environ.get("PORT", "3000"))
USERNAME = os.environ.get("PICKS_USER", "lao")
# ── 口令默认值（K-STOCK-409 轮换执行 2026-10-15：NEW 已换新码）──
NEW_PW = os.environ.get("PICKS_PW", "picks-6d8b1507")
OLD_PW = os.environ.get("PICKS_PW_OLD", "picks-15fdb9")
OLD_PW_EXPIRE = "2026-10-29"  # 旧码作废日（到期删本行+OLD 默认值，sync+deploy 即作废）
PASSWORD = NEW_PW  # 兼容旧变量名（日志/说明引用）
COOKIE = "picks_key"
HERE = os.path.dirname(os.path.abspath(__file__))

ROUTES = [
    (re.compile(r"^/$"), "picks.html"),
    (re.compile(r"^/picks\.html$"), "picks.html"),
    (re.compile(r"^/index\.html$"), "picks.html"),
    (re.compile(r"^/ledger\.html$"), "ledger.html"),  # K-STOCK-353 交易台账
    (re.compile(r"^/static/tokens\.css$"), "static/tokens.css"),
    (re.compile(r"^/data/todays_picks\.json$"), "data/todays_picks.json"),
    (re.compile(r"^/data/ledger\.json$"), "data/ledger.json"),  # K-STOCK-353
]
DETAIL_RE = re.compile(r"^/data/pick_details/([A-Za-z0-9_.\-]+)\.json$")
# K-PUSH-002 A1：分享令牌访问收口（token 即 72h 限时凭证——商业化
# 「分享试看」语义；内网链路落 visit 账，公网静态站无此路由降级 404）
SHARE_RE = re.compile(r"^/s/(st\.[A-Za-z0-9_\-]+\.[0-9a-f]{16})$")
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

LOGIN_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>访问码 · 今日选股</title><style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#0d1117;color:#e6edf3;
display:grid;place-items:center;min-height:100vh;margin:0}
.box{background:#161b22;border:1px solid #30363d;border-radius:14px;padding:32px 28px;width:min(360px,88vw)}
h1{font-size:17px;margin:0 0 6px}p{color:#8b949e;font-size:12.5px;margin:0 0 18px;line-height:1.6}
input{width:100%;box-sizing:border-box;padding:11px 13px;border-radius:9px;border:1px solid #30363d;
background:#0d1117;color:#e6edf3;font-size:15px;outline:none}
input:focus{border-color:#58a6ff}
button{width:100%;margin-top:12px;padding:11px;border:none;border-radius:9px;cursor:pointer;
background:#1f6feb;color:#fff;font-size:14px;font-weight:700}
.err{color:#f85149;font-size:12px;margin-top:10px;min-height:16px}
</style></head><body><div class="box">
<h1>🎯 今日选股看板</h1><p>私有数据，请输入访问码（见飞书推送卡片）。</p>
<input id="k" type="password" placeholder="访问码" autofocus>
<button onclick="go()">进入</button><div class="err" id="e"></div></div>
<script>function go(){var k=document.getElementById('k').value.trim();
if(!k){document.getElementById('e').textContent='请输入访问码';return}
location.href='/?key='+encodeURIComponent(k)}
document.getElementById('k').addEventListener('keydown',function(ev){
if(ev.key==='Enter')go()});</script></body></html>"""


def _key_ok(k):
    """K-STOCK-408：新旧口令都放行（兼容期）；空码/错码拒。"""
    return bool(k) and k in (NEW_PW, OLD_PW)


class Handler(BaseHTTPRequestHandler):
    server_version = "PicksGate/1.2"

    def _basic_ok(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            u, p = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)
        except Exception:
            return False
        return u == USERNAME and _key_ok(p)

    def _cookie_value(self):
        m = re.search(r"(?:^|;\s*)%s=([^;]+)" % COOKIE, self.headers.get("Cookie", ""))
        return m.group(1) if m else None

    def _cookie_ok(self):
        return _key_ok(self._cookie_value())

    def _query_key_ok(self):
        qs = parse_qs(urlparse(self.path).query)
        return _key_ok(qs.get("key", [""])[0])

    def _gate_ok(self):
        return self._basic_ok() or self._cookie_ok() or self._query_key_ok()

    def _send(self, code, body, ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra or []:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _serve_file(self, rel, extra=None):
        fp = os.path.join(HERE, rel)
        if not os.path.isfile(fp):
            self._send(404, b"404 not found")
            return
        with open(fp, "rb") as f:
            body = f.read()
        ext = os.path.splitext(rel)[1]
        extra = list(extra or [])
        if ext == ".json":
            extra.append(("Cache-Control", "no-store"))
        self._send(200, body, MIME.get(ext, "application/octet-stream"), extra)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/login":
            self._send(200, LOGIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        # K-PUSH-002 A1：分享链接访问收口——token 校验通过即落 visit 账
        # 并 302 带码进板（与「带访问码直达」同安全口径：码已在卡片 URL
        # 流转，token 额外带 72h 时限+溯源 jti）；过期/坏 token 引导去登录页
        m = SHARE_RE.match(path)
        if m:
            try:
                import share_tokens

                payload = share_tokens.visit(m.group(1))
            except Exception:
                payload = None
            if payload:
                loc = "/picks.html?key=%s" % NEW_PW
                self._send(302, b"", extra=[("Location", loc)])
            else:
                self._send(302, b"", extra=[("Location", "/login")])
            return
        if not self._gate_ok():
            body = (
                "需要访问码。请从带 ?key= 的链接进入，"
                "或打开 <a href='/login'>/login</a> 输入访问码。"
            ).encode("utf-8")
            self._send(
                401,
                body,
                "text/plain; charset=utf-8",
                [("WWW-Authenticate", 'Basic realm="Picks Portal", charset="UTF-8"')],
            )
            return
        # 查询串带访问码 → 顺手种 30 天 **新码** Cookie；旧码进入即无感换轨
        # （含身上还挂着旧 Cookie 的设备—— Cookie 已是新码则不必重种）
        if self._query_key_ok() and self._cookie_value() != NEW_PW:
            ck = "%s=%s; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax" % (
                COOKIE,
                NEW_PW,
            )
            for pat, rel in ROUTES:
                if pat.match(path):
                    self._serve_file(rel, extra=[("Set-Cookie", ck)])
                    return
            m = DETAIL_RE.match(path)
            if m:
                self._serve_file(
                    "data/pick_details/%s.json" % m.group(1), extra=[("Set-Cookie", ck)]
                )
                return
            self._send(404, b"404 not found", extra=[("Set-Cookie", ck)])
            return
        for pat, rel in ROUTES:
            if pat.match(path):
                self._serve_file(rel)
                return
        m = DETAIL_RE.match(path)
        if m:
            self._serve_file("data/pick_details/%s.json" % m.group(1))
            return
        self._send(404, b"404 not found")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        # K-PUSH-002 A2：picks.html 前端 ?st= beacon——校验+落 visit 账后
        # 204（fetch fire-and-forget，不跟重定向不取响应体）；公网静态站
        # 无此路由 → 前端 catch 静默，降级口径见 share_tokens 模块头
        m = SHARE_RE.match(urlparse(self.path).path)
        if m:
            try:
                import share_tokens

                share_tokens.visit(m.group(1))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/server_gate.py", exc)
            self._send(204, b"")
            return
        self._send(404, b"404 not found")

    def log_message(self, fmt, *args):  # noqa: A003
        pass  # 静默访问日志（公开服务不留请求明细）


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # nosec B104 - 对外门禁服务，依赖网络 ACL/basic-auth
    print("PicksGate v1.2 on 0.0.0.0:%d (key-gate+cookie, basic-auth kept)" % PORT)
    srv.serve_forever()
