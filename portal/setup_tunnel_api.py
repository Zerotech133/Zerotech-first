# -*- coding: utf-8 -*-
"""Cloudflare API Token 模式：建命名隧道 → 绑 DNS → 起 cloudflared → 改链接基地址。

用法（老田选 A：API Token 模式）：
  python setup_tunnel_api.py --token <CF_API_TOKEN> --domain m.example.com --dry-run
  python setup_tunnel_api.py --token <CF_API_TOKEN> --domain m.example.com

Token 权限（Custom Token）：
  Account → Cloudflare Tunnel → Edit
  Zone(<你的域名>) → DNS → Edit

流程：
  1) verify token（/user/tokens/verify）
  2) 找 account 与 zone（domain 后缀匹配）
  3) 建/复用命名隧道（name 默认 lingjie-mobile），取 tunnel token
  4) 写 ingress 配置 hostname → http://127.0.0.1:8081
  5) 建 CNAME <domain> → <tunnel_id>.cfargotunnel.com（proxied）
  6) 后台起 cloudflared tunnel run --token
  7) 验证 https://<domain>/api/m/health 并更新 m_link.json / urls.json
  --dry-run 只打印计划，不写任何资源。

安全：token 只用于本次调用，不落盘；tunnel token 传给 cloudflared 后不打印全文。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


API = "https://api.cloudflare.com/client/v4"
HERE = os.path.dirname(os.path.abspath(__file__))
CF_EXE = os.path.join(os.path.dirname(HERE), "bin", "cloudflared.exe")
CF_LOG = r"C:\Temp\m_named_tunnel.log"
M_LINK = os.path.join(HERE, "work", "m_link.json")
URLS = os.path.join(HERE, "data", "m", "urls.json")


def _req(method, path, token, body=None):
    data = None
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with safe_urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {"success": False, "errors": [{"message": str(e)}]}


def _fail(step, status, resp):
    errs = (resp or {}).get("errors") or []
    msg = "; ".join(str(x.get("message") if isinstance(x, dict) else x) for x in errs)[
        :300
    ]
    print("FAIL [%s] http=%s %s" % (step, status, msg))
    sys.exit(2)


def verify(token):
    st, r = _req("GET", "/user/tokens/verify", token)
    if st != 200 or not (r or {}).get("success"):
        _fail("verify", st, r)
    print("OK token verify:", (r.get("result") or {}).get("status"))


def pick_account(token, account_id=""):
    st, r = _req("GET", "/accounts", token)
    if st != 200 or not (r or {}).get("success"):
        _fail("accounts", st, r)
    accounts = r.get("result") or []
    if not accounts:
        print("FAIL 该 token 下没有 account")
        sys.exit(2)
    if account_id:
        for a in accounts:
            if a.get("id") == account_id:
                return a
        print("FAIL 指定 account 不在此 token 范围内")
        sys.exit(2)
    return accounts[0]


def pick_zone(token, domain):
    st, r = _req("GET", "/zones?per_page=50", token)
    if st != 200 or not (r or {}).get("success"):
        _fail("zones", st, r)
    zones = [z for z in (r.get("result") or []) if z.get("name")]
    zones.sort(key=lambda z: -len(z["name"]))
    for z in zones:
        if domain == z["name"] or domain.endswith("." + z["name"]):
            return z
    print(
        "FAIL 域名 %s 不在该 token 可见的 Cloudflare zone 中（需先把域名托管到 Cloudflare）"
        % domain
    )
    sys.exit(2)


def ensure_tunnel(token, account_id, name):
    st, r = _req(
        "GET",
        "/accounts/%s/cfd_tunnel?is_deleted=false&per_page=50" % account_id,
        token,
    )
    if st != 200 or not (r or {}).get("success"):
        _fail("list tunnel", st, r)
    for t in r.get("result") or []:
        if t.get("name") == name and not t.get("deleted_at"):
            print("OK reuse tunnel:", t.get("id"), name)
            return t
    st, r = _req(
        "POST",
        "/accounts/%s/cfd_tunnel" % account_id,
        token,
        {"name": name, "config_src": "local"},
    )
    if st not in (200, 201) or not (r or {}).get("success"):
        _fail("create tunnel", st, r)
    t = r.get("result") or {}
    print("OK created tunnel:", t.get("id"), name)
    return t


def tunnel_token(token, account_id, tunnel_id):
    st, r = _req(
        "GET", "/accounts/%s/cfd_tunnel/%s/token" % (account_id, tunnel_id), token
    )
    if st != 200 or not (r or {}).get("result"):
        _fail("tunnel token", st, r)
    tk = r.get("result")
    if isinstance(tk, dict):
        tk = tk.get("token") or tk.get("value")
    if not tk:
        print("FAIL tunnel token 为空")
        sys.exit(2)
    return str(tk)


def put_ingress(token, account_id, tunnel_id, hostname):
    cfg = {
        "config": {
            "ingress": [
                {"hostname": hostname, "service": "http://127.0.0.1:8081"},
                {"service": "http_status:404"},
            ]
        }
    }
    st, r = _req(
        "PUT",
        "/accounts/%s/cfd_tunnel/%s/configurations" % (account_id, tunnel_id),
        token,
        cfg,
    )
    if st != 200 or not (r or {}).get("success"):
        _fail("ingress config", st, r)
    print("OK ingress ->", hostname, "http://127.0.0.1:8081")


def upsert_dns(token, zone_id, hostname, tunnel_id):
    target = tunnel_id + ".cfargotunnel.com"
    st, r = _req(
        "GET", "/zones/%s/dns_records?name=%s&type=CNAME" % (zone_id, hostname), token
    )
    if st != 200 or not (r or {}).get("success"):
        _fail("list dns", st, r)
    existing = r.get("result") or []
    if existing:
        rid = existing[0].get("id")
        st2, r2 = _req(
            "PUT",
            "/zones/%s/dns_records/%s" % (zone_id, rid),
            token,
            {"type": "CNAME", "name": hostname, "content": target, "proxied": True},
        )
        if st2 != 200 or not (r2 or {}).get("success"):
            _fail("update dns", st2, r2)
        print("OK updated DNS CNAME", hostname, "->", target)
        return
    st2, r2 = _req(
        "POST",
        "/zones/%s/dns_records" % zone_id,
        token,
        {"type": "CNAME", "name": hostname, "content": target, "proxied": True},
    )
    if st2 not in (200, 201) or not (r2 or {}).get("success"):
        _fail("create dns", st2, r2)
    print("OK created DNS CNAME", hostname, "->", target)


def start_cloudflared(tk):
    os.makedirs(os.path.dirname(CF_LOG), exist_ok=True)
    # 停旧进程（避免多实例）
    subprocess.run(["taskkill", "/IM", "cloudflared.exe", "/F"], capture_output=True)
    time.sleep(1)
    args = [CF_EXE, "tunnel", "--no-autoupdate", "run", "--token", tk]
    logf = open(CF_LOG, "w", encoding="utf-8")
    errf = open(CF_LOG + ".err", "w", encoding="utf-8")
    subprocess.Popen(
        args,
        stdout=logf,
        stderr=errf,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    print("OK cloudflared started (log:", CF_LOG + ")")


def wait_health(hostname, tries=20):
    url = "https://" + hostname + "/api/m/health"
    for i in range(tries):
        time.sleep(3)
        try:
            with safe_urlopen(url, timeout=15) as r:
                if r.status == 200:
                    print("OK health 200:", url)
                    return True
        except Exception as e:  # noqa: BLE001
            last = str(e)[:100]
            if i % 3 == 2:
                print("  waiting health...", last)
    print("FAIL health not ready:", url)
    return False


def update_config(hostname):
    base = "https://" + hostname
    d = json.load(open(M_LINK, encoding="utf-8"))
    d["enabled"] = True
    d["base"] = base
    d["note"] = "named tunnel (API token mode) %s" % base
    json.dump(d, open(M_LINK, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    u = json.load(open(URLS, encoding="utf-8"))
    u["default_base"] = base
    json.dump(u, open(URLS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("OK updated", M_LINK, "and", URLS)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--domain", required=True, help="完整主机名，如 m.example.com")
    ap.add_argument("--name", default="lingjie-mobile")
    ap.add_argument("--account-id", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-run", action="store_true")
    args = ap.parse_args(argv)
    domain = args.domain.strip().lower()
    verify(args.token)
    acct = pick_account(args.token, args.account_id)
    zone = pick_zone(args.token, domain)
    print(
        "OK account:",
        acct.get("id"),
        acct.get("name"),
        "| zone:",
        zone.get("id"),
        zone.get("name"),
    )
    t = ensure_tunnel(args.token, acct.get("id"), args.name)
    tid = t.get("id")
    if args.dry_run:
        print(
            "DRY_RUN plan: tunnel=%s ingress=%s->127.0.0.1:8081 "
            "dns=CNAME %s -> %s.cfargotunnel.com" % (tid, domain, domain, tid)
        )
        return 0
    put_ingress(args.token, acct.get("id"), tid, domain)
    upsert_dns(args.token, zone.get("id"), domain, tid)
    tk = tunnel_token(args.token, acct.get("id"), tid)
    if not args.skip_run:
        start_cloudflared(tk)
        if not wait_health(domain):
            print("提示：检查 C:\\Temp\\m_named_tunnel.log 与 DNS 生效时间")
            return 3
    update_config(domain)
    print("DONE base=https://%s" % domain)
    return 0


if __name__ == "__main__":
    sys.exit(main())
