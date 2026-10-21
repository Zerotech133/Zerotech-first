# -*- coding: utf-8 -*-
"""军团指挥台一键启动器（WO-LEGION-S28）。

用途：老田/工位随手拉起 8796 指挥台，不必记命令、不重复起实例。

行为：
  1. 先探 /api/health；已在跑 → 打印访问地址直接退出（不重复起，避免端口冲突）
  2. 未跑 → 前台启动 serve.py（Ctrl+C 即停）；加 --bg 则后台分离启动
  3. 启动后等就绪并打印地址；失败打印明确原因（含端口占用/日志路径）

用法：
  python ops/launch_legion_console.py              # 前台（推荐：看到日志，Ctrl+C 停）
  python ops/launch_legion_console.py --bg         # 后台分离（脱离当前 shell）
  python ops/launch_legion_console.py --check      # 只探活，不起服务
  python ops/launch_legion_console.py --port 8798  # 换端口

说明：本机执行沙箱会回收会话遗留进程，长期常驻请用登录自启
（HKCU Run 项 LegionCommandConsole，注销重登触发）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SERVE = _REPO / "agent_platform" / "legion_command" / "serve.py"


def probe(host: str, port: int, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (host, port), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def url(host: str, port: int) -> str:
    return "http://%s:%d/" % (host, port)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="legion command console launcher")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--bg", action="store_true", help="后台分离启动")
    ap.add_argument("--check", action="store_true", help="只探活")
    ap.add_argument("--wait", type=float, default=6.0, help="就绪等待秒数")
    args = ap.parse_args(argv)

    alive = probe(args.host, args.port)
    if alive:
        print("已在运行：%s（%s）" % (url(args.host, args.port), alive.get("service", "?")))
        return 0
    if args.check:
        print("未运行：%s" % url(args.host, args.port))
        return 1
    if not _SERVE.exists():
        print("serve.py 不存在：%s" % _SERVE, file=sys.stderr)
        return 2

    cmd = [sys.executable, "-u", str(_SERVE), "--host", args.host, "--port", str(args.port)]
    if args.bg:
        subprocess.Popen(cmd, cwd=str(_SERVE.parent.parent), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        print("已后台启动，等待就绪…")
    else:
        print("启动中（Ctrl+C 停止）：%s" % url(args.host, args.port))

    deadline = time.time() + args.wait
    while time.time() < deadline:
        if probe(args.host, args.port, timeout=0.8):
            print("就绪：%s" % url(args.host, args.port))
            print("  GET /api/snapshot   军团快照")
            print("  GET /api/evolution  进化层视图")
            return 0
        if not args.bg:
            break
        time.sleep(0.5)

    if args.bg:
        print("启动超时：%s 未就绪（查看日志或改端口 --port）" % url(args.host, args.port),
              file=sys.stderr)
        return 3
    try:
        return subprocess.call(cmd, cwd=str(_SERVE.parent.parent))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
