# -*- coding: utf-8 -*-
"""Phase A auth-gateway 端到端 smoke（只读自检；不打印密码/token/sid）。

前置：8091 已加载 auth_gateway；vault 内有 zero_bff 服务账号凭据。
用法：python tools/auth_gateway_smoke.py
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CRED = Path(r"C:\项目\_vault\secrets\zhice\bff_service.env")


def _creds():
    vals = {}
    for line in CRED.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    return vals.get("ZHICE_SERVICE_USERNAME", ""), vals.get("ZHICE_SERVICE_PASSWORD", "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8091")
    args = ap.parse_args(argv)
    username, password = _creds()
    if not username or not password:
        print("SKIP: vault credentials missing")
        return 2

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(path, payload=None, headers=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(args.base + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, {"raw": body[:120]}

    status, body = call("/api/auth/login", {"username": username, "password": password})
    if status == 404:
        print("SKIP: auth_gateway_not_live (restart 8091)")
        return 2
    print("login", status, "sub", body.get("sub") if isinstance(body, dict) else "")
    if status != 200:
        return 1

    status, me = call("/api/bff/identity/me")
    print("me", status, "sub", (me.get("session") or {}).get("sub") if isinstance(me, dict) else "")
    if status != 200:
        return 1
    csrf = me.get("csrf_token") or ""

    status, out = call("/api/auth/logout", {}, headers={"X-CSRF-Token": csrf})
    print("logout", status, "ok", out.get("ok") if isinstance(out, dict) else "")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
