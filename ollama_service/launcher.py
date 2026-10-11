# -*- coding: utf-8 -*-
"""Ollama Web 聊天的一键启动入口。

启动 Web 服务前先做健康检查；检查失败时只输出中文原因并返回非零码，
避免出现网页已打开但 Ollama/模型不可用的半启动状态。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence

from .health import HealthCheckConfig, HealthReport, check_ollama_health


DEFAULT_WEB_SCRIPT = Path(__file__).resolve().parents[1] / "AI" / "111111" / "web_chat.py"


def _print_report(report: HealthReport, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    for name, check in report.checks.items():
        print(f"{'✓' if check.ok else '✗'} {name}: {check.message}")


def launch_web(
    report: HealthReport,
    web_script: Path = DEFAULT_WEB_SCRIPT,
    *,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """健康检查通过后启动 Web；8899 已在线时复用现有服务。"""
    if not report.ok:
        return 2
    if report.checks.get("web_chat_8899") and report.checks["web_chat_8899"].ok:
        print("8899 Web 聊天服务已在线，跳过重复启动")
        return 0
    process = popen(
        [sys.executable, str(web_script)],
        cwd=str(web_script.parent.parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(f"Web 聊天已启动，PID={process.pid}，地址：http://127.0.0.1:8899")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动前自检并运行本地 Ollama Web 聊天")
    parser.add_argument("--json", action="store_true", dest="as_json", help="以 JSON 输出检查结果")
    parser.add_argument("--check-only", action="store_true", help="只检查，不启动 Web")
    parser.add_argument("--web-script", type=Path, default=DEFAULT_WEB_SCRIPT)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    health_check: Callable[[HealthCheckConfig], HealthReport] = check_ollama_health,
) -> int:
    args = _build_parser().parse_args(argv)
    report = health_check(HealthCheckConfig())
    _print_report(report, args.as_json)
    if args.check_only or not report.ok:
        return 0 if report.ok else 2
    return launch_web(report, args.web_script)


if __name__ == "__main__":
    raise SystemExit(main())
