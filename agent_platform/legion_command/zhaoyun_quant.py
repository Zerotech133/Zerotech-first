# -*- coding: utf-8 -*-
"""量化只读视图（zhaoyun 页面/接口用）：调 py312 的 tools/quant_readonly.py（只读）。

live 8796 跑 py3.13 venv，quant_readonly 依赖 py312 环境与 SOPS 解密，
故用子进程隔离调用；仅传参，不落 token。runner 可注入便于单测。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PY312 = Path(r"C:\项目\py312\python.exe")


def _run(args: list[str], timeout: float = 90.0, runner=None) -> dict:
    tool = Path(__file__).resolve().parents[2] / "tools" / "quant_readonly.py"
    py = str(PY312) if PY312.exists() else sys.executable
    fn = runner or subprocess.run
    try:
        r = fn([py, "-X", "utf8", str(tool)] + args + ["--json"],
               capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": "quant_call_failed",
                "detail": type(e).__name__}
    if getattr(r, "returncode", 1) != 0:
        return {"ok": False, "error": "quant_failed",
                "detail": (getattr(r, "stderr", "") or "")[-300:]}
    try:
        return json.loads(getattr(r, "stdout", "") or "")
    except ValueError:
        return {"ok": False, "error": "bad_json"}


def quant_view(tool: str = "stock_basic", code: str = "", start: str = "",
               end: str = "", limit: int = 20, runner=None) -> dict:
    tool = str(tool or "stock_basic")
    if tool not in ("stock_basic", "daily", "index_daily", "trade_cal",
                    "daily_basic"):
        return {"ok": False, "error": "tool_not_allowed", "tool": tool}
    try:
        limit = max(1, min(int(limit or 20), 50))
    except (TypeError, ValueError):
        limit = 20
    args = ["--tool", tool, "--limit", str(limit)]
    if code:
        args += ["--code", str(code)]
    if start:
        args += ["--start", str(start)]
    if end:
        args += ["--end", str(end)]
    rep = _run(args, runner=runner)
    rep["view"] = "quant_readonly"
    return rep
