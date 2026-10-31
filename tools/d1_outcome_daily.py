# -*- coding: utf-8 -*-
"""D1 outcome 回填 + 校准日常任务（幂等、离线可测、**不自动交易**）。

每个交易日收盘后跑一次（例：17:10 计划任务）：

1. **回填**：复用 ``agent_platform/decision_outcomes.py::run_backfill``，把
   ``agent_platform/data/shadow/decision_outcomes.jsonl`` 里缺失的 T+1 outcome
   补上（按 run_id 幂等，不重复写；历史行缺 decision_id/model 时从
   shadow_ledger 同 run_id 补齐，缺就 null 不伪造）。默认只回填最近 7 天
   （``--since`` 覆盖 / ``--full`` 全量补），避免每次全历史重扫。
2. **校准**：跑 ``tools/d1_calibration.py --json``，取方向性样本 n / Brier / ECE
   / can_expand。
3. **落报告**：写 ``agent_platform/data/shadow/d1_outcome_daily_<YYYYMMDD>.json``
   （``--report-dir`` 可改；含 n/Brier/ECE/coverage 与回填明细）。

纪律：只写 outcome sidecar 与本次 JSON 报告；**不改 D1 决策/prompt/审批/权限、
不下单、不改 trading_permitted**；取不到价格的行不写（不伪造 outcome）。

用法::

    python -X utf8 tools/d1_outcome_daily.py            # 生产：回填+校准+报告
    python -X utf8 tools/d1_outcome_daily.py --json     # 机器可读（stdout）
    python -X utf8 tools/d1_outcome_daily.py --no-fetch # 离线预演：不取价不新增
    python -X utf8 tools/d1_outcome_daily.py --since 2026-10-30
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "agent_platform"
DEFAULT_LEDGER = PLATFORM / "data" / "shadow" / "shadow_ledger.jsonl"
DEFAULT_OUT = PLATFORM / "data" / "shadow" / "decision_outcomes.jsonl"
DEFAULT_REPORT_DIR = PLATFORM / "data" / "shadow"
CALIBRATION = ROOT / "tools" / "d1_calibration.py"
SCHEMA = "d1_outcome_daily/v1"
DEFAULT_SINCE_DAYS = 7
REPORT_NAME = "d1_outcome_daily_%s.json"
TRADING_FLAGS = {"auto_trade": False, "orders_placed": 0,
                 "trading_permitted": "unchanged"}

sys.path.insert(0, str(PLATFORM))
import decision_outcomes as dout  # noqa: E402  （同仓 sidecar 复用，不复制逻辑）


def default_since(days=DEFAULT_SINCE_DAYS, today=None):
    """日常默认回填窗口起点：今天 - days（覆盖周末/短假）。"""
    today = today or date.today()
    return (today - timedelta(days=days)).isoformat()


def _no_fetch(symbol, n=60):
    """--no-fetch 专用：不取价 → 不新增行（安全预演，不伪造）。"""
    return []


def run_calibration(outcomes_path, calibration=CALIBRATION, timeout=120):
    """子进程跑 tools/d1_calibration.py --json --outcomes <path>，返回 dict。"""
    cmd = [sys.executable, "-X", "utf8", str(calibration),
           "--outcomes", str(outcomes_path), "--json"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": "calibration_failed",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
    try:
        rep = json.loads(res.stdout)
    except ValueError:
        return {"error": "calibration_unparsable",
                "detail": (res.stderr or "")[-400:] or "empty stdout"}
    if not isinstance(rep, dict) or "n_directional" not in rep:
        return {"error": "calibration_unexpected_output",
                "detail": json.dumps(rep, ensure_ascii=False)[:300]}
    return rep


def _resolve_report_path(report_dir, run_date):
    folder = Path(report_dir)
    if not folder.is_absolute():
        folder = ROOT / folder
    return folder / (REPORT_NAME % str(run_date).replace("-", ""))


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, str(path))


def build_report(backfill, calibration, since, run_date, report_path,
                 ledger, outcomes, network=True):
    """组装日常报告（n/Brier/ECE/coverage 为顶层必读字段）。"""
    summary = backfill.get("summary") or {}
    ledger_done = summary.get("ledger_done")
    outcome_rows = summary.get("outcomes")
    missing = None
    if isinstance(ledger_done, int) and isinstance(outcome_rows, int):
        missing = max(0, ledger_done - outcome_rows)
    warnings = []
    if missing:
        warnings.append("仍有 %d 条 done 裁决未算出 outcome（次日行情未到/取价"
                        "失败，按纪律不伪造不补写）" % missing)
    if calibration.get("error"):
        warnings.append("校准不可用：%s" % calibration.get("error"))
    elif calibration.get("can_expand") is False:
        warnings.append("校准门未达扩容条件：%s"
                        % "；".join(calibration.get("reasons") or []))
    if not network:
        warnings.append("--no-fetch 模式：本次不取价、不新增 outcome（离线预演）")
    return {
        "schema": SCHEMA,
        "date": str(run_date),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "report_path": str(report_path),
        "since": since,
        "network": bool(network),
        "outcome_backfill": {
            "new": backfill.get("new", 0),
            "dates": backfill.get("dates") or {},
            "repaired": backfill.get("repaired", 0),
            "repaired_fields": backfill.get("repaired_fields")
            or {"decision_id": 0, "model": 0},
            "written": bool(backfill.get("written")),
            "ledger": str(ledger),
            "outcomes_file": str(outcomes),
        },
        "calibration": calibration,
        "n": calibration.get("n_directional"),
        "brier": calibration.get("brier"),
        "ece": calibration.get("ece"),
        "can_expand": calibration.get("can_expand"),
        "reasons": calibration.get("reasons") or [],
        "coverage": summary.get("coverage"),
        "ledger_done": ledger_done,
        "outcome_rows": outcome_rows,
        "hit_rate": summary.get("hit_rate"),
        "missing_outcomes": missing,
        "warnings": warnings,
        "trading": dict(TRADING_FLAGS),
    }


def run_daily(ledger=DEFAULT_LEDGER, outcomes=DEFAULT_OUT,
              report_dir=DEFAULT_REPORT_DIR, since=None, full=False,
              no_fetch=False, write=True, today=None):
    """执行一次幂等日常，返回 (report, report_path)。"""
    run_day = today or date.today()
    if full:
        since = None
    elif since is None:
        since = default_since(today=run_day)
    fetcher = _no_fetch if no_fetch else None
    backfill = dout.run_backfill(ledger, outcomes, since=since,
                                 price_fetcher=fetcher, write=write)
    calibration = run_calibration(outcomes)
    path = _resolve_report_path(report_dir, run_day)
    report = build_report(backfill, calibration, since, run_day, path,
                          ledger, outcomes, network=not no_fetch)
    if write:
        _write_json_atomic(path, report)
    return report, path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="D1 outcome 回填 + 校准日常（幂等；不自动交易）")
    ap.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR),
                    help="JSON 报告目录（默认 agent_platform/data/shadow；"
                         "可指向 deliverables）")
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="回填起始日（含；默认最近 %d 天）" % DEFAULT_SINCE_DAYS)
    ap.add_argument("--full", action="store_true",
                    help="不限起始日，补全全部历史缺失")
    ap.add_argument("--no-fetch", action="store_true",
                    help="离线预演：不取价、不新增 outcome（仍出校准/报告）")
    ap.add_argument("--no-write", action="store_true",
                    help="不写 sidecar/报告，只 dry-run 打印")
    ap.add_argument("--json", action="store_true", help="stdout 输出报告 JSON")
    args = ap.parse_args(argv)
    since = args.since
    if since is not None:
        try:
            date.fromisoformat(str(since))
        except ValueError:
            ap.error("--since 需 YYYY-MM-DD 格式（如 2026-10-30）")
    try:
        report, path = run_daily(
            ledger=args.ledger, outcomes=args.out,
            report_dir=args.report_dir, since=since, full=args.full,
            no_fetch=args.no_fetch, write=not args.no_write)
    except Exception as exc:  # noqa: BLE001 - 日常任务 fail-visible，不静默
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__,
                              "detail": str(exc)[:400]},
                             ensure_ascii=False, indent=2))
        else:
            print("[d1_outcome_daily] 失败：%s: %s"
                  % (type(exc).__name__, str(exc)[:300]), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        bf = report["outcome_backfill"]
        print("[d1_outcome_daily] date=%s new=%d dates=%s repaired=%d n=%s "
              "brier=%s ece=%s coverage=%s → %s"
              % (report["date"], bf["new"],
                 ",".join("%s:%d" % (d, n) for d, n in bf["dates"].items()) or "-",
                 bf["repaired"], report["n"], report["brier"],
                 report["ece"], report["coverage"], path))
        for warning in report["warnings"]:
            print("[warn] %s" % warning)
        if report["calibration"].get("error"):
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
