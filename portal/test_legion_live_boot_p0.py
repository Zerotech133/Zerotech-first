# -*- coding: utf-8 -*-
"""P0 鉴权整改真实 HTTP 验证：临时环回端口启动 H2，不触碰现役 8096。
真实环回直连用真实 TCP；非环回来源通过仅限本测试的 handler 替换 client_address
模拟，不把伪造头当 TCP 对端，也不写入业务数据。
"""
import pytest
import json
import sys
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

import api_legion_ops_v2 as ops
import logging



def main():
    """完整 live 检查：临时环回端口起 H2，验证鉴权 / CORS 全链（手工运行）。"""
    TOKEN = "livetest-token-0f3a"
    ops._load_token = lambda: TOKEN

    class SimulatedPeerH2(ops.H2):
        """测试专用：仅在解析完请求后替换 TCP peer，用于覆盖非环回授权分支。"""

        def parse_request(self):
            valid = super().parse_request()
            if valid and self.headers.get("X-Test-Tcp-Peer"):
                self.client_address = (self.headers["X-Test-Tcp-Peer"], self.client_address[1])
            return valid


    srv = ThreadingHTTPServer(("127.0.0.1", 0), SimulatedPeerH2)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    BASE = "http://127.0.0.1:%d" % srv.server_port
    PASS = []


    def check(name, cond):
        if not cond:
            raise AssertionError("FAIL: " + name)
        PASS.append(name)


    def req(path, headers=None, data=None):
        r = urllib.request.Request(BASE + path, data=data, headers=headers or {})
        try:
            with safe_urlopen(r, timeout=5) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                body = json.loads(body)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/test_legion_live_boot_p0.py", exc)
            return e.code, dict(e.headers), body


    # 1. 真正无标记的本机 TCP 环回直连仍免 token；映射环回反代需 token。
    code, _, body = req("/api/legion/tri-party/meetings")
    check("本机直连会议列表 200", code == 200 and body.get("ok") is True)
    code, _, body = req("/api/legion/tri-party/meetings",
                        {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"})
    check("映射环回反代自报双头无 token 拒绝", code == 403 and body.get("ok") is False)
    code, _, body = req("/api/legion/tri-party/meetings",
                        {"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1",
                         "X-Portal-Token": TOKEN})
    check("映射环回反代持 token 放行", code == 200 and body.get("ok") is True)

    # 2. 用测试专用 handler 模拟 nginx 容器、bridge 网关及普通远端 TCP 对端。
    private_reads = ("/api/legion/tri-party/meetings",
                     "/api/legion/tri-party/meeting?meeting_id=x",
                     "/api/legion/room/list", "/api/legion/room?room_id=x",
                     "/api/legion/room/resolution?room_id=x&resolution_id=y")
    data = json.dumps({"meeting_id": "nonexistent", "status": "confirmed",
                       "confirmed_by": ""}).encode("utf-8")
    for peer in ("127.0.0.1", "10.9.8.7", "172.17.0.1", ops.PROXY_PEER_IPS[0]):
        headers = {"X-Test-Tcp-Peer": peer, "X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}
        for path in private_reads:
            code, _, body = req(path, headers)
            check("%s 自报双头私人读 %s 拒绝" % (peer, path), code == 403 and body.get("ok") is False)
        code, _, body = req("/api/legion/tri-party/confirm",
                            {**headers, "Content-Type": "application/json"}, data)
        check("%s 自报双头写拒绝" % peer, code == 403 and body.get("ok") is False)

    # 3. 无标记的 nginx peer 仍需 token；正确 token 通过，错误 token 拒绝。
    peer = ops.PROXY_PEER_IPS[0]
    code, _, body = req(private_reads[0], {"X-Test-Tcp-Peer": peer})
    check("nginx peer 缺标记无 token 拒绝", code == 403)
    code, _, body = req(private_reads[0], {"X-Test-Tcp-Peer": peer, "X-Portal-Proxy": "1",
                                          "X-Real-IP": "127.0.0.1", "X-Portal-Token": TOKEN})
    check("反代本机浏览器持 token 放行", code == 200 and body.get("ok") is True)
    code, _, body = req(private_reads[0], {"X-Test-Tcp-Peer": "172.17.0.1",
                                          "X-Portal-Token": TOKEN})
    check("bridge 网关持 token 放行", code == 200 and body.get("ok") is True)
    code, _, body = req(private_reads[0], {"X-Test-Tcp-Peer": peer,
                                          "X-Portal-Token": "wrong"})
    check("反代错误 token 拒绝", code == 403)
    code, _, body = req("/api/legion/tri-party/confirm",
                        {"X-Test-Tcp-Peer": peer, "X-Portal-Token": TOKEN,
                         "Content-Type": "application/json"}, data)
    check("反代写持 token 进入业务校验", code == 200 and body.get("ok") is False
          and "confirmed_by" in body.get("err", ""))

    # 4. 环回确认端点：空署名被拒（未触达会议账本）
    code, _, body = req("/api/legion/tri-party/confirm",
                        {"Content-Type": "application/json"}, data)
    check("空署名确认被拒", code == 200 and body.get("ok") is False
          and "confirmed_by" in body.get("err", ""))

    # 8. CORS：白名单回显、陌生来源与无 Origin 均不下发
    code, h, _ = req("/api/legion/tri-party/meetings", {"Origin": "http://localhost:8080"})
    check("白名单来源回显 CORS", h.get("Access-Control-Allow-Origin") == "http://localhost:8080")
    code, h, _ = req("/api/legion/tri-party/meetings", {"Origin": "http://evil.example"})
    check("陌生来源无 CORS 头", "Access-Control-Allow-Origin" not in h)
    code, h, _ = req("/api/legion/tri-party/meetings")
    check("无 Origin 无 CORS 头", "Access-Control-Allow-Origin" not in h)

    srv.shutdown()
    print("PASS %d live checks on temporary loopback port (prod 8096 untouched)" % len(PASS))


def test_legion_live_boot_probe_is_manual():
    """live 探针需真实环回端口，不进 pytest 自动化。

    历史缺陷（2026-11-07 审计）：原文件在模块级直接起服务并断言，pytest
    收集阶段 import 即执行 → 整个 portal 套件收集中断（1 error，其余
    11154 项一条都不跑）；且模块级 `ops._load_token = lambda: TOKEN` 会
    污染同进程其他 legion 测试的鉴权口径。
    """
    pytest.skip("手工运行：python portal/test_legion_live_boot_p0.py")


if __name__ == "__main__":
    main()
