# -*- coding: utf-8 -*-
"""plan_status_lint.py — docs/plans 计划 STATUS 覆盖 lint（只读，2026-11-01）。

口径与 `tools/daily_three_ledger.py` 的计划账**完全一致**（复用其 `scan_plans`，不另立第二套）：
  · 扫描 `docs/plans/**/*.md`；
  · 排除**顶层**说明/生成台账 `README.md`、`PLAN_REGISTER_20261031.md`、
    `PLAN_REGISTER_ALL_20261031.md`（子目录中的 README 等仍按受治理计划文档计入）；
  · `> STATUS:` 行必须出现在文件前 6000 字符内；
  · 无 STATUS → `unspecified`（计划账 fail）。

用途：日终/合并前快速 lint（**不自动写文件**）；未标计划由 owner 按内容手工补标，
本工具只负责发现与输出，禁止批量套模板。

用法
----
    python -X utf8 tools/plan_status_lint.py                 # 人类摘要
    python -X utf8 tools/plan_status_lint.py --json          # 机读 JSON
    python -X utf8 tools/plan_status_lint.py --plans-dir D:/x/docs/plans

退出码：0=无未标（计划账 pass）；1=有未标（fail）；2=参数/目录不可读。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))
from daily_three_ledger import scan_plans  # noqa: E402  复用同一口径，避免第二套账

SCHEMA = "plan-status-lint/v1"
DEFAULT_PLANS_DIR = Path(__file__).resolve().parents[1] / "docs" / "plans"


def lint(plans_dir: Path) -> dict:
    """执行 lint，返回报告 dict（只读，不写文件）。"""
    plans_dir = Path(plans_dir)
    if not plans_dir.is_dir():
        raise FileNotFoundError("计划目录不存在：%s" % plans_dir)
    result = scan_plans(plans_dir)
    unspecified = result.get("unspecified", 0)
    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "plans_dir": str(plans_dir),
        "files_total": result.get("files_total", 0),
        "plan_docs": result.get("plan_docs", 0),
        "with_status": result.get("with_status", 0),
        "unspecified": unspecified,
        "unspecified_files": result.get("unspecified_files", []),
        "coverage_pct": result.get("coverage_pct", 0.0),
        "by_status": result.get("by_status", {}),
        "status": "fail" if unspecified else "pass",
        "note": "只读 lint；未标计划由 owner 按内容手工补标，本工具不写文件、不批量伪造",
    }


def format_human(report: dict) -> str:
    lines = [
        "计划 STATUS lint（只读）｜目录：%s" % report["plans_dir"],
        "  files_total=%d  plan_docs=%d  with_status=%d  unspecified=%d  coverage=%.2f%%"
        % (report["files_total"], report["plan_docs"], report["with_status"],
           report["unspecified"], report["coverage_pct"]),
    ]
    files = report.get("unspecified_files") or []
    if files:
        lines.append("  未标文件（%d，由 owner 按内容补标）：" % report["unspecified"])
        shown = files[:30]
        for name in shown:
            lines.append("    - %s" % name)
        if len(files) > len(shown):
            lines.append("    …（另 %d 份，见 --json）" % (len(files) - len(shown)))
    lines.append("  状态分布：%s" % json.dumps(report.get("by_status") or {}, ensure_ascii=False))
    lines.append("  结论：%s（%d 份未标；本工具不写文件）"
                 % ("PASS" if report["status"] == "pass" else "FAIL", report["unspecified"]))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="plan_status_lint.py",
        description="docs/plans 计划 STATUS 覆盖 lint（只读；口径同 daily_three_ledger 计划账）。",
        epilog="退出码：0=无未标；1=有未标；2=参数/目录错误。",
    )
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--plans-dir", default="", help="计划目录（默认 <repo>/docs/plans）")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    plans_dir = Path(args.plans_dir) if args.plans_dir else DEFAULT_PLANS_DIR
    try:
        report = lint(plans_dir)
    except FileNotFoundError as exc:
        print("ERROR: %s" % exc)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_human(report))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
