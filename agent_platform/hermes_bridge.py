# -*- coding: utf-8 -*-
"""D1 ↔ Hermes stock-data 只读 MCP 数据桥（2026-10-30 WO-D1-HERMES-BRIDGE）。

方向：D1 引擎 → Hermes stock-data MCP（stdio JSON-RPC）→ 8791 数据总览 API。
只读、fail-open、带缓存、可用 D1_HERMES_BRIDGE=0 关闭；不参与编排、不改裁决链。
"""
from __future__ import annotations

import json
import os
import subprocess
import time

PYTHON = os.environ.get("D1_HERMES_PYTHON", r"C:\Program Files\Python311\python.exe")
SERVER = os.environ.get(
    "D1_HERMES_STOCK_MCP",
    r"C:\项目\zerobound-kg\stock_data_mcp.py",
)
CACHE_TTL = int(os.environ.get("D1_HERMES_CACHE_TTL", "300"))
_CACHE = {"ts": 0.0, "risk": None, "signals": None}
_TIMEOUT = float(os.environ.get("D1_HERMES_TIMEOUT", "20"))


def enabled():
    return os.environ.get("D1_HERMES_BRIDGE", "1").strip().lower() not in (
        "0", "off", "false", "no")


def _mcp_call(name, arguments=None, timeout=None):
    """一次 stdio MCP 调用；返回 result.content 文本解析后的对象，失败抛异常。"""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "d1-hermes-bridge", "version": "1.0"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": name, "arguments": arguments or {}}},
    ]
    payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    p = subprocess.Popen(
        [PYTHON, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        out, err = p.communicate(payload, timeout=timeout or _TIMEOUT)
    except subprocess.TimeoutExpired:
        # 超时必须 kill + 回收，避免 run_shadow 长跑积累僵尸子进程
        p.kill()
        try:
            p.communicate(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        if p.poll() is None:
            p.kill()
    if not out.strip():
        raise RuntimeError("mcp empty output: %s" % (err or "")[:160])
    res = None
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") == 2:
            res = msg
    if not res or "result" not in res:
        raise RuntimeError("mcp call failed: %s" % out[:200])
    content = (res.get("result") or {}).get("content") or []
    if not content:
        return {}
    text = content[0].get("text") or ""
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def _load(force=False):
    if not enabled():
        return None, None
    age = time.time() - _CACHE.get("ts", 0)
    if not force and _CACHE.get("risk") is not None and age < CACHE_TTL:
        return _CACHE["risk"], _CACHE["signals"]
    try:
        risk = _mcp_call("risk_overview")
        signals = _mcp_call("strategy_signals")
        _CACHE.update({"ts": time.time(), "risk": risk, "signals": signals})
        return risk, signals
    except Exception:
        return _CACHE.get("risk"), _CACHE.get("signals")


def _symbol(code):
    return str(code or "").split(".")[0].strip()


def context_text(code=None):
    """生成给投研 prompt 的只读补充；失败返回空字符串。"""
    risk, signals = _load()
    parts = []
    if isinstance(risk, dict) and risk.get("ok"):
        gate = risk.get("market_gate") or {}
        if gate:
            parts.append("市场闸: action=%s score=%s reason=%s" % (
                gate.get("action"), gate.get("score"), str(gate.get("reason") or "")[:180]))
        rules = (risk.get("risk") or {}).get("rules") or {}
        rs = []
        for k in ("R1", "R2", "R3", "R4"):
            r = rules.get(k) or {}
            if r.get("pass") or r.get("block"):
                rs.append("%s pass=%s block=%s" % (k, r.get("pass"), r.get("block")))
        if rs:
            parts.append("风控水位: " + " | ".join(rs))
    if isinstance(signals, dict) and signals.get("ok"):
        rows = signals.get("signals") or []
        sym = _symbol(code)
        mine = [x for x in rows if _symbol(x.get("symbol")) == sym]
        parts.append("今日信号总数: %s" % signals.get("count"))
        if mine:
            s = mine[0]
            parts.append("本票Hermes信号: action=%s score=%s conf=%s %s" % (
                s.get("action"), s.get("score"), s.get("confidence"),
                str(s.get("rationale") or "")[:180]))
        else:
            top = rows[:3]
            if top:
                parts.append("市场高分信号: " + "; ".join(
                    "%s %s %s" % (x.get("symbol"), x.get("action"), x.get("score"))
                    for x in top))
    return "\n".join(parts)
