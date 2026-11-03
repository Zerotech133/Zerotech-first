# -*- coding: utf-8 -*-
"""MCP-8787 只读验证探针（task-41 / WO-MCP-8787-VERIFY-20261104）。

只读：仅 GET http://127.0.0.1:8787 下已登记的 WebUI HTTP API；
不写配置、不启停任何服务、不触发 /reload-mcp、不打印 token/cookie 原文。
登录态使用本机既有 SSO 断言头（X-Portal-User，nginx auth_request 通过后注入，
见 Hermes/start_hermes_bg.py L175-196 与 config/auth_surfaces.json），
以及由它签发的 hermes_session 服务端会话 cookie（只记录脱敏指纹）。

用法：
  python probe_mcp8787.py --out-dir "C:\\...\\docs\\ops\\evidence"
"""
import argparse
import hashlib
import http.client
import json
import time
from datetime import datetime

HOST = "127.0.0.1"
PORT = 8787
TRUSTED_HEADER = "X-Portal-User"
TRUSTED_USER = "laotian"
COOKIE_NAME = "hermes_session"


def http_get(path, headers=None):
    t0 = time.perf_counter()
    conn = http.client.HTTPConnection(HOST, PORT, timeout=20)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
        hdrs = {}
        for k, v in resp.getheaders():
            hdrs.setdefault(k.lower(), []).append(v)
    finally:
        conn.close()
    return {
        "path": path,
        "status": status,
        "ms": round((time.perf_counter() - t0) * 1000, 1),
        "headers": hdrs,
        "body": body,
    }


def parse_json(row):
    try:
        return json.loads(row["body"].decode("utf-8"))
    except Exception:
        return None


def mask_set_cookies(row):
    """脱敏记录 Set-Cookie：只留 name / 长度 / sha256 前 12 位 / 属性。"""
    out = []
    for sc in row["headers"].get("set-cookie") or []:
        name, _, rest = sc.partition("=")
        val = rest.split(";", 1)[0]
        attrs = [a.strip() for a in rest.split(";")[1:] if a.strip()]
        out.append(
            {
                "name": name.strip(),
                "value_len": len(val),
                "value_sha256_12": hashlib.sha256(val.encode("utf-8")).hexdigest()[:12],
                "attrs": attrs,
            }
        )
    return out


def redact_servers(payload):
    """/api/mcp/servers 白名单字段（丢弃 command/args/url/env，避免暴露本机路径/凭据）。"""
    keep = ("name", "transport", "enabled", "active", "status", "tool_count")
    out = []
    for s in (payload or {}).get("servers") or []:
        out.append({k: s.get(k) for k in keep})
    return out


def redact_tools(payload, desc_limit=160):
    out = []
    for t in (payload or {}).get("tools") or []:
        desc = str(t.get("description") or "")
        if len(desc) > desc_limit:
            desc = desc[: desc_limit - 1].rstrip() + "…"
        out.append(
            {
                "name": t.get("name"),
                "server": t.get("server"),
                "active": t.get("active"),
                "enabled": t.get("enabled"),
                "status": t.get("status"),
                "description": desc,
            }
        )
    return out


def auth_header(value=TRUSTED_USER):
    return {TRUSTED_HEADER: value}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    evidence = {
        "tool": "mcp8787_probe",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "endpoint": "http://%s:%d" % (HOST, PORT),
        "read_only": True,
        "trusted_header_name": TRUSTED_HEADER,
        "trusted_header_user": TRUSTED_USER,
        "cookie_name": COOKIE_NAME,
        "probes": {},
    }
    probes = evidence["probes"]

    # ── A. 未登录（无 trusted header / 无 session cookie）──
    r_health = http_get("/health")
    r_auth_anon = http_get("/api/auth/status")
    r_tools_anon = http_get("/api/mcp/tools")
    r_servers_anon = http_get("/api/mcp/servers")
    r_mcp_path_anon = http_get("/mcp")

    # ── B. 未登录 + 错误头/空值（负例：证明只认指定 trusted header）──
    r_tools_wrong_header = http_get("/api/mcp/tools", {"X-Forwarded-User": TRUSTED_USER})
    r_tools_empty_header = http_get("/api/mcp/tools", auth_header(""))

    # ── C. 登录后：trusted header（nginx SSO 身份断言）──
    r_auth_login = http_get("/api/auth/status", auth_header())
    r_tools_login = http_get("/api/mcp/tools", auth_header())
    r_servers_login = http_get("/api/mcp/servers", auth_header())
    r_mcp_path_login = http_get("/mcp", auth_header())

    cookies = mask_set_cookies(r_tools_login)
    session_cookie = None
    for sc in r_tools_login["headers"].get("set-cookie") or []:
        name, _, rest = sc.partition("=")
        if name.strip() == COOKIE_NAME:
            session_cookie = rest.split(";", 1)[0]

    # ── D. 登录后（无 trusted header，仅 hermes_session cookie）──
    r_tools_cookie = (
        http_get("/api/mcp/tools", {"Cookie": "%s=%s" % (COOKIE_NAME, session_cookie)})
        if session_cookie
        else None
    )

    tools_login = parse_json(r_tools_login)
    servers_login = parse_json(r_servers_login)
    auth_login = parse_json(r_auth_login)
    auth_anon = parse_json(r_auth_anon)
    tools_redacted = redact_tools(tools_login)
    servers_redacted = redact_servers(servers_login)

    by_server = {}
    for t in tools_redacted:
        by_server.setdefault(str(t.get("server") or ""), []).append(t["name"])
    server_tool_sum = 0
    for s in servers_redacted:
        tc = s.get("tool_count")
        if isinstance(tc, int):
            server_tool_sum += tc
        elif isinstance(tc, (list, dict)):
            server_tool_sum += len(tc)

    probes["health_anon"] = {"status": r_health["status"], "ms": r_health["ms"],
                             "body": parse_json(r_health) or r_health["body"][:300].decode("utf-8", "replace")}
    probes["auth_status_anon"] = {"status": r_auth_anon["status"], "ms": r_auth_anon["ms"], "body": auth_anon}
    probes["mcp_tools_anon"] = {"status": r_tools_anon["status"], "ms": r_tools_anon["ms"],
                                "body": parse_json(r_tools_anon),
                                "content_type": (r_tools_anon["headers"].get("content-type") or [""])[0]}
    probes["mcp_servers_anon"] = {"status": r_servers_anon["status"], "ms": r_servers_anon["ms"],
                                  "body": parse_json(r_servers_anon)}
    probes["mcp_path_anon"] = {"status": r_mcp_path_anon["status"], "ms": r_mcp_path_anon["ms"],
                               "location": (r_mcp_path_anon["headers"].get("location") or [""])[0]}
    probes["mcp_tools_wrong_header_X-Forwarded-User"] = {"status": r_tools_wrong_header["status"]}
    probes["mcp_tools_empty_trusted_header"] = {"status": r_tools_empty_header["status"]}
    probes["auth_status_trusted_header"] = {"status": r_auth_login["status"], "ms": r_auth_login["ms"], "body": auth_login}
    probes["mcp_tools_trusted_header"] = {
        "status": r_tools_login["status"],
        "ms": r_tools_login["ms"],
        "set_cookies_masked": cookies,
        "total": (tools_login or {}).get("total"),
        "source": (tools_login or {}).get("source"),
        "inventory_scope": (tools_login or {}).get("inventory_scope"),
        "runtime_scope": (tools_login or {}).get("runtime_scope"),
        "unavailable_servers": (tools_login or {}).get("unavailable_servers"),
        "tool_count_by_server": {k: len(v) for k, v in sorted(by_server.items())},
        "tools": tools_redacted,
    }
    probes["mcp_servers_trusted_header"] = {
        "status": r_servers_login["status"],
        "ms": r_servers_login["ms"],
        "server_count": len(servers_redacted),
        "servers": servers_redacted,
        "runtime_scope": (servers_login or {}).get("runtime_scope"),
        "server_tool_count_sum": server_tool_sum,
    }
    probes["mcp_path_trusted_header"] = {"status": r_mcp_path_login["status"],
                                         "location": (r_mcp_path_login["headers"].get("location") or [""])[0]}
    if r_tools_cookie is not None:
        cookie_tools = parse_json(r_tools_cookie)
        probes["mcp_tools_session_cookie_only"] = {"status": r_tools_cookie["status"],
                                                   "total": (cookie_tools or {}).get("total"),
                                                   "source": (cookie_tools or {}).get("source")}

    # ── 交叉核对 ──
    names = [t["name"] for t in tools_redacted]
    d1_hits = [t for t in tools_redacted if "d1" in str(t.get("name", "")).lower()
               or "d1" in str(t.get("server", "")).lower()]
    research_hits = [t for t in tools_redacted if "research" in str(t.get("name", "")).lower()
                     or "research" in str(t.get("server", "")).lower()]
    evidence["summary"] = {
        "auth_method": "trusted-header X-Portal-User (nginx SSO) -> hermes_session cookie",
        "unauth_tools_status": probes["mcp_tools_anon"]["status"],
        "unauth_tools_body": probes["mcp_tools_anon"]["body"],
        "auth_tools_status": probes["mcp_tools_trusted_header"]["status"],
        "tool_count": (tools_login or {}).get("total"),
        "server_count": len(servers_redacted),
        "active_server_count": sum(1 for s in servers_redacted if s.get("active")),
        "unavailable_servers": (tools_login or {}).get("unavailable_servers"),
        "server_tool_count_sum": server_tool_sum,
        "tool_count_matches_server_sum": ((tools_login or {}).get("total") == server_tool_sum),
        "source": (tools_login or {}).get("source"),
        "runtime_scope": (tools_login or {}).get("runtime_scope"),
        "session_cookie_works_without_header": (probes.get("mcp_tools_session_cookie_only", {}) or {}).get("status") == 200,
        "d1_research_server_present": any("d1" in str(s.get("name", "")).lower() for s in servers_redacted),
        "d1_research_tool_count": len(d1_hits),
        "research_tool_hits": [t["name"] for t in research_hits][:20],
        "mcp_http_endpoint_on_8787": "none (/mcp is not a route; MCP tools inventory = GET /api/mcp/tools)",
    }

    print(json.dumps(evidence["summary"], ensure_ascii=False, indent=1))
    if args.out_dir:
        out = args.out_dir.rstrip("\\/") + "\\2026-11-04_mcp8787_probe_raw.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=1)
        print("[evidence] written:", out)


if __name__ == "__main__":
    main()
