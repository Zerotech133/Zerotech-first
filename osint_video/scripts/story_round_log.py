"""轮次台账查询：看每轮跑了什么、和上一轮比是否正常。

`story_group_run.py` 每轮往台账追加一行；本脚本负责把它读出来给人看。
两者分开，是因为**写台账的人（cron）与看台账的人（人）关注点不同**：
写侧只要一行不出错，读侧要的是表格、趋势与体检结论。

## 用法

::

    python scripts/story_round_log.py list            # 最近 10 轮一览
    python scripts/story_round_log.py list --last 3 --json
    python scripts/story_round_log.py show            # 最新一轮的完整记录
    python scripts/story_round_log.py check           # 对最新一轮重跑体检

``check`` 的退出码：0=无异常，1=有 warn，2=有 fail。可以直接挂到监控上。

## 为什么 list 要同时打印"前提"

台账里存的是"库 + 窗口 + limit + 开关"，不是只有数字。只印事件数的话，人会
拿 ``osint`` 与 ``osint_calib`` 两轮的差当趋势看——而那是换了库，不是聚类变了。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.logging_setup import setup_logging  # noqa: E402
from config.settings import get_settings  # noqa: E402
from knowledge import run_ledger as rl  # noqa: E402

_LEVEL_ORDER = {"ok": 0, "warn": 1, "fail": 2}


def _ledger_path(args) -> Path:
    return Path(args.ledger or get_settings().story_run_ledger)


def _fmt(value) -> str:
    """表格里把 None 印成 ``—``，避免和数字 0 混起来。

    整数值的 float 印成整数：``14.0`` 在 4 字宽列里会溢出，而窗口天数本来就是整数。
    """
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _cmd_list(args) -> int:
    if args.real_only:
        # 空转轮次可能占绝大多数（调度越频繁越明显），先过滤再取最后 N 条，
        # 否则 --last 10 会全是空转，看不到任何真实轮次。
        records = [r for r in rl.load_rounds(_ledger_path(args)) if not r.get("noop")][
            -args.last :
        ]
    else:
        records = rl.load_rounds(_ledger_path(args), limit=args.last)
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    if not records:
        print(f"台账为空或不存在：{_ledger_path(args)}")
        print("首轮跑完 story_group_run.py 之后才会有记录。")
        return 0

    header = (
        f"{'记录时刻(UTC)':20s} {'库':10s} {'kind':>5s} {'窗':>4s} {'dry':>4s} "
        f"{'跨窗':>5s} {'新建':>4s} {'复用':>4s} {'跨窗合并':>8s} "
        f"{'临界带':>6s} {'积压':>4s}"
    )
    print(header)
    print("-" * len(header))
    for row in records:
        ctx = row.get("context") or {}
        m = row.get("metrics") or {}
        print(
            f"{str(row.get('recorded_at'))[:19]:20s} "
            f"{str(ctx.get('db_name'))[:10]:10s} "
            f"{('noop' if row.get('noop') else 'real'):>5s} "
            f"{_fmt(ctx.get('window_days')):>4s} "
            f"{('Y' if ctx.get('dry_run') else 'N'):>4s} "
            f"{('on' if ctx.get('cross_window') else 'off'):>5s} "
            f"{_fmt(m.get('stories_created')):>4s} "
            f"{_fmt(m.get('stories_reused')):>4s} "
            f"{_fmt(m.get('cross_window_merged')):>8s} "
            f"{_fmt(m.get('borderline_rechecked')):>6s} "
            f"{_fmt(m.get('review_pending')):>4s}"
        )

    health = rl.ledger_health(_ledger_path(args))
    noop = sum(1 for r in rl.load_rounds(_ledger_path(args)) if r.get("noop"))
    print()
    print(
        f"共 {health['parsed']} 轮（其中空转 {noop} 轮）/ 台账 {health['lines']} 行"
        + (f"（**{health['corrupt']} 行无法解析**）" if health["corrupt"] else "")
    )
    if health["corrupt"]:
        # 坏行不阻止读历史，但要明确指出来：它意味着某次写坏或手工编辑过。
        print("坏行已被跳过；如非手工编辑，检查是否有并发写入或磁盘写入中断。")
    return 0


def _cmd_show(args) -> int:
    records = rl.load_rounds(_ledger_path(args))
    if not records:
        print(f"台账为空或不存在：{_ledger_path(args)}")
        return 0
    latest = records[-1]
    print(json.dumps(latest, ensure_ascii=False, indent=2))
    print()
    findings = rl.check_round(latest, records[:-1])
    _print_findings(findings)
    return 0


def _cmd_check(args) -> int:
    records = rl.load_rounds(_ledger_path(args))
    if not records:
        print(f"台账为空或不存在：{_ledger_path(args)}")
        print("没有可体检的轮次。")
        return 0
    findings = rl.check_round(records[-1], records[:-1])
    _print_findings(findings)
    return max((_LEVEL_ORDER[f.level] for f in findings), default=0)


def _print_findings(findings) -> None:
    for finding in findings:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}[finding.level]
        print(f"[{mark}] {finding.code}: {finding.message}")
        if finding.action:
            print(f"       → {finding.action}")
    worst = max((_LEVEL_ORDER[f.level] for f in findings), default=0)
    label = {0: "无异常", 1: "有需要关注的项", 2: "有结论不可信的项"}[worst]
    print()
    print(f"体检结论：{label}")


def main() -> int:
    parser = argparse.ArgumentParser(description="查看轮次台账与跨轮体检结论")
    parser.add_argument(
        "--ledger",
        default=None,
        help=f"台账路径，默认取配置项 STORY_RUN_LEDGER（{rl.DEFAULT_LEDGER}）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="按时间列出轮次")
    p_list.add_argument("--last", type=int, default=10, help="只看最近 N 轮，默认 10")
    p_list.add_argument(
        "--real-only",
        action="store_true",
        help="只看真正算了东西的轮次（跳过 units_scanned=0 的空转）",
    )
    p_list.add_argument("--json", action="store_true", help="输出机读 JSON")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", help="最新一轮的完整记录与体检")
    p_show.set_defaults(func=_cmd_show)

    p_check = sub.add_parser("check", help="对最新一轮重跑体检（可用于监控）")
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args()
    setup_logging()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
