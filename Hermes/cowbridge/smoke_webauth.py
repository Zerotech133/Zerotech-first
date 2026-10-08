# -*- coding: utf-8 -*-
"""CowAgent web console 认证冒烟（审计 P1-3①，2026-10-08）。

验证：
  1. /api/health        无凭据 → 200（设计上免认证，pages.py L35）
  2. /message           无凭据 → 401（web_password 已设）
  3. /message           带本地构造 Bearer token → 非 401（HMAC 与服务端同钥）
  4. /message           带错误 token → 401（签名校验有效）

token 形态（_common.py::_verify_auth_token 源码对齐）：
  hex(ts) + "." + hmac_sha256(key=web_password, msg=hex(ts))，30 天有效。

凭据纪律（2026-10-08 复审整改）：口令运行时从 CowAgent/config.json 读取，
不硬编码入脚本（旧口令曾硬编码于此并随 63ab598 入 git 历史，已轮换作废）。
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request as rq
from pathlib import Path

BASE = "http://127.0.0.1:9899"
_COW_CONFIG = Path(r"E:\老田的AI助手\CowAgent\config.json")
opener = rq.build_opener(rq.ProxyHandler({}))   # 直连绕系统代理


def _load_password() -> str:
    cfg = json.loads(_COW_CONFIG.read_text(encoding="utf-8"))
    pwd = str(cfg.get("web_password") or "")
    if not pwd:
        raise SystemExit("config.json web_password 为空——服务当前免认证，冒烟前提不成立")
    return pwd


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
    pwd = _load_password()
    results = []

    results.append(("1 health 免认证=200", get_health(), lambda r: r[0] == 200))
    results.append(("2 /message 无凭据=401", post_message(None),
                    lambda r: r[0] == 401))
    results.append(("3 /message 正确 token≠401", post_message(make_token(pwd)),
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
