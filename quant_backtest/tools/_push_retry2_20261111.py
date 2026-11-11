# -*- coding: utf-8 -*-
"""2026-11-11 push 重试器 v2（吸收 v1 教训）。

v1 死因：push 子进程 120s 超时未捕获 TimeoutExpired → 整个循环崩掉。
网络实况：TCP 探测时通时断（连通≠能完成 TLS+HTTP），push 常被 reset。
v2 改进：
  * TimeoutExpired / 一切异常都捕获，循环不退出；
  * push 失败退避 90s，探测失败退避 30s；
  * 上限 60 分钟；成功即 exit 0。
输出仍落 logs/push_retry_20261111.log。
"""
import os
import socket
import subprocess
import sys
import time

REPO = "C:/aios"
LOG = os.path.join(REPO, "quant_backtest", "logs", "push_retry_20261111.log")
ENV = {k: v for k, v in os.environ.items()
       if k.lower() not in ("https_proxy", "http_proxy")}
DEADLINE = time.time() + 60 * 60


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def probe():
    try:
        s = socket.create_connection(("github.com", 443), timeout=4)
        s.close()
        return True
    except Exception:
        return False


def push():
    try:
        r = subprocess.run(
            ["git", "-c", "http.proxy=", "-c", "https.proxy=",
             "push", "origin", "master:master", "master:main",
             "repo-layout-20261111"],
            cwd=REPO, env=ENV, capture_output=True, text=True, timeout=90,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " | ")
        log("push rc=%d %s" % (r.returncode, out[:200]))
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log("push TimeoutExpired(90s)，继续退避")
        return False
    except Exception as e:
        log("push exc %s: %s" % (type(e).__name__, str(e)[:120]))
        return False


log("=== v2 启动（v1 已死于未捕获超时）===")
n = 0
while time.time() < DEADLINE:
    n += 1
    if probe():
        log("round %d: 探测通" % n)
        if push():
            log("SUCCESS：推送完成")
            sys.exit(0)
        time.sleep(90)
    else:
        if n % 5 == 1:
            log("round %d: 探测失败" % n)
        time.sleep(30)
log("TIMEOUT：60 分钟未成功，保持挂账")
sys.exit(1)
