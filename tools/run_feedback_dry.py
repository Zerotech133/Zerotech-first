# -*- coding: utf-8 -*-
"""L0 反馈环干跑的计划任务 runner（2026-11-08）。

为什么存在：计划任务不经 shell，Action 里写 `>> dry.log 2>&1` 的 shell
重定向语法会被当成脚本参数，stdout 原地蒸发（08:35 首跑事故：任务退出码 0
但 dry.log 为空）。本 runner 用 Python 自己打开日志文件做 append，彻底避开
shell 重定向，计划任务 Action 只需要干净的一行 python 命令。

计划任务 zeroq-feedback-dry 的 Action 应为：
    "C:\\Program Files\\Python311\\python.exe" -X utf8
    "C:\\项目\\老田的AI助手\\tools\\run_feedback_dry.py"
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "intel_collector" / "examples" / "feedback_dry.py"
LOG = REPO / "reports" / "feedback_dry_daily" / "dry.log"
PY = Path(r"C:\Program Files\Python311\python.exe")

def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = "\n===== run_feedback_dry: %s =====\n" % __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a", encoding="utf-8", errors="replace") as f:
        f.write(stamp)
        f.flush()
        r = subprocess.run(
            [str(PY), "-X", "utf8", str(SCRIPT)],
            stdout=f, stderr=subprocess.STDOUT,
            cwd=str(REPO),
        )
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())
