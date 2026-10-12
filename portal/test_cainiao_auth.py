# -*- coding: utf-8 -*-
"""cainiao_auth.py 单元测试：不依赖外部网络（mock urllib / 本地 http.server）。

覆盖：缓存命中、token 无效、网络超时降级、机器通道优先、回环放行、
端到端 Bearer 校验（本地临时端点）。
用法: python test_cainiao_auth.py
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cainiao_auth  # noqa: E402


# ===== 测试用假 check-token 后端 =====

def _fake_http(ok=True, calls=None):
    """替换 cainiao_auth._http_check_token，返回 (可调用, 计数器容器)。"""
    counter = {"n": 0}

    def fake(token, cfg):
        counter["n"] += 1
        if calls is not None:
            calls.append((token, cfg))
        if ok:
            return {"code": 0, "data": {"userId": 42, "expiresTime": 1893456000000}}
        return {"code": 400, "msg": "无效的令牌"}
    return fake, counter


class _StubHandler(BaseHTTPRequestHandler):
    """本地临时 check-token 端点：按 path 决定返回，验证 urllib 直连真实链路。"""
    behavior = {"ok": True}  # 类变量，测试里可改
    seen_headers = {}        # 最近一次请求头快照（tenant-id/Authorization 断言用）

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        assert "token=" in body
        _StubHandler.seen_headers = dict(self.headers.items())
        if _StubHandler.behavior["ok"]:
            payload = {"code": 0, "data": {"userId": 7, "expiresTime": 1893456000000}}
            code = 200
        else:
            payload = {"code": 400, "msg": "无效的令牌"}
            code = 400
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # 静默
        pass


class CainiaoAuthTest(unittest.TestCase):
    def setUp(self):
        cainiao_auth._cache.clear()

    # --- 1. 缓存命中：TTL 内第二次调用不打远端 ---
    def test_cache_hit(self):
        fake, counter = _fake_http(ok=True)
        with mock.patch.object(cainiao_auth, "_http_check_token", fake):
            r1 = cainiao_auth.verify_bearer("tok-a")
            r2 = cainiao_auth.verify_bearer("tok-a")
        self.assertTrue(r1.ok and r2.ok)
        self.assertEqual(counter["n"], 1)
        self.assertEqual(r2.user_id, "42")
        self.assertAlmostEqual(r2.expiry, 1893456000.0)

    # --- 2. token 无效：权威拒绝结论也缓存 ---
    def test_invalid_token(self):
        fake, counter = _fake_http(ok=False)
        with mock.patch.object(cainiao_auth, "_http_check_token", fake):
            r = cainiao_auth.verify_bearer("bad")
            r2 = cainiao_auth.verify_bearer("bad")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "invalid_token")
        self.assertEqual(counter["n"], 1)  # 无效结论同样进缓存
        self.assertFalse(r2.ok)

    # --- 3. 网络超时：degraded 放行 / enabled 拒绝 ---
    def test_timeout_degraded_vs_enabled(self):
        """K-STOCK-187 fail-closed 收窄：不可达时 degraded/enabled 均拒绝
        （能到 bearer 分支的必然是非回环来源，降级放行=鉴权洞）。"""
        def boom(token, cfg):
            raise OSError("timed out")
        with mock.patch.object(cainiao_auth, "_http_check_token", boom):
            with mock.patch.dict(os.environ, {"CAINIAO_AUTH_MODE": "degraded"}):
                r = cainiao_auth.verify_bearer("tok-net")
            self.assertFalse(r.ok)
            self.assertTrue(r.reason.startswith("auth_unreachable_rejected"))
            with mock.patch.dict(os.environ, {"CAINIAO_AUTH_MODE": "enabled"}):
                r2 = cainiao_auth.verify_bearer("tok-net")
            self.assertFalse(r2.ok)
            self.assertTrue(r2.reason.startswith("auth_unreachable_rejected"))

    # --- 4. 机器通道优先：X-Portal-Token 命中即放行，不走 Bearer ---
    def test_machine_channel_priority(self):
        def _explode(token):
            raise AssertionError("verify_bearer 不应被调用")
        headers = {"X-Portal-Token": "secret123",
                   "Authorization": "Bearer whatever"}
        with mock.patch.object(cainiao_auth, "verify_bearer", _explode):
            r = cainiao_auth.verify_request(headers, "192.168.1.50",
                                            portal_token="secret123")
        self.assertTrue(r.ok)
        self.assertEqual(r.reason, "machine_channel")
        # token 不匹配则不认机器通道
        r2 = cainiao_auth.verify_request({"X-Portal-Token": "wrong"},
                                         "192.168.1.50", portal_token="secret123")
        self.assertFalse(r2.ok)

    # --- 5. 回环地址放行：无任何凭据也过（产线保底）---
    def test_loopback_pass(self):
        for addr in ("127.0.0.1", "::1"):
            r = cainiao_auth.verify_request({}, addr, portal_token="secret123")
            self.assertTrue(r.ok, addr)
            self.assertEqual(r.reason, "local_trusted")
        # 非回环且无凭据 → 拒绝
        r = cainiao_auth.verify_request({}, "192.168.1.50")
        self.assertFalse(r.ok)
        self.assertEqual(r.reason, "no_credentials")

    # --- 6. 端到端：本地 http.server 起临时端点，验证 urllib 直连真实链路 ---
    def test_bearer_end_to_end_local_server(self):
        _StubHandler.behavior["ok"] = True
        srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env = {"CAINIAO_BASE_URL": "http://127.0.0.1:%d" % port,
                   "CAINIAO_CHECK_TOKEN_PATH": "/admin-api/system/oauth2/check-token",
                   "CAINIAO_TIMEOUT": "2"}
            with mock.patch.dict(os.environ, env):
                r = cainiao_auth.verify_bearer("real-flow-token")
                self.assertTrue(r.ok)
                self.assertEqual(r.user_id, "7")
                # 走 verify_request 组合入口（Bearer 路径）
                r2 = cainiao_auth.verify_request(
                    {"Authorization": "Bearer real-flow-token"}, "10.1.2.3")
                self.assertTrue(r2.ok)
                self.assertEqual(r2.reason, "cainiao_ok")
            # 端到端无效 token（服务端返回 400 HTTPError 路径）
            _StubHandler.behavior["ok"] = False
            cainiao_auth._cache.clear()
            with mock.patch.dict(os.environ, env):
                r3 = cainiao_auth.verify_bearer("nope")
            self.assertFalse(r3.ok)
            self.assertEqual(r3.reason, "invalid_token")
        finally:
            srv.shutdown()
            srv.server_close()

    # --- 7. 杂项：空 token / 空 Bearer ---
    def test_missing_token(self):
        self.assertFalse(cainiao_auth.verify_bearer("").ok)
        self.assertFalse(cainiao_auth.verify_bearer(None).ok)
        r = cainiao_auth.verify_request({"Authorization": "Bearer   "}, "10.0.0.1")
        self.assertFalse(r.ok)

    # --- 8. tenant-id / Basic 凭据头随请求上送（2026-10-09 真端点实证：
    #        缺 tenant-id 头被芋道租户过滤器拦截 code=400，到不了 OAuth2）---
    def test_tenant_and_basic_headers_sent(self):
        _StubHandler.behavior["ok"] = True
        srv = HTTPServer(("127.0.0.1", 0), _StubHandler)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env = {"CAINIAO_BASE_URL": "http://127.0.0.1:%d" % port,
                   "CAINIAO_TENANT_ID": "162",
                   "CAINIAO_CLIENT_ID": "default",
                   "CAINIAO_CLIENT_SECRET": "sec-test"}
            with mock.patch.dict(os.environ, env):
                cainiao_auth.verify_bearer("hdr-check-token")
            seen = _StubHandler.seen_headers
            # urllib add_header 会 capitalize 键（tenant-id→Tenant-id），
            # 服务端头名不区分大小写；断言同样大小写不敏感。
            seen_lower = {k.lower(): v for k, v in seen.items()}
            self.assertEqual(seen_lower.get("tenant-id"), "162")
            self.assertTrue(seen_lower.get("authorization", "").startswith("Basic "))
            # 租户缺省值=1：环境未设时也必须带头（否则真端点 400）
            _StubHandler.seen_headers = {}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("CAINIAO_TENANT_ID")
                cainiao_auth._cache.clear()
                cainiao_auth.verify_bearer("hdr-check-token-2")
            seen_lower2 = {k.lower(): v for k, v in _StubHandler.seen_headers.items()}
            self.assertEqual(seen_lower2.get("tenant-id"), "1")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
