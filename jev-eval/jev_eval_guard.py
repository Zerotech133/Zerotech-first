#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-3（A3 2026-10-07）：Jev 月度回归评测守卫。

读 results/ 最新评测 JSON，判定基线漂移（三个生产组件的阈值锚定在 09-21 基线上，
模型端 jev-latest 若静默升级，题型准确率可能漂移）：
  总准确率 < 0.90 或 noul/choice 分题型 < 0.95 → 飞书告警
  结果文件超过 3 天 → 评测链路故障提示（防评测失败静默）

推送渠道：消息写文件 + backup_alert.py（remind_report.ps1 判例，绕开 PS 5.1 传参剥引号）。
用法：
  python jev_eval_guard.py                # 判定最新一份（无论达标与否都推摘要）
  python jev_eval_guard.py --no-push      # 只打印不推送
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
RESULTS = HERE / "results"
ACC_MIN = 0.90  # 总准确率地板
TYPE_MIN = 0.95  # noul/choice 分题型地板（生产只用这两种题型）
MAX_AGE_DAYS = 3  # 结果文件超 N 天 → 评测链路故障提示


def push(text: str) -> None:
    msg_file = HERE / "review_data" / "lark_eval_msg.txt"
    msg_file.parent.mkdir(exist_ok=True)
    msg_file.write_text(text, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(HERE / "backup_alert.py"), str(msg_file)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    print(f"推送 exit={r.returncode} {r.stdout.strip()[:120]}")


def main() -> int:
    no_push = "--no-push" in args
    files = sorted(RESULTS.glob("result-*.json"))
    if not files:
        msg = (
            "[Jev评测] results/ 无评测结果文件——月度评测任务未产出，请检查 run_eval.ps1"
        )
        print(msg)
        if not no_push:
            push(msg)
        return 1
    latest = files[-1]
    s = json.loads(latest.read_text(encoding="utf-8"))["summary"]
    age = (datetime.now() - datetime.fromisoformat(s["run_at"])).days

    problems = []
    if age > MAX_AGE_DAYS:
        problems.append(f"最新结果已 {age} 天（>{MAX_AGE_DAYS}），评测链路疑似未跑")
    if s["accuracy"] < ACC_MIN:
        problems.append(f"总准确率 {s['accuracy']:.1%} < {ACC_MIN:.0%}")
    by_type = s.get("by_type_accuracy") or {}
    for t in ("noul", "choice"):
        v = by_type.get(t)
        if v is not None and v < TYPE_MIN:
            problems.append(f"{t} 题型 {v:.1%} < {TYPE_MIN:.0%}（生产在用）")

    head = "⚠ [Jev评测] 基线漂移告警" if problems else "[Jev评测] 月度回归正常"
    lines = [
        head,
        f"结果: {latest.name}（{s['run_at'][:16]}，repeats={s['repeats']}）",
        f"总准确率 {s['accuracy']:.1%} / 一致率 {s['self_consistency']:.1%} / "
        f"失败率 {s['error_rate']:.1%} / 延迟中位 {s['latency_ms_median']}ms",
        f"分题型: {json.dumps(by_type, ensure_ascii=False)}",
    ]
    if s.get("high_conf_wrong_ids"):
        lines.append(f"高置信答错: {s['high_conf_wrong_ids']}")
    lines += [f"  - {p}" for p in problems]
    msg = "\n".join(lines)
    print(msg)
    if not no_push:
        push(msg)
    return 1 if problems else 0


args = sys.argv[1:]

if __name__ == "__main__":
    sys.exit(main())
