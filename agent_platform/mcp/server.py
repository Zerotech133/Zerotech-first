# -*- coding: utf-8 -*-
"""MCP stdio JSON-RPC 服务端通用实现（零依赖，stdlib）。

MCP stdio transport：每行一条 JSON-RPC 2.0 消息（换行分隔）。
支持最小方法集：initialize / notifications/initialized / ping / tools/list / tools/call。
工具注册表结构：{name: {"description": str, "inputSchema": dict, "handler": callable}}
"""
from __future__ import annotations

import json
import sys
import traceback

PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "1.0.0"


def error(req_id, code: int, message: str):
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def dispatch(request: dict, tools: dict, server_name: str,
             server_version: str = SERVER_VERSION):
    """处理单条请求；notification 返回 None；非法请求返回 error。"""
    if not isinstance(request, dict):
        return error(None, -32600, "invalid request")
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}
    if method is None:
        return error(req_id, -32600, "missing method")
    if req_id is None and method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": server_name, "version": server_version},
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": [
            {"name": n,
             "description": spec.get("description", ""),
             "inputSchema": spec.get("inputSchema") or
                            {"type": "object", "properties": {}}}
            for n, spec in tools.items()]})
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        spec = tools.get(name)
        if spec is None:
            return error(req_id, -32602, "unknown tool: %s" % name)
        if not isinstance(args, dict):
            return error(req_id, -32602, "arguments must be object")
        try:
            payload = spec["handler"](**args)
            text = json.dumps(payload, ensure_ascii=False)
            return _result(req_id, {"content": [{"type": "text", "text": text}],
                                    "isError": False})
        except TypeError as e:
            return _result(req_id, {"content": [{"type": "text",
                                                 "text": "bad arguments: %s" % e}],
                                    "isError": True})
        except Exception as e:  # noqa: BLE001
            return _result(req_id, {"content": [{"type": "text", "text":
                                                 "%s: %s" % (type(e).__name__, e)}],
                                    "isError": True})
    return error(req_id, -32601, "method not found: %s" % method)


def serve_stdio(tools: dict, server_name: str, server_version: str = SERVER_VERSION) -> int:
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            out = error(None, -32700, "parse error")
        else:
            try:
                out = dispatch(req, tools, server_name, server_version)
            except Exception:  # noqa: BLE001
                out = error(req.get("id") if isinstance(req, dict) else None,
                            -32603, "internal error")
                traceback.print_exc(file=sys.stderr)
        if out is not None:
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
