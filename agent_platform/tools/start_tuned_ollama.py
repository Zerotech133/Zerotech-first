#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ollama 守护启动器（tuned 配置）—— 2026-10-18 建立

背景（为什么需要这个脚本，别再用别的方式启动）
--------------------------------------------------
Ollama 在本机有一套 tuned 参数，原始载体是启动文件夹里的
`ollama-serve-tuned.bat`（2026-10-15 为 AMD Radeon 8060S iGPU 配的）：

    OLLAMA_FLASH_ATTENTION=1       # 大上下文性能
    OLLAMA_KV_CACHE_TYPE=q8_0      # 修 f16 KV cache 在大上下文下输出乱码
    OLLAMA_IGPU_ENABLE=1
    OLLAMA_CONTEXT_LENGTH=65536    # 上下文上限
    OLLAMA_KEEP_ALIVE=2h           # 模型驻留 2 小时，避免反复卸载重载
    OLLAMA_NUM_PARALLEL=2          # 并发 2，实测吞吐 1.53x

⚠️ 三个已踩过的坑，都是本脚本存在的原因：

1. **裸 `ollama serve` 不带这些变量 = 用默认配置（ctx=4096 / keep_alive=5m /
   KV=f16 / parallel=1）**。表面上服务是通的，但 run_shadow 的推理质量与
   稳定性都受影响，而且 `ollama ps` 里 ctx 显示 4096 才看得出来。
2. **`server.log` 是「Ollama 桌面 App」写的，CLI `serve` 的日志不进这个文件。**
   所以排查时读 `%LOCALAPPDATA%\\Ollama\\server.log` 会读到上一次 App 实例的
   旧 config 行，误判为「环境变量没生效」。要看真实 config，必须把 serve 的
   stdout 重定向到自己的文件。
3. **桌面 App 与 CLI serve 抢 11434 会互相踢**（日志出现
   `app_windows.go ... starting ollama server` + 请求 502）。所以启动前应先确认
   没有别的实例，必要时 taskkill 再起。

另一个必须知道的行为：**首次加载模型会超过 HTTP 客户端的常见超时**，
表现为 502 Bad Gateway（`waiting for llama-server to become available`），
但日志随后会显示模型其实已加载成功。所以「第一次请求 502」不等于配置错，
稍后重试通常就通。

用法
----
    python start_tuned_ollama.py            # 检查；不在则用 tuned 配置启动
    python start_tuned_ollama.py --status    # 只查状态，不启动
    python start_tuned_ollama.py --restart   # 强制重启（会短暂中断推理）

退出码：0=已在跑或启动成功；1=启动失败；2=参数错误
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOST = "127.0.0.1"
PORT = 11434
EXE = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\t\AppData\Local")) / "Programs" / "Ollama" / "ollama.exe"
# 日志按定时任务产物统一约定落 automation_runs/<任务名>/（不随脚本位置漂移）
LOG_DIR = Path(os.environ.get("OLLAMA_GUARD_LOG_DIR", r"C:\aios\automation_runs\ollama_guard"))
SERVE_LOG = LOG_DIR / "serve_tuned.log"
GUARD_LOG = LOG_DIR / "guard.log"

TUNED_ENV = {
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_IGPU_ENABLE": "1",
    "OLLAMA_CONTEXT_LENGTH": "65536",
    "OLLAMA_KEEP_ALIVE": "2h",
    "OLLAMA_NUM_PARALLEL": "2",
}

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def log(msg: str) -> None:
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with GUARD_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def port_open(timeout: float = 2.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        return True
    except Exception:
        return False
    finally:
        s.close()


def server_config() -> dict:
    """从 serve 自己的日志里取最新一条 server config 的 env 值。"""
    if not SERVE_LOG.exists():
        return {}
    try:
        text = SERVE_LOG.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    cfgs = [ln for ln in text.splitlines() if "server config" in ln]
    if not cfgs:
        return {}
    last = cfgs[-1]
    out = {}
    for key in TUNED_ENV:
        marker = key + ":"
        idx = last.find(marker)
        if idx >= 0:
            rest = last[idx + len(marker):]
            out[key] = rest.split(" ")[0].split("]")[0]
    return out


def ollama_procs() -> list[str]:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
        return [ln for ln in (r.stdout or "").splitlines() if "ollama.exe" in ln.lower()]
    except Exception:
        return []


def start() -> bool:
    if not EXE.exists():
        log("FATAL 找不到 ollama.exe: %s" % EXE)
        return False

    env = os.environ.copy()
    env.update(TUNED_ENV)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = SERVE_LOG.open("ab")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [str(EXE), "serve"], env=env,
        stdout=fh, stderr=subprocess.STDOUT, creationflags=flags,
    )
    log("STARTED pid=%s (tuned env 已注入)" % proc.pid)

    deadline = time.time() + 60
    while time.time() < deadline:
        if port_open():
            log("PORT %d OPEN" % PORT)
            return True
        if proc.poll() is not None:
            log("FATAL 进程启动即退出 rc=%s（查 %s）" % (proc.returncode, SERVE_LOG))
            return False
        time.sleep(2)
    log("FATAL 60s 内端口未就绪")
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ollama tuned 守护启动器")
    ap.add_argument("--status", action="store_true", help="只查状态，不启动")
    ap.add_argument("--restart", action="store_true", help="强制重启")
    args = ap.parse_args(argv)

    running = port_open()
    procs = ollama_procs()

    if args.status:
        log("STATUS port_open=%s processes=%d" % (running, len(procs)))
        cfg = server_config()
        if cfg:
            log("STATUS config=" + json.dumps(cfg, ensure_ascii=False))
        else:
            log("STATUS 无 serve 日志（可能是桌面 App 起的实例，配置未经本脚本校验）")
        return 0

    if running and not args.restart:
        log("ALREADY RUNNING（不重启）")
        cfg = server_config()
        if cfg and cfg.get("OLLAMA_CONTEXT_LENGTH") not in (None, "65536", "0"):
            log("WARN 当前实例 ctx=%s，不是 tuned 的 65536；需要 tuned 请 --restart"
                % cfg.get("OLLAMA_CONTEXT_LENGTH"))
        return 0

    if args.restart or not running:
        if procs:
            log("KILL 现有 ollama.exe（%d 个）" % len(procs))
            subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"],
                           capture_output=True, timeout=30)
            time.sleep(4)  # 给端口释放与 TIME_WAIT 收敛

    ok = start()
    if ok:
        cfg = server_config()
        log("DONE config=" + json.dumps(cfg, ensure_ascii=False) if cfg else "DONE（无 config 行）")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
