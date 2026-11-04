#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-3（A3 2026-10-07）/ JEV-EVALCOV-20261105：Jev 回归评测守卫。

读 results/ 最新评测 JSON，判定基线漂移（三个生产组件的阈值锚定在 09-21 基线上，
模型端 jev-latest 若静默升级，题型准确率可能漂移）：
  总准确率 < 0.90 或 noul/choice 分题型 < 0.95 → 飞书告警
  结果文件超过周期阈值 → 评测链路故障提示（防评测失败静默）

周期口径（JEV-EVALCOV-20261105 修复）：
  需求是月度回归，但旧版硬编码 MAX_AGE_DAYS=3，月度跑成功后第 4 天起天天告警。
  现按 --cadence 取阈值：monthly=35 天（31 天月长 + 补跑余量），weekly=3 天。
  默认 monthly（与生产计划任务的月度口径一致）。

推送渠道：消息写文件 + backup_alert.py（remind_report.ps1 判例，绕开 PS 5.1 传参剥引号）。
用法：
  python jev_eval_guard.py                      # 月度口径判最新一份（无论达标与否都推摘要）
  python jev_eval_guard.py --cadence weekly     # 周度口径（3 天超龄）
  python jev_eval_guard.py --no-push            # 只打印不推送
exit: 0 正常 / 1 有告警（含超龄、无结果文件）/ 2 参数错误
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
MAX_AGE_DAYS = 3  # 兼容旧名：weekly 口径阈值；实际取值见 CADENCE_MAX_AGE
DEFAULT_CADENCE = "monthly"
# 超龄阈值（天）：monthly 容忍 31 天月长 + 补跑；weekly 保持 3 天
CADENCE_MAX_AGE = {"monthly": 35, "weekly": 3}
CADENCE_LABEL = {"monthly": "月度", "weekly": "周度"}

USAGE = (
    "用法: jev_eval_guard.py [--cadence monthly|weekly] [--no-push] "
    "[--results-dir DIR]"
)


def max_age_days(cadence: str) -> int:
    """周期 → 超龄阈值（天）。未知周期抛 ValueError。"""
    try:
        return CADENCE_MAX_AGE[cadence]
    except KeyError:
        raise ValueError(
            f"未知 cadence={cadence!r}，可选 {sorted(CADENCE_MAX_AGE)}"
        ) from None


def parse_args(argv: list[str]) -> dict:
    """手写解析：仓库 PS 5.1 调用链靠空格传参，不引入 argparse 行为差异。"""
    cfg = {
        "cadence": DEFAULT_CADENCE,
        "no_push": False,
        "results_dir": str(RESULTS),
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-push":
            cfg["no_push"] = True
        elif a in ("-h", "--help"):
            cfg["help"] = True
        elif a.startswith("--cadence="):
            cfg["cadence"] = a.split("=", 1)[1]
        elif a == "--cadence":
            i += 1
            if i >= len(argv):
                raise ValueError("--cadence 缺少取值")
            cfg["cadence"] = argv[i]
        elif a.startswith("--results-dir="):
            cfg["results_dir"] = a.split("=", 1)[1]
        elif a == "--results-dir":
            i += 1
            if i >= len(argv):
                raise ValueError("--results-dir 缺少取值")
            cfg["results_dir"] = argv[i]
        else:
            raise ValueError(f"未知参数 {a!r}")
        i += 1
    if cfg.get("help"):
        return cfg
    cfg["cadence"] = cfg["cadence"].strip().lower()
    # 提前校验，避免跑了整轮评测才发现参数错
    max_age_days(cfg["cadence"])
    return cfg


def remedy_cmd() -> str:
    """补救命令：优先月度入口脚本，缺失则退回直接评测命令。"""
    script = HERE / "jev_monthly_eval.ps1"
    if script.exists():
        return (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass "
            f"-File {script}"
        )
    return (
        f'"{sys.executable}" -X utf8 {HERE / "jev_eval.py"} --repeats 3'
    )


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


def check(
    results_dir: Path | str | None = None,
    cadence: str = DEFAULT_CADENCE,
    now: datetime | None = None,
) -> dict:
    """纯判定，不推送。返回 {code, msg, problems, age_days, max_age, latest}。"""
    cadence = (cadence or DEFAULT_CADENCE).strip().lower()
    limit = max_age_days(cadence)
    label = CADENCE_LABEL.get(cadence, cadence)
    rdir = Path(results_dir) if results_dir else RESULTS
    now = now or datetime.now()

    files = sorted(rdir.glob("result-*.json"))
    if not files:
        msg = (
            f"[Jev评测] {rdir} 无评测结果文件——{label}评测任务未产出，"
            f"请检查 jev_monthly_eval.ps1 / run_eval.ps1\n"
            f"补救: {remedy_cmd()}"
        )
        return {
            "code": 1,
            "msg": msg,
            "problems": ["无评测结果文件"],
            "age_days": None,
            "max_age": limit,
            "cadence": cadence,
            "latest": None,
        }

    latest = files[-1]
    summary = json.loads(latest.read_text(encoding="utf-8"))["summary"]
    problems: list[str] = []
    age_days = None
    run_at = summary.get("run_at") or ""
    try:
        age_days = (now - datetime.fromisoformat(run_at)).days
    except (TypeError, ValueError):
        problems.append(f"结果 run_at 无法解析: {run_at!r}")

    if age_days is not None and age_days > limit:
        problems.append(
            f"最新结果已 {age_days} 天（>{limit} 天）："
            f"上次评测 {run_at[:16].replace('T', ' ')}，"
            f"期望周期 {label}（≤{limit} 天），"
            f"补救命令 {remedy_cmd()}"
        )
    accuracy = summary.get("accuracy")
    if accuracy is not None and accuracy < ACC_MIN:
        problems.append(f"总准确率 {accuracy:.1%} < {ACC_MIN:.0%}")
    by_type = summary.get("by_type_accuracy") or {}
    for t in ("noul", "choice"):
        v = by_type.get(t)
        if v is not None and v < TYPE_MIN:
            problems.append(f"{t} 题型 {v:.1%} < {TYPE_MIN:.0%}（生产在用）")

    head = "⚠ [Jev评测] 基线漂移告警" if problems else f"[Jev评测] {label}回归正常"
    lines = [
        head,
        f"结果: {latest.name}（{run_at[:16].replace('T', ' ')}，"
        f"周期={cadence}，年龄={age_days}天/{limit}天，"
        f"repeats={summary.get('repeats')}）",
        f"总准确率 {summary.get('accuracy', 0):.1%} / 一致率 "
        f"{summary.get('self_consistency', 0):.1%} / "
        f"失败率 {summary.get('error_rate', 0):.1%} / "
        f"延迟中位 {summary.get('latency_ms_median')}ms",
        f"分题型: {json.dumps(by_type, ensure_ascii=False)}",
    ]
    if summary.get("high_conf_wrong_ids"):
        lines.append(f"高置信答错: {summary['high_conf_wrong_ids']}")
    lines += [f"  - {p}" for p in problems]
    return {
        "code": 1 if problems else 0,
        "msg": "\n".join(lines),
        "problems": problems,
        "age_days": age_days,
        "max_age": limit,
        "cadence": cadence,
        "latest": latest.name,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        cfg = parse_args(argv)
    except ValueError as e:
        print(f"{e}\n{USAGE}", file=sys.stderr)
        return 2
    if cfg.get("help"):
        print(USAGE)
        return 0

    res = check(cfg["results_dir"], cfg["cadence"])
    print(res["msg"])
    if not cfg["no_push"]:
        push(res["msg"])
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
