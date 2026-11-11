# -*- coding: utf-8 -*-
"""2026-11-11 push 后台重试器（选项 A 裁定提交 dac116918 + tag）。

网络实况：两个本地代理（50908/53138）均已死；GitHub 直连间歇可用
（3 次探测 1 次 TCP+TLS+HTTP 401 全通，另 2 次 TCP 超时）——典型
间歇性窗口。策略：循环「探测 → 窗口期内立即 push」，成功即退出；
总时长上限 ~25 分钟，每轮间隔 20s（探测失败时）/60s（push 失败时）。
所有输出落 logs/push_retry_20261111.log（铁律：长跑输出必须落文件）。
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
DEADLINE = time.time() + 25 * 60


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
    r = subprocess.run(
        ["git", "-c", "http.proxy=", "-c", "https.proxy=",
         "push", "origin", "master:master", "master:main",
         "repo-layout-20261111"],
        cwd=REPO, env=ENV, capture_output=True, text=True, timeout=120,
    )
    out = (r.stdout or "") + (r.stderr or "")
    log("push rc=%d out=%s" % (r.returncode, out.strip().replace("\n", " | ")[:200]))
    return r.returncode == 0


log("=== 启动：目标 dac116918 + tag repo-layout-20261111 ===")
n = 0
while time.time() < DEADLINE:
    n += 1
    if probe():
        log("round %d: 探测通，尝试 push" % n)
        if push():
            log("SUCCESS：推送完成，退出")
            sys.exit(0)
        time.sleep(60)
    else:
        log("round %d: 探测失败，20s 后重试" % n)
        time.sleep(20)
log("TIMEOUT：25 分钟窗口内未成功，保持挂账（本地领先 origin 1 提交 + 1 tag）")
sys.exit(1)
