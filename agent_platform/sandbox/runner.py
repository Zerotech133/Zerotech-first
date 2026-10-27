# -*- coding: utf-8 -*-
"""本地受限沙箱：隔离 cwd / 净环境 / 超时 / 输出限额，执行技能 verify（只读验证）。

边界（诚实说明）：
  · 只允许执行 `agent_platform/skills/<name>/scripts/verify.py`（路径白名单，禁任意命令）；
  · 每次运行在 `data/sandbox/runs/<uuid>/` 独立 cwd，环境变量白名单（去密钥/代理/Token）；
  · 超时 kill、stdout/stderr 各截断 64KB、结果落 results.jsonl（留证，可审计）；
  · 这是**进程级隔离**，不是 OS 内核沙箱；用于"验证脚本只读检查"，不接写入/网络类任务。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
SKILLS = PLATFORM / "skills"
SANDBOX = PLATFORM / "data" / "sandbox"
RESULTS = SANDBOX / "results.jsonl"
KEEP_RUNS = 200
OUTPUT_CAP = 64 * 1024
ENV_KEEP = (
    "PATH",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "COMSPEC",
    "PATHEXT",
    "USERPROFILE",
    "HOME",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
)
ENV_DENY_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PROXY",
    "COOKIE",
    "AUTH",
    "CREDENTIAL",
)


def _clean_env() -> dict:
    env = {}
    for k, v in os.environ.items():
        if k not in ENV_KEEP and k.upper() not in {x.upper() for x in ENV_KEEP}:
            continue
        if any(h in k.upper() for h in ENV_DENY_HINTS):
            continue
        env[k] = v
    env["ZHAOYUN_SANDBOX"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def verify_skill(name: str, timeout: int = 120) -> dict:
    t0 = time.time()
    name = str(name or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        return {"ok": False, "error": "bad_skill_name", "skill": name}
    script = (SKILLS / name / "scripts" / "verify.py").resolve()
    base = SKILLS.resolve()
    if not str(script).startswith(str(base) + os.sep) or not script.exists():
        return {"ok": False, "error": "verify_script_not_found", "skill": name}
    run_dir = SANDBOX / "runs" / ("%s-%s" % (name, uuid.uuid4().hex[:8]))
    run_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "zhaoyun_sandbox_run/v1",
        "skill": name,
        "sandbox_dir": str(run_dir),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, min(int(timeout), 300)),
            env=_clean_env(),
        )
        result.update(
            {
                "ok": r.returncode == 0,
                "rc": r.returncode,
                "stdout": (r.stdout or "")[:OUTPUT_CAP],
                "stderr": (r.stderr or "")[:OUTPUT_CAP],
            }
        )
    except subprocess.TimeoutExpired:
        result.update({"ok": False, "error": "timeout", "rc": None, "timeout": timeout})
    except OSError as e:
        result.update(
            {
                "ok": False,
                "error": "%s: %s" % (type(e).__name__, str(e)[:160]),
                "rc": None,
            }
        )
    result["duration_ms"] = int((time.time() - t0) * 1000)
    _record(result)
    return result


def _record(result: dict) -> None:
    try:
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        _prune()
    except OSError:
        pass


def _prune() -> None:
    try:
        lines = RESULTS.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > KEEP_RUNS:
            RESULTS.write_text("\n".join(lines[-KEEP_RUNS:]) + "\n", encoding="utf-8")
        runs = sorted((SANDBOX / "runs").glob("*"), key=lambda p: p.stat().st_mtime)
        for old in runs[:-KEEP_RUNS]:
            try:
                import shutil

                shutil.rmtree(old, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def recent(limit: int = 20) -> dict:
    rows = []
    try:
        lines = RESULTS.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-max(1, min(int(limit), 100)) :]:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            rows.append(
                {
                    k: r.get(k)
                    for k in (
                        "skill",
                        "ok",
                        "rc",
                        "duration_ms",
                        "error",
                        "started_at",
                        "sandbox_dir",
                    )
                }
            )
    except OSError:
        pass
    skills = []
    try:
        reg = json.loads((SKILLS / "registry.json").read_text(encoding="utf-8"))
        skills = [s.get("name") for s in reg.get("skills") or []]
    except (OSError, ValueError):
        pass
    return {
        "ok": True,
        "mode": "process-isolated-readonly-verify",
        "count": len(rows),
        "recent": rows,
        "skills": skills,
        "note": "进程级隔离（独立cwd/净环境/超时/输出限额），非内核沙箱",
    }
