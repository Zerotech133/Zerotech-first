# -*- coding: utf-8 -*-
"""8094 零界驾驶舱看门狗（K-STOCK-077 P-1）。

背景：2026-10-10 8094 曾无监听挂死（挂因未定性），驾驶舱无自愈能力。
配方沿 restart_portal_api.py 教训：
  - 无代理探针（系统代理 http_proxy 会吃本地请求→假 502/假绿）
  - 复活走计划任务 Start-ScheduledTask（沙箱/会话派生的驻进程会被连坐收割，
    唯一稳定宿主是计划任务 'AI工作站零界驾驶舱8094'）
  - 探针 x2 间隔 3s 都失败才动作（防抖）；拉起后最长等 90s 验证

注册（计划任务每 5 分钟跑一次本脚本，幂等）：
  AI工作站8094看门狗 -> python ops/keepalive_8094.py

日志：C:/Temp/watchdog_8094.log（动作留痕，绿了也记一行心跳可选——默认只记动作）
"""
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

PORT = 8094
TASK = "AI工作站零界驾驶舱8094"
LOG = r"C:\Temp\watchdog_8094.log"
# 探针打根路径即可（home_server 无 /health；能回任何 HTTP 响应=进程+socket 活着）
URL = f"http://127.0.0.1:{PORT}/"

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="")


def alive():
    """任何 HTTP 响应（含 4xx/5xx）= 服务活着；连接拒绝/超时=死。"""
    try:
        op.open(URL, timeout=4)
        return True
    except Exception as e:  # noqa: BLE001
        # HTTPError 说明服务在响应（socket 通），也算活
        if type(e).__name__ == "HTTPError":
            return True
        return False


def main():
    if alive():
        sys.exit(0)  # 正常：静默退出，不刷日志
    time.sleep(3)
    if alive():
        sys.exit(0)  # 抖动：第二次探活了，不动
    log("DOWN x2 -> Start-ScheduledTask %s" % TASK)
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Start-ScheduledTask -TaskName '%s'" % TASK],
        capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        log("Start-ScheduledTask rc=%s err=%s" % (r.returncode, (r.stderr or "")[:200]))
    ok = False
    for i in range(30):  # 最长 90s
        time.sleep(3)
        if alive():
            log("REVIVED after ~%ds" % ((i + 1) * 3))
            ok = True
            break
    if not ok:
        log("REVIVE_FAIL（计划任务拉起后 90s 仍无监听——需人工看任务定义/服务日志）")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
