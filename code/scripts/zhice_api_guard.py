# -*- coding: utf-8 -*-
"""8105 智策 API 临时守护：端口未监听则拉起，30s 轮询。

仅用于 11-02 试运行窗口；不是永久服务。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CODE = Path(r"C:\项目\zhice-os-v2-windows-20260920\code")
PY = CODE / ".venv" / "Scripts" / "python.exe"
LOG = Path(r"C:\Temp\zhice_api_8105_guard.log")


def up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", 8105)) == 0


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))


def main() -> int:
    log("guard start")
    while True:
        if not up():
            log("8105 down, starting")
            env = dict(os.environ, PYTHONPATH=str(CODE / "src"))
            out = open(r"C:\Temp\zhice_api_8105_guard.out.log", "a", encoding="utf-8")
            err = open(r"C:\Temp\zhice_api_8105_guard.err.log", "a", encoding="utf-8")
            subprocess.Popen(
                [str(PY), "-m", "uvicorn", "zhice_os_v2.api.main:app",
                 "--host", "127.0.0.1", "--port", "8105"],
                cwd=str(CODE), env=env, stdout=out, stderr=err,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            time.sleep(5)
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
