# -*- coding: utf-8 -*-
"""每日投研派活的日志包装器（2026-11-16 WO-DISPATCH-DAILY）。

## 为什么需要它

计划任务用 `pythonw.exe` 拉起，**没有控制台也没有日志**——如果某天失败（网络抖动、池接口挂了、
模型限流），事后无从查起。`hidden_launch.py` 只负责无窗口启动，不做重定向。

这个包装器做三件事：

1. 用带缓冲的方式运行 `research_dispatch.py`，把 **stdout/stderr 全量落盘**到
   `logs/dispatch/<YYYYMMDD>.log`（按天分文件，追加）；
2. 在日志里写清起止时间与退出码——便于判断"跑了没跑"和"跑成没跑成"；
3. 原样返回子进程退出码，让 Task Scheduler 的 Last Result 语义保持正确。

    pythonw.exe -X utf8 hidden_launch.py python.exe tools/daily_dispatch_wrapper.py [args...]
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "research_dispatch.py"
LOGDIR = REPO / "logs" / "dispatch"


def main() -> int:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now()
    logfile = LOGDIR / ("%s.log" % stamp.strftime("%Y%m%d"))
    argv = [sys.executable, "-X", "utf8", "-u", str(SCRIPT), *sys.argv[1:]]

    with logfile.open("ab") as fh:            # 二进制追加：原样落盘，不做二次解码
        def w(text: str) -> None:
            fh.write(text.encode("utf-8"))
            fh.flush()

        w("\n" + "=" * 78 + "\n")
        w("START %s\n" % stamp.strftime("%Y-%m-%d %H:%M:%S"))
        w("CMD   %s\n" % " ".join(argv[:3] + ["…"] + argv[4:]))
        w("-" * 78 + "\n")
        try:
            # 不指定 encoding / text：直接拿字节原样写入，避免 UTF-8 → GBK 之类的解码错乱
            proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, cwd=str(REPO))
            if proc.stdout:
                fh.write(proc.stdout)
            rc = proc.returncode
        except Exception as exc:  # noqa: BLE001
            w("LAUNCH_FAILED %s: %s\n" % (type(exc).__name__, exc))
            rc = 3
        end = dt.datetime.now()
        w("-" * 78 + "\n")
        w("END   %s  exit=%d  elapsed=%.1fs\n"
          % (end.strftime("%Y-%m-%d %H:%M:%S"), rc, (end - stamp).total_seconds()))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
