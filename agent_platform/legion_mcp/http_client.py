# -*- coding: utf-8 -*-
"""MCP Streamable HTTP 客户端（零依赖）：POST JSON-RPC，兼容 JSON 与 SSE 响应。

说明：
  · 仅用于**配置白名单**内的 MCP HTTP server（url 来自 servers.json，不接受用户 URL）；
  · 支持 initialize（记录 Mcp-Session-Id 并在后续请求回传）、tools/list、tools/call；
  · token 从环境变量读取（token_env），不落仓、不打印。
"""

from __future__ import annotations

import json
import os
import urllib.request
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

PROTOCOL_VERSION = "2025-06-18"
UA = "zhaoyun-mcp-http/1.0"


class MCPHttpError(RuntimeError):
    pass


class MCPHttpClient:
    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: float = 15.0,
        headers: dict | None = None,
    ):
        u = str(url or "").strip()
        if not u.lower().startswith(("http://", "https://")):
            raise ValueError("http_url_required")
        self.url = u
        self.token = token
        self.timeout = float(timeout)
        self.headers = dict(headers or {})
        self.session_id = ""
        self._next_id = 0

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": UA,
        }
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        h.update(self.headers)
        return h

    def _parse(self, raw: str, content_type: str, rid: int) -> dict:
        ct = (content_type or "").lower()
        candidates = []
        if "text/event-stream" in ct or raw.lstrip().startswith("data:"):
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    candidates.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
        else:
            try:
                data = json.loads(raw)
                candidates = data if isinstance(data, list) else [data]
            except ValueError:
                raise MCPHttpError("bad_json_response") from None
        for msg in candidates:
            if not isinstance(msg, dict):
                continue
            if msg.get("id") not in (rid, None):
                continue
            if "error" in msg:
                err = msg.get("error") or {}
                raise MCPHttpError("%s: %s" % (err.get("code"), err.get("message")))
            return msg.get("result") or {}
        raise MCPHttpError("response_id_mismatch")

    def _post(self, payload: dict):
        rid = payload.get("id")
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with safe_urlopen(req, timeout=self.timeout) as r:
                raw = r.read(2_000_000).decode("utf-8", errors="replace")
                ct = r.headers.get("Content-Type") or ""
                sid = r.headers.get("Mcp-Session-Id") or ""
                if sid:
                    self.session_id = sid
        except Exception as e:  # noqa: BLE001
            raise MCPHttpError("http_request_failed: %s" % str(e)[:160]) from None
        if rid is None:
            return None
        return self._parse(raw, ct, int(rid))

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        return self._post(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        )

    def _notify(self, method: str, params: dict) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self) -> dict:
        res = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "zhaoyun-mcp-http", "version": "1.0.0"},
            },
        )
        try:
            self._notify("notifications/initialized", {})
        except MCPHttpError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_mcp/http_client.py", exc)
        return res

    def list_tools(self) -> list:
        return (self._request("tools/list", {}) or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        content = res.get("content") or []
        text = ""
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        try:
            payload = json.loads(text) if text else {}
        except ValueError:
            payload = {"raw": text}
        return {
            "ok": not res.get("isError"),
            "isError": bool(res.get("isError")),
            "payload": payload,
        }

    def close(self):
        self.session_id = ""


def token_from_env(cfg: dict) -> str:
    env = str(cfg.get("token_env") or "")
    if not env:
        return ""
    return str(os.environ.get(env) or "").strip()
