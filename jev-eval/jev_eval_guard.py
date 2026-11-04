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
INFRA_ERROR_RATE = 0.5  # 请求失败率 ≥50% 视为接口故障，不作漂移判定（2026-11-05）
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


def _pick_latest(files: list[Path]) -> tuple[Path, dict, list[str], bool]:
    """按 summary.run_at 取最新结果，并优先正式跑（repeats>=3）。

    2026-11-05 修：原实现取字典序最后一个 result-*.json，实测最新的是
    `result-20261014-154128.json`（repeats=1、by_type 只有 score 的临时跑），
    被当成正式结果判成 33.3% 准确率，制造假漂移告警。改为：
      - 时间序以 run_at 为准（缺 run_at 退回文件 mtime）；
      - 只在 repeats>=3 的正式跑里取最新；一份都没有 → 用最新一份并标注，
        且跳过准确率/题型阈值判定（临时跑不构成基线）。
    返回 (path, summary, notes, formal)。
    """
    loaded: list[tuple[datetime, Path, dict]] = []
    for f in files:
        try:
            s = json.loads(f.read_text(encoding="utf-8")).get("summary") or {}
        except (OSError, ValueError):
            continue
        ts = s.get("run_at") or ""
        try:
            key = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            try:
                key = datetime.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue
        loaded.append((key, f, s))
    if not loaded:
        # 全部不可解析：退回旧行为，保证不崩
        f = files[-1]
        return f, json.loads(f.read_text(encoding="utf-8"))["summary"], [], False

    loaded.sort(key=lambda x: x[0])
    formal_items = [x for x in loaded if (x[2].get("repeats") or 0) >= 3]
    notes: list[str] = []
    if formal_items:
        key, path, summary = formal_items[-1]
        skipped = len(loaded) - len(formal_items)
        newest = loaded[-1]
        if newest[0] > key:
            notes.append(
                f"忽略 {skipped} 份非正式结果（repeats<3），已取最近正式跑 "
                f"{path.name}（{summary.get('run_at', '')[:16].replace('T', ' ')}）；"
                f"目录内更新的 {newest[1].name} 为临时跑，不参与判定"
            )
        return path, summary, notes, True

    key, path, summary = loaded[-1]
    return path, summary, notes, False


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

    latest, summary, pick_notes, formal = _pick_latest(files)
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
    # 2026-11-05：接口层故障（451 地区屏蔽 / 断网 / 5xx）会让准确率变成 0%，
    # 这不是模型漂移，判成漂移会发假告警（当日实测：typesafe 直连全 30 题 451）。
    err_rate = summary.get("error_rate")
    infra = bool(
        formal
        and (
            (err_rate is not None and err_rate >= INFRA_ERROR_RATE)
            or (summary.get("total_tokens") == 0 and (accuracy or 0) == 0)
        )
    )
    if not formal:
        problems.append(
            "results 目录无正式评测结果（repeats>=3）；最新一份 repeats=%s，"
            "属临时跑，不参与基线判定。补救: %s"
            % (summary.get("repeats"), remedy_cmd())
        )
    if infra:
        problems.append(
            f"评测未能完成：请求失败率 {err_rate if err_rate is not None else 1:.1%}"
            f"（≥{INFRA_ERROR_RATE:.0%}）、token 用量 {summary.get('total_tokens')} —— "
            f"属接口不可达/被屏蔽，**不作漂移判定**；先验端点 "
            f"jev_eval.py --probe-endpoint，再重跑 {remedy_cmd()}"
        )
    elif formal and accuracy is not None and accuracy < ACC_MIN:
        problems.append(f"总准确率 {accuracy:.1%} < {ACC_MIN:.0%}")
    by_type = summary.get("by_type_accuracy") or {}
    for t in ("noul", "choice"):
        v = by_type.get(t)
        if formal and not infra and v is not None and v < TYPE_MIN:
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
    lines += [f"  · {n}" for n in pick_notes]
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
