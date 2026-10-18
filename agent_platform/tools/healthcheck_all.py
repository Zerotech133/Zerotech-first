# -*- coding: utf-8 -*-
"""healthcheck_all：一键聚合体检（周一现场一条命令跑全景，纯聚合零重复实现）。

## 背景

tools/ 现有 5 个只读检查工具（check_trigger_chain / check_observation_point /
verify_strategy_ledger / list_rejects / scan_secrets），现场要跑多条命令。
本工具是**聚合器**：一条命令按索引卡 T-30min 时序顺序调全部工具，汇总各
退出码与结论，输出总表。

## 纯聚合纪律（零重复实现）

各工具逻辑**一行都不复制**：全部 subprocess 调用子进程执行原脚本，本文件
只做「拼参数 → 跑 → 透传 stdout → 按退出码映射结论 → 出总表」。各工具
退出码语义取证自各源码 docstring 与 docs/audit/2026-10-18_工具箱索引卡.md
（M 路 25ac3e53），聚合顺序对齐其 T-30min 时序。

## 每工具退出码 → 结论映射（取证自源码 docstring）

  check_trigger_chain      0=PASS；1=全 UNKNOWN 中性；2=存在 FAIL
  check_observation_point  0=PASS；1=全 PENDING 中性；2=存在 FAIL
  verify_strategy_ledger   0=PASS（含空账本中性）；1=存在 FAIL；2=参数缺失→FAIL
  list_rejects             恒 0=中性浏览（无拒档也是中性）；其他码→FAIL
  scan_secrets             恒 0=中性报告（含命中但未开 --strict）；其他码→FAIL

未列入映射的退出码（含负数信号崩溃）一律按 FAIL 计并显示 stderr 尾部。

## 总退出码

  2 = 任一工具 FAIL（先查清）
  0 = 无 FAIL 且有 PASS（可放行）
  1 = 全中性（首跑前/浏览面形态，非失败）

## 红线

聚合器自身**零写面**：不创建目录、不写日志、不落盘任何文件；
子工具全部只读（各源码红线平移）。

用法：

    python tools/healthcheck_all.py                      # 全集（cwd=agent_platform）
    python tools/healthcheck_all.py --quiet              # 只出总表
    python tools/healthcheck_all.py --only check_trigger_chain check_observation_point
    python tools/healthcheck_all.py --repo-root <agent_platform根> --data-root <agent_platform/data>
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = TOOLS_DIR.parent

PASS, NEUTRAL, FAIL = "PASS", "中性", "FAIL"
STDERR_TAIL_CHARS = 500
DEFAULT_TIMEOUT_S = 120.0

# 每工具退出码语义映射（code_map 未覆盖的退出码 → FAIL）
TOOL_SPECS = (
    {"name": "check_trigger_chain", "script": "check_trigger_chain.py",
     "code_map": {0: PASS, 1: NEUTRAL, 2: FAIL}},
    {"name": "check_observation_point", "script": "check_observation_point.py",
     "code_map": {0: PASS, 1: NEUTRAL, 2: FAIL}},
    {"name": "verify_strategy_ledger", "script": "verify_strategy_ledger.py",
     "code_map": {0: PASS, 1: FAIL, 2: FAIL}},
    {"name": "list_rejects", "script": "list_rejects.py",
     "code_map": {0: NEUTRAL}},
    {"name": "scan_secrets", "script": "scan_secrets.py",
     "code_map": {0: NEUTRAL}},
)


def build_cmd(spec: dict, tools_dir: Path, repo_root: Path, data_root: Path) -> List[str]:
    """按索引卡 CLI 最短形式拼子命令（显式参数纪律，不依赖子工具默认值）。"""
    name = spec["name"]
    if name == "check_trigger_chain":
        extra = ["--repo-root", str(repo_root)]
    elif name in ("check_observation_point", "verify_strategy_ledger"):
        extra = ["--data-root", str(data_root)]
    elif name == "scan_secrets":
        extra = ["--git-status"]
    else:  # list_rejects：全可选，恒 0 浏览
        extra = []
    return [sys.executable, str(Path(tools_dir) / spec["script"])] + extra


def _as_text(data) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def _tail(text: str, n: int = STDERR_TAIL_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= n else "…" + text[-n:]


def run_tool(cmd: List[str], cwd: str, timeout_s: float) -> subprocess.CompletedProcess:
    """跑单个子工具（utf-8 强制，防 Windows 控制台 GBK 乱码）。可被测试注入替换。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd, cwd=cwd, timeout=timeout_s, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )


def run_one(spec: dict, cmd: List[str], cwd: str, timeout_s: float) -> dict:
    """跑一个工具并映射结论。崩溃（TimeoutExpired/OSError/未知退出码）→ FAIL。"""
    try:
        proc = run_tool(cmd, cwd, timeout_s)
    except subprocess.TimeoutExpired as exc:
        return {"name": spec["name"], "verdict": FAIL, "exit_code": None,
                "stdout": _as_text(exc.stdout),
                "stderr_tail": "超时（>%gs）" % timeout_s + _tail(_as_text(exc.stderr))}
    except OSError as exc:
        return {"name": spec["name"], "verdict": FAIL, "exit_code": None,
                "stdout": "", "stderr_tail": "启动失败：%s" % str(exc)[:STDERR_TAIL_CHARS]}
    rc = proc.returncode
    verdict = spec["code_map"].get(rc, FAIL)
    stderr_tail = _tail(proc.stderr) if verdict == FAIL else ""
    return {"name": spec["name"], "verdict": verdict, "exit_code": rc,
            "stdout": proc.stdout, "stderr_tail": stderr_tail}


def aggregate(results: List[dict]) -> int:
    """总退出码：任一 FAIL→2；无 FAIL 有 PASS→0；全中性→1。"""
    verdicts = [r["verdict"] for r in results]
    if FAIL in verdicts:
        return 2
    if PASS in verdicts:
        return 0
    return 1


def _print_summary(results: List[dict]) -> None:
    print("=" * 62)
    print("healthcheck_all 聚合总表")
    print("=" * 62)
    for r in results:
        print("%-24s | 退出码 %-4s | %s" % (r["name"], r["exit_code"], r["verdict"]))
    n_pass = sum(1 for r in results if r["verdict"] == PASS)
    n_neutral = sum(1 for r in results if r["verdict"] == NEUTRAL)
    n_fail = sum(1 for r in results if r["verdict"] == FAIL)
    print("汇总: PASS %d / 中性 %d / FAIL %d —— 共 %d 项" % (
        n_pass, n_neutral, n_fail, len(results)))
    verdict = {0: "无 FAIL 且有 PASS——可放行（总退出码 0）",
               1: "全中性——首跑前/浏览面形态，非失败（总退出码 1）",
               2: "存在 FAIL——先查清（总退出码 2）"}[aggregate(results)]
    print("结论: %s" % verdict)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="一键聚合体检：顺序调 5 个只读工具出总表（纯聚合零写面）")
    ap.add_argument("--repo-root", default=None,
                    help="agent_platform 根（缺省=本文件上级目录）")
    ap.add_argument("--data-root", default=None,
                    help="数据根（缺省=<repo-root>/data）")
    ap.add_argument("--only", nargs="+", metavar="工具名",
                    choices=[s["name"] for s in TOOL_SPECS],
                    help="只跑指定子集（按 T-30min 时序过滤）")
    ap.add_argument("--quiet", action="store_true",
                    help="只出总表，不透传各工具 stdout")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help="单工具超时秒数（默认 %g，超时按 FAIL 计）" % DEFAULT_TIMEOUT_S)
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else DEFAULT_REPO_ROOT
    data_root = Path(args.data_root).resolve() if args.data_root else repo_root / "data"
    selected = TOOL_SPECS if not args.only else [
        s for s in TOOL_SPECS if s["name"] in set(args.only)]

    results = []
    for spec in selected:
        cmd = build_cmd(spec, TOOLS_DIR, repo_root, data_root)
        res = run_one(spec, cmd, cwd=str(repo_root), timeout_s=args.timeout)
        results.append(res)
        if not args.quiet and res["stdout"]:
            sys.stdout.write(res["stdout"] if res["stdout"].endswith("\n")
                             else res["stdout"] + "\n")
        if res["verdict"] == FAIL and res["stderr_tail"]:
            print("[%s] stderr 尾部：%s" % (res["name"], res["stderr_tail"]),
                  file=sys.stderr)

    _print_summary(results)
    return aggregate(results)


if __name__ == "__main__":
    sys.exit(main())
