# -*- coding: utf-8 -*-
"""CowAgent web console 幂等启动器（用户手动拉起用）。

用法（用户终端/双击均可）：
    python E:\\老田的AI助手\\ops\\start_cowagent_bg.py

行为：
  - 9899 已监听 → 幂等退出（不重复起）
  - 否则 DETACHED 拉起 CowAgent app.py（venv），日志追加 C:/Temp/cowagent_bg.log
  - 等待 HTTP /api/health 200（最多 60s）并报告结果

注意：在 WorkBuddy 沙箱会话内运行时，派生进程会被沙箱 Job 连坐清除
（2026-10-08 K273 实测：DETACHED/BREAKAWAY/PowerShell Start-Process 均秒杀，
schtasks 在安全黑名单）——请在自己的终端运行本脚本，进程即常驻。
"""
import json
import socket
import subprocess
import sys
import time
import urllib.request

COW_DIR = r"E:\老田的AI助手\CowAgent"
PY = COW_DIR + r"\venv\Scripts\python.exe"
LOG = r"C:\Temp\cowagent_bg.log"
PORT = 9899
HEALTH = f"http://127.0.0.1:{PORT}/api/health"


def port_open() -> bool:
    s = socket.socket()
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def http_ok() -> bool:
    """直连（绕系统代理，127.0.0.1 吃代理有前科）。"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(HEALTH, timeout=3) as r:
            return r.status == 200 and json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def main() -> int:
    if port_open():
        ok = http_ok()
        print(f"ALREADY RUNNING: port {PORT} listening, health={'OK' if ok else 'ABNORMAL'}")
        return 0

    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    logf = open(LOG, "ab")
    logf.write(f"\n=== launch {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
    logf.flush()
    p = subprocess.Popen(
        [PY, "app.py"],
        cwd=COW_DIR,
        stdin=subprocess.DEVNULL,
        stdout=logf,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        close_fds=True,
    )
    print(f"spawned pid={p.pid}, waiting for {PORT} ...")

    for i in range(60):
        time.sleep(1)
        if http_ok():
            print(f"READY after ~{i + 1}s: {HEALTH} -> 200 ok")
            return 0
        if p.poll() is not None:
            print(f"PROCESS EXITED early, code={p.returncode}; see {LOG}")
            return 2
    print("TIMEOUT: health not OK in 60s; see " + LOG)
    return 3


if __name__ == "__main__":
    sys.exit(main())
