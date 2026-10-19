# -*- coding: utf-8 -*-
"""观察点复查计划任务 wrapper（ObservePointReviewOnce，OneTime 17:50）。

职责：跑 observe_point_review.py → 输出落盘 C:\Temp → 结论行追加公告板
（kg_sync_task.log），使 17:50 后任意会话/老田无需在场即得复查结论。
--selftest：只落盘不写公告板（验证用，防污染）。

退出码=执行器退出码（0=可开M4 / 1=红顺延 / 2=前置未到）。
"""
import subprocess
import sys
from datetime import datetime

PY = sys.executable
EXECUTOR = r"C:\aios\agent_platform\observe_point_review.py"
BULLETIN = r"C:\Temp\kg_sync_task.log"
OUT_FMT = r"C:\Temp\observe_point_review_%Y-%m-%d.log"

SELFTEST = "--selftest" in sys.argv

# 2026-10-19 审计修复（P2-4）：执行器超时不得静默——构造 RED 结论照常落盘+公告板
try:
    r = subprocess.run([PY, "-X", "utf8", EXECUTOR],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=600)
except subprocess.TimeoutExpired as exc:
    r = subprocess.CompletedProcess(
        args=exc.args, returncode=124,
        stdout="结论: 复查执行器超时 600s（ObservePointReviewOnce 包装层判定）"
               "→ 按 SOP 异常矩阵处理并通报\n",
        stderr=str(exc)[:200])
out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
out_path = datetime.now().strftime(OUT_FMT)
with open(out_path, "a", encoding="utf-8") as f:
    f.write(out.rstrip() + "\n")

concl = [l for l in out.splitlines() if l.startswith("结论:")]
stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
if not SELFTEST and concl:
    with open(BULLETIN, "a", encoding="utf-8") as f:
        f.write("[{ts}] 观察点复查自动执行（ObservePointReviewOnce，"
                "exit={rc}）：{c} | 全量输出={p}\n".format(
                    ts=stamp, rc=r.returncode, c=concl[-1][:200], p=out_path))
print(out)
sys.exit(r.returncode)
