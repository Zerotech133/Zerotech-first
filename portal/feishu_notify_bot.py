# -*- coding: utf-8 -*-
"""零界·通知 bot 常驻壳入口（zb.ops.notify，2026-10-25 拉起）。

定位：本角色是 **outbound_only**（registry: events=[], inputs=none）——
出向统一走 8097 闸门；本进程只是"待命 WS 壳"，用于：
  1) 提供管理台可读的心跳（logs/feishu_heartbeat_zb.ops.notify.txt）；
  2) 验证该 app 的 WS 长连接/凭据可用；
  3) 将来若给该角色加事件订阅，无需再改入口。

启动（隐藏窗口）：
  C:\\aios\\CowAgent\\venv\\Scripts\\python.exe -u -X utf8 C:\\aios\\portal\\feishu_notify_bot.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from feishu_bot_base import Shell  # noqa: E402

ROLE = "zb.ops.notify"


def main():
    Shell(ROLE).run()


if __name__ == "__main__":
    main()
