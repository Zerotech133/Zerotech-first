# -*- coding: utf-8 -*-
"""IDE 受控执行（R2）：命令白名单 + 超时/输出上限 + 环境净化。

白名单（最小集，按需再加）：
  · git status [-s|--short|--porcelain]
  · git diff [--stat|--name-only] [-- <path>]
  · git log --oneline [-n N | -N]
  · git branch --show-current
  · python -m pytest <worktree 内路径>... [-q]
  · python -m py_compile <worktree 内文件>
  · python --version

拒绝：shell 元字符（; & | > < ` $( %）、任意未列命令、worktree 外路径。
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

MAX_OUTPUT = 256 * 1024
DEFAULT_TIMEOUT = 120
SHELL_META = (";", "&", "|", ">", "<", "`", "$(", "\n", "\r")
ENV_DENY = ("KEY", "TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL")


def _err(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}


def _clean_env(worktree: Path) -> dict:
    env = {}
    for k, v in os.environ.items():
        if any(tok in k.upper() for tok in ENV_DENY):
            continue
        if k.upper() in (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "PYTHONUTF8",
            "PYTHONIOENCODING",
            "USERPROFILE",
            "HOME",
        ):
            env[k] = v
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _inside(worktree: Path, candidate: str) -> bool:
    try:
        (Path(worktree) / candidate).resolve().is_relative_to(Path(worktree).resolve())
        return True
    except (OSError, ValueError):
        return False


def _validate_tokens(worktree: Path, tokens: list[str]) -> dict:
    if not tokens:
        return _err("empty_command", "命令为空")
    prog = tokens[0].lower()
    wt = Path(worktree)

    if prog == "git":
        if len(tokens) < 2:
            return _err("bad_command", "git 需要子命令")
        sub = tokens[1]
        rest = tokens[2:]
        if sub == "status":
            if rest and any(a not in ("-s", "--short", "--porcelain") for a in rest):
                return _err("denied", "git status 仅允许 -s/--short/--porcelain")
            return {"ok": True, "argv": ["git", "-C", str(wt), "status", *rest]}
        if sub == "diff":
            paths = []
            i = 0
            while i < len(rest):
                a = rest[i]
                if a in ("--stat", "--name-only"):
                    i += 1
                elif a == "--":
                    paths = rest[i + 1 :]
                    break
                else:
                    return _err(
                        "denied", "git diff 仅允许 --stat/--name-only/-- <path>"
                    )
            if any(not _inside(wt, p) for p in paths):
                return _err("denied", "git diff 路径越出 worktree")
            return {"ok": True, "argv": ["git", "-C", str(wt), "diff", *rest]}
        if sub == "log":
            args = ["--oneline"]
            if rest:
                if len(rest) == 2 and rest[0] == "-n" and rest[1].isdigit():
                    args = ["--oneline", "-n", rest[1]]
                elif (
                    len(rest) == 1 and rest[0].startswith("-") and rest[0][1:].isdigit()
                ):
                    args = ["--oneline", rest[0]]
                else:
                    return _err("denied", "git log 仅允许 --oneline [-n N| -N]")
            return {"ok": True, "argv": ["git", "-C", str(wt), "log", *args]}
        if sub == "branch" and rest == ["--show-current"]:
            return {
                "ok": True,
                "argv": ["git", "-C", str(wt), "branch", "--show-current"],
            }
        return _err("denied", "git 子命令不在白名单：%s" % sub)

    if prog in ("python", "py") or prog.endswith("python.exe"):
        exe = sys.executable
        if tokens[1:] == ["--version"]:
            return {"ok": True, "argv": [exe, "--version"]}
        if len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] == "pytest":
            rest = tokens[3:]
            paths = [a for a in rest if not a.startswith("-")]
            flags = [a for a in rest if a.startswith("-")]
            if any(f not in ("-q", "-x") for f in flags):
                return _err("denied", "pytest 仅允许 -q/-x 与路径")
            for p in paths:
                if not _inside(wt, p):
                    return _err("denied", "pytest 路径越出 worktree：%s" % p)
                if not (wt / p).exists():
                    return _err("path_missing", "路径不存在：%s" % p)
            if not paths:
                return _err("denied", "pytest 必须指定 worktree 内路径")
            return {"ok": True, "argv": [exe, "-m", "pytest", *rest]}
        if len(tokens) == 4 and tokens[1] == "-m" and tokens[2] == "py_compile":
            target = tokens[3]
            if not _inside(wt, target) or not (wt / target).is_file():
                return _err("denied", "py_compile 目标须为 worktree 内文件")
            return {"ok": True, "argv": [exe, "-m", "py_compile", target]}
        return _err(
            "denied", "python 命令不在白名单（-m pytest / -m py_compile / --version）"
        )

    return _err("denied", "命令不在白名单：%s" % tokens[0])


def validate(worktree: Path, command: str) -> dict:
    cmd = str(command or "").strip()
    if not cmd:
        return _err("empty_command", "命令为空")
    if any(m in cmd for m in SHELL_META):
        return _err("shell_meta", "命令含 shell 元字符（; & | > < ` $( 等），拒绝")
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError as exc:
        return _err("bad_quote", "命令解析失败：%s" % exc)
    return _validate_tokens(Path(worktree), tokens)


def run(worktree: Path, command: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    check = validate(worktree, command)
    if not check.get("ok"):
        return check
    argv = check["argv"]
    t0 = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout)),
            env=_clean_env(Path(worktree)),
        )
    except subprocess.TimeoutExpired:
        return _err("timeout", "命令超时（>%ss）：%s" % (timeout, command))
    except FileNotFoundError:
        return _err("not_found", "可执行文件不存在：%s" % argv[0])
    except OSError as exc:
        return _err("exec_failed", "执行失败：%s" % type(exc).__name__)
    out = (proc.stdout or "") + (proc.stderr or "")
    truncated = len(out) > MAX_OUTPUT
    return {
        "ok": True,
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": out[:MAX_OUTPUT],
        "truncated": truncated,
        "duration_ms": int((time.time() - t0) * 1000),
        "command": command,
    }
