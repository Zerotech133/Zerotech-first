# -*- coding: utf-8 -*-
"""OpenClaw CLI 桥（军团侧复用，2026-10-26 WO-LEGION-ZHUGE-8796OC）。

用途：让 8796 会议厅等**非 portal** 组件复用 OpenClaw 的 agent-config 载体
（GPT6 跨境不可达期间由 deepseek 中转；网络恢复后自动回 GPT6，无需改码），
并统一输出**诚实标签**：`carrier`=agent-config 实际模型、`relay`=是否中转替代。

纪律：
- 只读调用（`infer model run --local`），不做任何写操作；
- 不落盘密钥（由 OpenClaw secret store 承载）；
- 失败/超时返回 (None, error)，不抛；调用方自行决定显式降级并标注。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT = int(os.environ.get("OPENCLAW_CLI_TIMEOUT", "120"))
DEFAULT_AGENT = os.environ.get("OPENCLAW_CLI_AGENT", "zhuge")


def bin_path():
    """→ openclaw 可执行路径或 None（靠 PATH 查找，不猜路径）。"""
    for name in ("openclaw", "openclaw.cmd"):
        p = shutil.which(name)
        if p:
            return p
    return None


def config_path():
    """→ 现存 OpenClaw 配置 Path 或 None（新名 openclaw.json，旧名 config.json 兼容）。"""
    root = Path.home() / ".openclaw"
    for name in ("openclaw.json", "config.json"):
        p = root / name
        if p.exists():
            return p
    return None


def agent_primary_model(agent=None):
    """→ OpenClaw agent 配置里的 model.primary（如 deepseek/deepseek-chat）；读不到 ""。"""
    cfg = config_path()
    if not cfg:
        return ""
    try:
        d = json.loads(cfg.read_text(encoding="utf-8"))
        entries = (d.get("agents") or {}).get("entries") or {}
        ent = entries.get(agent or DEFAULT_AGENT) or {}
        return str(((ent.get("model") or {}).get("primary")) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def is_relay(model):
    """agent-config 载体是否为「非 GPT6 中转替代」。"""
    m = str(model or "").lower()
    return bool(m) and "gpt-6" not in m


def parse_stdout(stdout, stderr="", returncode=0):
    """解析 `infer model run --json` → (text, error)；兼容纯文本/日志行。"""
    txt = (stdout or "").strip()
    if txt:
        try:
            d = json.loads(txt)
        except ValueError:
            d = None
        if isinstance(d, dict):
            if d.get("ok"):
                out = d.get("text") or d.get("output") or d.get("content")
                if not out and isinstance(d.get("outputs"), list):
                    parts = [str(it.get("text") or it.get("content") or "")
                             for it in d["outputs"] if isinstance(it, dict)]
                    out = "\n".join(p for p in parts if p)
                if not out:
                    res = d.get("result")
                    if isinstance(res, dict):
                        out = res.get("text") or res.get("content")
                        # A2-B09：与 portal/comm_zhuge 对齐，兼容 result.outputs[*].text 嵌套
                        if not out and isinstance(res.get("outputs"), list):
                            parts = [str(it.get("text") or it.get("content") or "")
                                     for it in res["outputs"] if isinstance(it, dict)]
                            out = "\n".join(p for p in parts if p)
                out = str(out or "").strip()
                return (out, None) if out else (None, "empty_output")
            err = d.get("error")
            msg = err.get("message") if isinstance(err, dict) else str(err or "unknown")
            return None, "cli_error: %s" % str(msg)[-160:]
    if returncode != 0:
        return None, "exit=%d: %s" % (returncode, (stderr or txt or "")[-160:])
    lines = [ln for ln in txt.splitlines()
             if ln.strip() and not ln.startswith("[openclaw]")
             and not ln.startswith("[provider-")]
    out = "\n".join(lines).strip()
    return (out, None) if out else (None, "empty_output")


def run(agent, prompt, model=None, timeout=None):
    """跑一次纯模型推理。→ (text, error)。绝不抛（调用方靠 error 判断）。"""
    exe = bin_path()
    if not exe:
        return None, "openclaw_bin_missing"
    if not config_path():
        return None, "openclaw_config_missing"
    one_line = " ".join(str(prompt or "").split())
    args = [exe, "infer", "model", "run", "--local", "--agent", agent]
    if model:
        args += ["--model", model]
    args += ["--prompt", one_line, "--json"]
    try:
        cp = subprocess.run(args, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            timeout=timeout or DEFAULT_TIMEOUT,
                            env={**os.environ})
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as ex:  # noqa: BLE001
        return None, "%s: %s" % (type(ex).__name__, ex)
    return parse_stdout(cp.stdout, cp.stderr, cp.returncode)
