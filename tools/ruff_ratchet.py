# -*- coding: utf-8 -*-
"""ruff F/E9 棘轮：脏账只降不升（默认基线在 tools/ruff_baseline.json）。

用法：
  python -X utf8 tools/ruff_ratchet.py --update          # 生成/更新基线（仅在清理后）
  python -X utf8 tools/ruff_ratchet.py --check           # 检查（默认）
  python -X utf8 tools/ruff_ratchet.py --check --json
  python -X utf8 tools/ruff_ratchet.py --check --select F,E9 --roots portal agent_platform tools

规则：当前计数（总数或任一错误码）超过基线 → rc=1；低于基线不失败（提示可 --update 收紧）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BASELINE = os.path.join(REPO, "tools", "ruff_baseline.json")
DEFAULT_ROOTS = ("portal", "agent_platform", "tools", "k_stock", "analysis", "common")
DEFAULT_SELECT = "F,E9"


def parse_ruff_json(payload):
    """把 `ruff check --output-format json` 的 stdout 解析为 {code: count}。"""
    data = json.loads(payload or "[]")
    counts = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        if not code:
            code = "unknown"
        counts[code] = counts.get(code, 0) + 1
    return counts


def diff_counts(baseline_counts, current_counts):
    """返回 (regressions, improvements, baseline_total, current_total)。

    regressions: [(code, baseline, current)] 当前 > 基线；
    improvements: [(code, baseline, current)] 当前 < 基线。
    """
    regressions = []
    improvements = []
    for code in sorted(set(baseline_counts) | set(current_counts)):
        b = int(baseline_counts.get(code, 0))
        c = int(current_counts.get(code, 0))
        if c > b:
            regressions.append((code, b, c))
        elif c < b:
            improvements.append((code, b, c))
    return (regressions, improvements, sum(baseline_counts.values()),
            sum(current_counts.values()))


def _run_ruff(roots, select, repo=None):
    root_dir = repo or REPO
    cmd = [sys.executable, "-m", "ruff", "check", "--select", select,
           "--output-format", "json", "--no-cache"] + list(roots)
    proc = subprocess.run(cmd, cwd=root_dir, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode not in (0, 1):
        raise SystemExit("ruff 执行失败 rc=%s\n%s" % (proc.returncode, proc.stderr[-800:]))
    # ruff 0.x: rc=1 表示有命中；stdout 为 JSON 数组
    out = proc.stdout.strip()
    if not out:
        return {}
    return parse_ruff_json(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ruff F/E9 棘轮")
    ap.add_argument("--update", action="store_true", help="写入基线")
    ap.add_argument("--check", action="store_true", help="检查（默认动作）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--select", default=DEFAULT_SELECT)
    ap.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS))
    ap.add_argument("--code-root", default=REPO, help="仓库根（测试用）")
    ap.add_argument("--repo", default=None, help="被扫描仓库根（默认脚本所在仓库；生成主仓基线时用）")
    args = ap.parse_args(argv)

    if not args.update and not args.check:
        args.check = True
    try:
        current = _run_ruff(args.roots, args.select, repo=args.repo)
    except SystemExit:
        raise
    total = sum(current.values())

    if args.update:
        baseline = {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "select": args.select,
            "roots": args.roots,
            "total": total,
            "codes": current,
        }
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        with io.open(args.baseline, "w", encoding="utf-8", newline="") as f:
            json.dump(baseline, f, ensure_ascii=False, indent=2)
        print("ruff ratchet: baseline updated, total=%d file=%s" % (total, args.baseline))
        return 0

    if not os.path.exists(args.baseline):
        print("ruff ratchet: baseline 不存在，请先 --update（当前 total=%d）" % total,
              file=sys.stderr)
        return 2
    with io.open(args.baseline, "r", encoding="utf-8") as f:
        baseline = json.load(f)
    base_codes = baseline.get("codes") or {}
    regressions, improvements, base_total, cur_total = diff_counts(base_codes, current)
    result = {
        "ok": not regressions,
        "baseline_total": base_total,
        "current_total": cur_total,
        "regressions": regressions,
        "improvements": improvements,
        "current_codes": current,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("ruff ratchet: base=%d current=%d" % (base_total, cur_total))
        for code, b, c in regressions:
            print("  REGRESS %s: %d -> %d" % (code, b, c))
        for code, b, c in improvements:
            print("  improved %s: %d -> %d（可 --update 收紧基线）" % (code, b, c))
        if not regressions:
            print("  PASS（无新增 F/E9）")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
