#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 单栈验收（ADR-016）v1。

只读校验 7 组：
  1. 端口：8734/8091/8105 监听 127.0.0.1；5175/8102 无监听
  2. 5175 引用：portal py/html/static js = 0
  3. nginx：/quant/ 与 /quant-api/ ^~ location 存在；/probe/zhice 指 8105
  4. HTTP 三态：/quant/ 302/200；/quant-api/api/v1/health 401/200；/probe/zhice 200；/api/auth/guard 401
  5. OIDC：status mode=on ready=true
  6. 8091 代码生效（换血后）：/health-portal 含 智策API(8105)、无 5175；未换血= PENDING
  7. 8102 默认引用：auth_gateway.py / api_zhice_bff.py 中 0 处

退出码：0 全 PASS；1 存在 FAIL；2 参数/环境错误。--allow-pending 时 PENDING 不导致失败。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8734"


def ps_listen(port: int) -> list[str]:
    cmd = (
        f"$c=Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue;"
        "if($c){$c|Select-Object -ExpandProperty LocalAddress -Unique}else{'OFF'}"
    )
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        return [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
    except Exception:  # noqa: BLE001
        return ["ERR"]


def http(url: str, timeout: float = 6.0):
    try:
        r = urllib.request.urlopen(url, timeout=timeout)
        return r.status, r.read(300000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(300000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def read_text(rel: str) -> str:
    p = ROOT / rel
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="L1 单栈验收（ADR-016）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-pending", action="store_true")
    args = ap.parse_args(argv)

    rows: list[dict] = []

    def add(group: str, name: str, status: str, detail: str = "") -> None:
        rows.append({"group": group, "name": name, "status": status, "detail": detail})

    # 1) 端口
    for port in (8734, 8091, 8105):
        addrs = ps_listen(port)
        ok = any(a.startswith("127.0.0.1") for a in addrs)
        add("port", f"{port} 监听 127.0.0.1", "PASS" if ok else "FAIL", ",".join(addrs))
    for port in (5175, 8102):
        addrs = ps_listen(port)
        off = addrs == ["OFF"]
        add("port", f"{port} 已关闭", "PASS" if off else "FAIL", ",".join(addrs))

    # 2) 5175 引用
    refs = 0
    for pat in ("portal/*.py", "portal/*.html", "portal/static/*.js"):
        for f in ROOT.glob(pat):
            try:
                refs += f.read_text(encoding="utf-8", errors="replace").count("5175")
            except Exception:  # noqa: BLE001
                pass
    add("refs", "5175 代码/页面/nav 引用为 0", "PASS" if refs == 0 else "FAIL", f"count={refs}")

    # 3) nginx
    ng = read_text("portal/nginx.conf")
    add("nginx", "location ^~ /quant/ 存在", "PASS" if "location ^~ /quant/" in ng else "FAIL")
    add("nginx", "location ^~ /quant-api/ 存在", "PASS" if "location ^~ /quant-api/" in ng else "FAIL")
    add("nginx", "/probe/zhice 指 8105", "PASS" if "proxy_pass http://$h:8105/api/v1/health;" in ng else "FAIL")
    add("nginx", "无 /probe/zhice 指 8102", "PASS" if "proxy_pass http://$h:8102/api/v1/health;" not in ng else "FAIL")

    # 4) HTTP 三态
    checks = [
        ("/quant/", (200, 302), "PASS"),
        ("/quant-api/api/v1/health", (200, 401), "PASS"),
        ("/probe/zhice", (200,), "PASS"),
        ("/api/auth/guard", (401,), "PASS"),
    ]
    for path, allowed, _ in checks:
        code, _body = http(BASE + path)
        add("http", f"{path} in {allowed}", "PASS" if code in allowed else "FAIL", f"code={code}")

    # 5) OIDC
    code, body = http(BASE + "/api/auth/oidc/status")
    try:
        st = json.loads(body) if body.strip().startswith("{") else {}
    except Exception:  # noqa: BLE001
        st = {}
    ok = code == 200 and st.get("mode") == "on" and st.get("ready") is True
    add("oidc", "mode=on ready=true", "PASS" if ok else "FAIL",
        f"code={code} mode={st.get('mode')} ready={st.get('ready')}")

    # 6) 8091 代码生效（换血后）
    hp_code, hp_body = http(BASE + "/health-portal")
    if hp_code != 200:
        hp_code, hp_body = http(BASE + "/api/health-portal")
    live = "智策API(8105)" in hp_body and "5175" not in hp_body
    if live:
        add("8091", "新代码生效（8105/无5175）", "PASS", f"code={hp_code}")
    else:
        add("8091", "新代码生效（8105/无5175）", "PENDING", f"需 8091 换血；code={hp_code}")

    # 7) 8102 默认引用
    for rel in ("portal/auth_gateway.py", "portal/api_zhice_bff.py"):
        s = read_text(rel)
        n = s.count("127.0.0.1:8102")
        add("code", f"{rel} 无 127.0.0.1:8102", "PASS" if n == 0 else "FAIL", f"count={n}")

    # 汇总
    fails = [r for r in rows if r["status"] == "FAIL"]
    pend = [r for r in rows if r["status"] == "PENDING"]
    if args.json:
        print(json.dumps({"rows": rows, "fail": len(fails), "pending": len(pend)}, ensure_ascii=False, indent=1))
    else:
        print("== L1 单栈验收（ADR-016）==")
        for r in rows:
            mark = r["status"]
            print(f"[{mark:<7}] {r['group']:<6} {r['name']}  {r['detail']}")
        print(f"汇总：{len(rows)} 项；FAIL={len(fails)}；PENDING={len(pend)}")
    if fails:
        return 1
    if pend and not args.allow_pending:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
