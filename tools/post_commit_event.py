# -*- coding: utf-8 -*-
"""跨 Windows/Git shell 的 post-commit 事件回流适配器。

Git hook 只调用本文件，任务号解析和提交信息读取全部由 Python 完成，
避免依赖 grep、head 等在某些 Git for Windows 环境中不可用的外部命令。
事件上报失败时返回 0，不阻断提交。
"""

from __future__ import annotations

import sys

try:  # 2026-10-26 K-ORG-20261026-ENCPROT: non-UTF8 console printing CJK/checkmark
    # raises UnicodeEncodeError and can crash merge/hook paths; degrade to UTF-8 output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - non-TTY/old interpreter: ignore
    pass
import os
import re
import subprocess
from pathlib import Path

from events import emit


ROOT = Path(__file__).resolve().parents[1]
TASK_PATTERN = re.compile(r"K-[A-Z0-9]+-[0-9]+")


def extract_task_id(subject: str) -> str:
    match = TASK_PATTERN.search(subject or "")
    return match.group(0) if match else ""


def _git_log(format_string: str) -> str:
    result = subprocess.run(
        ["git", "log", "-1", f"--format={format_string}"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
        check=False,
    )
    return (result.stdout or "").strip()


def main() -> int:
    try:
        subject = _git_log("%s")
        commit = _git_log("%h %s")
        emit(
            "commit",
            task=extract_task_id(subject),
            agent=os.environ.get("DEVTOOL_AGENT", "dev"),
            detail=commit,
            ide=os.environ.get("DEVTOOL_IDE", ""),
            model=os.environ.get("DEVTOOL_MODEL", ""),
        )
    except Exception:
        # 事件层永不阻塞开发动作；hook 需要始终成功返回。
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
