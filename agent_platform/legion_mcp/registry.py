# -*- coding: utf-8 -*-
"""赵云 MCP 工具注册表：服务器配置加载 / 探测 / 白名单调用（只读）。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
CONFIG = PLATFORM / "mcp" / "servers.json"
_CACHE: dict = {"ts": 0.0, "data": None}
CACHE_TTL = 60.0


def load_servers() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {
            "schema": "zhaoyun_mcp_servers/v1",
            "servers": {},
            "error": "config_unreadable",
        }
    return data


def _argv(cfg: dict) -> list:
    script = cfg.get("script")
    if not script:
        raise ValueError("server config missing script")
    argv = [sys.executable, str((REPO / script).resolve())]
    argv += [str(a) for a in (cfg.get("args") or [])]
    return argv


def _timeout(cfg: dict, default: float = 20.0) -> float:
    try:
        return float(cfg.get("timeout") or default)
    except (TypeError, ValueError):
        return default


def _is_http(cfg: dict) -> bool:
    return str(cfg.get("transport") or "stdio").lower() == "http"


def _http_client(cfg: dict, timeout: float | None = None):
    from .http_client import MCPHttpClient, token_from_env  # noqa: WPS433

    return MCPHttpClient(
        str(cfg.get("url") or ""),
        token=token_from_env(cfg),
        timeout=timeout or _timeout(cfg, 15.0),
    )


def probe(name: str | None = None, timeout: float | None = None) -> dict:
    """启动 server → initialize → tools/list → 关闭；返回健康与工具清单。"""
    sys.path.insert(0, str(PLATFORM))
    from .client import MCPClient, MCPError  # noqa: WPS433

    servers = load_servers().get("servers") or {}
    names = [name] if name else list(servers.keys())
    out = []
    for n in names:
        cfg = servers.get(n) or {}
        t0 = time.time()
        item = {
            "name": n,
            "risk": cfg.get("risk", "unknown"),
            "transport": "http" if _is_http(cfg) else "stdio",
            "ok": False,
            "tools": [],
            "error": "",
        }
        if not cfg:
            item["error"] = "server_not_configured"
            out.append(item)
            continue
        if cfg.get("enabled") is False:
            # 显式禁用 = 正常配置（跳过），不算失败；调用仍会被拒
            item["ok"] = True
            item["skipped"] = True
            item["error"] = "transport_disabled" + (
                "（" + str(cfg.get("note")) + "）" if cfg.get("note") else ""
            )
            item["elapsed_ms"] = int((time.time() - t0) * 1000)
            out.append(item)
            continue
        client = None
        try:
            if _is_http(cfg):
                client = _http_client(cfg, timeout)
                client.initialize()
            else:
                client = MCPClient(
                    _argv(cfg), cwd=str(REPO), timeout=timeout or _timeout(cfg)
                ).start()
            tools = client.list_tools()
            item["ok"] = True
            item["tools"] = [t.get("name") for t in tools if isinstance(t, dict)]
        except (MCPError, OSError, ValueError, RuntimeError) as e:
            item["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        finally:
            if client is not None:
                client.close()
        item["elapsed_ms"] = int((time.time() - t0) * 1000)
        out.append(item)
    return {"ok": all(i["ok"] for i in out), "count": len(out), "servers": out}


def summary(force: bool = False, ttl: float = CACHE_TTL) -> dict:
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["ts"] < ttl:
        data = dict(_CACHE["data"])
        data["cached"] = True
        return data
    rep = probe()
    _CACHE.update(ts=now, data=rep)
    rep["cached"] = False
    return rep


def call(
    server: str, tool: str, arguments: dict | None = None, timeout: float = 30.0
) -> dict:
    servers = load_servers().get("servers") or {}
    cfg = servers.get(str(server or ""))
    if not cfg:
        return {"ok": False, "error": "server_not_allowed", "server": server}
    allow = cfg.get("allow_tools") or []
    if allow != "*" and str(tool) not in allow:
        return {
            "ok": False,
            "error": "tool_not_allowed",
            "server": server,
            "tool": tool,
            "allowed": allow,
        }
    if cfg.get("risk") != "read-only":
        return {"ok": False, "error": "server_not_read_only", "server": server}
    sys.path.insert(0, str(PLATFORM))
    if _is_http(cfg):
        if cfg.get("enabled") is False:
            return {
                "ok": False,
                "error": "transport_disabled",
                "server": server,
                "note": cfg.get("note") or "",
            }
        from .http_client import MCPHttpError  # noqa: WPS433

        client = None
        try:
            client = _http_client(cfg, timeout)
            client.initialize()
            rep = client.call_tool(tool, arguments or {})
            return {
                "ok": bool(rep.get("ok")),
                "server": server,
                "tool": tool,
                "payload": rep.get("payload"),
                "isError": rep.get("isError"),
            }
        except (MCPHttpError, ValueError) as e:
            return {
                "ok": False,
                "error": "%s: %s" % (type(e).__name__, str(e)[:160]),
                "server": server,
                "tool": tool,
            }
        finally:
            if client is not None:
                client.close()
    from .client import MCPClient, MCPError  # noqa: WPS433

    client = None
    try:
        client = MCPClient(
            _argv(cfg), cwd=str(REPO), timeout=timeout or _timeout(cfg, 30.0)
        ).start()
        rep = client.call_tool(tool, arguments or {})
        return {
            "ok": bool(rep.get("ok")),
            "server": server,
            "tool": tool,
            "payload": rep.get("payload"),
            "isError": rep.get("isError"),
        }
    except (MCPError, OSError, ValueError) as e:
        return {
            "ok": False,
            "error": "%s: %s" % (type(e).__name__, str(e)[:160]),
            "server": server,
            "tool": tool,
        }
    finally:
        if client is not None:
            client.close()


def selftest() -> dict:
    rep = probe()
    missing = [s["name"] for s in rep["servers"] if not s.get("tools")]
    return {
        "ok": rep["ok"] and not missing,
        "servers": rep["servers"],
        "missing_tools": missing,
    }
