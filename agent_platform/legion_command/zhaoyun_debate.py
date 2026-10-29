# -*- coding: utf-8 -*-
"""辩论视图（zhaoyun 接口用）：子进程调 py312 tools/zhuge_debate.py（只读）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PY312 = Path(r"C:\项目\py312\python.exe")


def _run(args: list[str], timeout: float = 90.0, runner=None) -> dict:
    tool = Path(__file__).resolve().parents[2] / "tools" / "zhuge_debate.py"
    py = str(PY312) if PY312.exists() else sys.executable
    fn = runner or subprocess.run
    try:
        r = fn([py, "-X", "utf8", str(tool)] + args + ["--json"],
               capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": "debate_call_failed",
                "detail": type(e).__name__}
    if getattr(r, "returncode", 1) != 0:
        return {"ok": False, "error": "debate_failed",
                "detail": (getattr(r, "stderr", "") or "")[-300:]}
    try:
        return json.loads(getattr(r, "stdout", "") or "")
    except ValueError:
        return {"ok": False, "error": "bad_json"}


def debate_view(question: str = "", code: str = "", runner=None) -> dict:
    q = " ".join((question or "").split())
    if not q:
        return {"ok": False, "error": "empty_question"}
    args = ["--question", q]
    if code:
        args += ["--code", str(code)]
    rep = _run(args, runner=runner)
    rep["view"] = "debate"
    return rep
