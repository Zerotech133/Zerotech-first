# -*- coding: utf-8 -*-
"""CowAgent web console 认证冒烟（审计 P1-3①，2026-10-08）。

验证：
  1. /api/health        无凭据 → 200（设计上免认证，pages.py L35）
  2. /message           无凭据 → 401（web_password 已设）
  3. /message           带本地构造 Bearer token → 非 401（HMAC 与服务端同钥）
  4. /message           带错误 token → 401（签名校验有效）

token 形态（_common.py::_verify_auth_token 源码对齐）：
  hex(ts) + "." + hmac_sha256(key=web_password, msg=hex(ts))，30 天有效。
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request as rq

BASE = "http://127.0.0.1:9899"
PWD = "cow-06df5d04f5d0"          # 与 CowAgent/config.json web_password 一致
opener = rq.build_opener(rq.ProxyHandler({}))   # 直连绕系统代理


def make_token(pwd: str, ts: int | None = None) -> str:
    ts_hex = format(ts if ts is not None else int(time.time()), "x")
    sig = hmac.new(pwd.encode(), ts_hex.encode(), hashlib.sha256).hexdigest()
    return f"{ts_hex}.{sig}"


def post_message(token: str | None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"session_id": "smoke_audit_p13",
                       "message": "认证冒烟：只回一个字「好」",
                       "stream": False}).encode()
    req = rq.Request(f"{BASE}/message", data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=90) as r:
            return r.status, r.read()[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200].decode("utf-8", "replace")


def get_health():
    try:
        with opener.open(f"{BASE}/api/health", timeout=10) as r:
            return r.status, r.read()[:120].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:120].decode("utf-8", "replace")


def main() -> int:
    results = []

    results.append(("1 health 免认证=200", get_health(), lambda r: r[0] == 200))
    results.append(("2 /message 无凭据=401", post_message(None),
                    lambda r: r[0] == 401))
    results.append(("3 /message 正确 token≠401", post_message(make_token(PWD)),
                    lambda r: r[0] != 401))
    results.append(("4 /message 错误 token=401",
                    post_message(make_token("wrong-password")),
                    lambda r: r[0] == 401))

    fail = 0
    for name, (code, snippet), ok in results:
        passed = ok((code, snippet))
        fail += 0 if passed else 1
        print(f"[{'PASS' if passed else 'FAIL'}] {name} -> HTTP {code} {snippet[:80]!r}")
    print(f"== smoke_webauth PASS {len(results) - fail} / FAIL {fail} ==")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
