# -*- coding: utf-8 -*-
"""daily_pipeline.py — 盘后分析单入口：筛选 + 市场结构，一条命令跑完。

为什么单独一个串联脚本
----------------------
`daily_screening.py` 管选股、`market_analysis.py` 管市场状态，两者都已能独立跑。
这里只做**编排**，不改动两个存量脚本，避免把新依赖塞进别人的链路。

执行顺序与失败语义
----------------
1. `daily_screening.py`（约 70s）：产出 research/reports/daily_screening_<date>.{json,md}
2. `market_analysis.py`（约 15s）：产出 research/reports/market_analysis_<date>.{json,md}

**任一步非 0 都不阻断后续步骤**（少一份产物比少两份好），但整体退出码非 0，
并逐条打印失败原因。--strict 同时透传给 market_analysis：数据降级也算失败。

注意
----
- 必须在**主工作区**跑：`daily_screening.py` 的 REPO 是硬编码的，
  在 linked worktree 里跑仍会写主工作区的 data/，且 worktree 缺 gitignore 掉的数据根。
- 不注册计划任务；调度需审批后另行接入。
- 只写 research/reports/，不写生产 data/、不调 LLM、不下单。

用法
----
    python -X utf8 tools/research/daily_pipeline.py                 # 最新交易日
    python -X utf8 tools/research/daily_pipeline.py --date 20261105
    python -X utf8 tools/research/daily_pipeline.py --date 20261105 --strict
    python -X utf8 tools/research/daily_pipeline.py --skip-screening  # 只跑市场结构
    python -X utf8 tools/research/daily_pipeline.py --dry-run         # 筛选只算不写
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(name: str, script: Path, args: list[str]) -> dict:
    t0 = time.time()
    p = subprocess.run([sys.executable, "-X", "utf8", str(script), *args],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = [x for x in (p.stdout or "").strip().splitlines() if x.strip()][-3:]
    return {
        "step": name, "rc": p.returncode, "seconds": round(time.time() - t0, 1),
        "tail": tail,
        "stderr": (p.stderr or "").strip()[-400:] if p.returncode else "",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="盘后分析单入口（筛选 + 市场结构）")
    ap.add_argument("--date", default="", help="交易日 YYYYMMDD，默认最新")
    ap.add_argument("--skip-screening", action="store_true", help="跳过每日筛选")
    ap.add_argument("--dry-run", action="store_true", help="筛选只算不写")
    ap.add_argument("--strict", action="store_true", help="数据降级也判失败")
    ap.add_argument("--json", action="store_true", help="只输出汇总 JSON")
    args = ap.parse_args(argv)

    date_args = ["--date", args.date] if args.date else []
    steps: list[dict] = []

    if not args.skip_screening:
        sargs = date_args + (["--dry-run"] if args.dry_run else [])
        steps.append(run("screening", ROOT / "tools" / "research" / "daily_screening.py", sargs))

    margs = date_args + (["--strict"] if args.strict else [])
    steps.append(run("market", ROOT / "tools" / "research" / "market_analysis.py", margs))

    ok = all(s["rc"] == 0 for s in steps)
    summary = {"ok": ok, "date": args.date or "latest", "steps": steps}

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        for s in steps:
            print("[%s] rc=%d %.1fs" % (s["step"], s["rc"], s["seconds"]))
            for line in s["tail"]:
                print("    " + line)
            if s["stderr"]:
                print("    ! " + s["stderr"].replace("\n", "\n    ! "))
        print("结果: %s" % ("全部成功" if ok else "有步骤失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
