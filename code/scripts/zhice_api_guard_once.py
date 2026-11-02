# -*- coding: utf-8 -*-
"""一次性 8105 守护检查：供计划任务每 5 分钟调用。

ACL-104（2026-11-02）：8105 启动前强制跑只读迁移 preflight
（`C:\\项目\\老田的AI助手\\tools\\zhice_migration_preflight.py --db --json`）。

- rc=0  → 才允许按原逻辑启动 8105（原行为不变）；
- rc≠0 / 超时 / 异常 / JSON 不可解析 → 写 `C:\\Temp\\zhice_api_8105_guard_preflight.log`
  并 exit 1（**fail-closed**：不起 8105；DB 迁移链与仓库 head 不一致时拒绝带病启动）。
- 环境变量 `ZHICE_GUARD_PREFLIGHT_ONLY=1`：只跑 preflight 即退出（演练用，不判断 8105 是否在跑）。
- 环境变量 `ZHICE_GUARD_PREFLIGHT_PY` / `ZHICE_GUARD_PREFLIGHT_TOOL`：覆盖 preflight
  解释器 / 工具路径（默认 `sys.executable` / 固定工具路径）。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CODE = Path(r"C:\项目\zhice-os-v2-windows-20260920\code")
PY = CODE / ".venv" / "Scripts" / "python.exe"
PREFLIGHT_TOOL = Path(os.environ.get(
    "ZHICE_GUARD_PREFLIGHT_TOOL",
    r"C:\项目\老田的AI助手\tools\zhice_migration_preflight.py",
))
PREFLIGHT_LOG = Path(r"C:\Temp\zhice_api_8105_guard_preflight.log")
PREFLIGHT_TIMEOUT_S = 120.0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _log(line: str) -> None:
    """append-only 日志；写日志失败不改变 fail-closed 语义（仍返回非 0）。"""
    try:
        PREFLIGHT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(PREFLIGHT_LOG, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s\n" % (_now(), line))
    except OSError:
        pass


def run_preflight() -> int:
    """只读迁移 preflight：0=通过；非 0=阻断（fail-closed）。不启动/不重启任何服务。"""
    py = os.environ.get("ZHICE_GUARD_PREFLIGHT_PY") or sys.executable or "python"
    cmd = [str(py), str(PREFLIGHT_TOOL), "--db", "--json"]
    # 子进程强制 UTF-8：Windows 管道下 Python 默认按 locale(cp936) 编码 stdout，
    # 会把中文错误文案写成 GBK 字节，父进程按 UTF-8 解析会出现替换符（已实测）。
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    _log("preflight start: %s" % " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=PREFLIGHT_TIMEOUT_S, env=env,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed：任何异常都不启动 8105
        _log("preflight EXCEPTION %s: %s" % (type(exc).__name__, str(exc)[:300]))
        return 1
    try:
        payload = json.loads((proc.stdout or "").strip() or "null")
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        _log("preflight UNPARSEABLE rc=%s stdout_tail=%r stderr_tail=%r"
             % (proc.returncode, (proc.stdout or "")[-300:], (proc.stderr or "")[-300:]))
        return 1
    try:
        rc = int(payload.get("rc", proc.returncode))
    except (TypeError, ValueError):
        rc = proc.returncode or 1
    summary = {
        "rc": rc,
        "ok": payload.get("ok"),
        "head": payload.get("head"),
        "expected": payload.get("expected"),
        "db": {k: (payload.get("db") or {}).get(k)
               for k in ("requested", "mode", "current", "ok", "error")},
        "findings": [f.get("code") for f in (payload.get("findings") or [])],
    }
    if rc != 0:
        _log("preflight FAIL %s stderr_tail=%r"
             % (json.dumps(summary, ensure_ascii=False), (proc.stderr or "")[-300:]))
        return 1
    _log("preflight PASS %s" % json.dumps(summary, ensure_ascii=False))
    return 0


def up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", 8105)) == 0


def main() -> int:
    # 演练模式：只检查后退出（不判断 8105 是否在跑、不启动任何进程）
    if os.environ.get("ZHICE_GUARD_PREFLIGHT_ONLY", "").strip() == "1":
        return run_preflight()
    if up():
        return 0
    # 启动前硬闸：迁移链/DB revision 不一致 → 拒绝启动（fail-closed）
    if run_preflight() != 0:
        return 1
    env = dict(os.environ, PYTHONPATH=str(CODE / "src"))
    out = open(r"C:\Temp\zhice_api_8105_task.out.log", "a", encoding="utf-8")
    err = open(r"C:\Temp\zhice_api_8105_task.err.log", "a", encoding="utf-8")
    subprocess.Popen(
        [str(PY), "-m", "uvicorn", "zhice_os_v2.api.main:app",
         "--host", "127.0.0.1", "--port", "8105"],
        cwd=str(CODE), env=env, stdout=out, stderr=err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return 10


if __name__ == "__main__":
    raise SystemExit(main())
