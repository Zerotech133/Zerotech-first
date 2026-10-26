# -*- coding: utf-8 -*-
"""MCP stdio 客户端（零依赖）：初始化握手 / tools/list / tools/call / 超时。"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, argv: list, cwd: str | None = None, timeout: float = 20.0,
                 env: dict | None = None):
        self.argv = [str(a) for a in argv]
        self.cwd = cwd
        self.timeout = float(timeout)
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self.stderr_tail: list[str] = []

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self.proc is not None:
            return self
        self.proc = subprocess.Popen(
            self.argv, cwd=self.cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=self.env)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "zhaoyun-mcp-client", "version": "1.0.0"},
        })
        self._notify("notifications/initialized", {})
        return self

    def _read_loop(self):
        assert self.proc is not None
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except ValueError:
                self._q.put({"raw": line})
        self._q.put({"closed": True})

    def _write(self, obj: dict):
        if self.proc is None or self.proc.stdin is None:
            raise MCPError("client not started")
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method,
                     "params": params})
        deadline = time.time() + self.timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise MCPError("timeout: %s" % method)
            try:
                msg = self._q.get(timeout=remain)
            except queue.Empty:
                raise MCPError("timeout: %s" % method) from None
            if msg.get("closed"):
                raise MCPError("server closed: %s" % self._stderr())
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise MCPError("%s: %s" % (err.get("code"), err.get("message")))
            return msg.get("result") or {}

    def _notify(self, method: str, params: dict):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def list_tools(self) -> list:
        return (self._request("tools/list", {}) or {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        res = self._request("tools/call", {"name": name,
                                           "arguments": arguments or {}})
        content = res.get("content") or []
        text = ""
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        try:
            payload = json.loads(text) if text else {}
        except ValueError:
            payload = {"raw": text}
        return {"ok": not res.get("isError"), "isError": bool(res.get("isError")),
                "payload": payload, "result": res}

    def _stderr(self) -> str:
        try:
            if self.proc and self.proc.stderr:
                tail = self.proc.stderr.read() or ""
                self.stderr_tail = tail.strip().splitlines()[-5:]
        except OSError:
            pass
        return " | ".join(self.stderr_tail)[:300]

    def close(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False


def _env_with_pythonpath(extra_paths: list) -> dict:
    import os
    env = dict(os.environ)
    parts = [str(p) for p in extra_paths] + [env.get("PYTHONPATH", "")]
    env["PYTHONPATH"] = ";".join([p for p in parts if p])
    env["PYTHONIOENCODING"] = "utf-8"
    return env


if __name__ == "__main__":
    # 简单自测：python -m agent_platform.mcp.client
    c = MCPClient([sys.executable, "-m", "agent_platform.mcp.server"], timeout=10)
    print(c.start().list_tools())
    c.close()
