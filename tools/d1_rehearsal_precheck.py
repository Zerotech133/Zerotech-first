# -*- coding: utf-8 -*-
"""D1 rehearsal 日常预检（17:10，计划 E3）：隔离根跑单票 + 五字段核对 + 校准记录。

每个交易日 17:10（与影子主跑同窗口）用**隔离根**跑一次单票 rehearsal，验证 D1
决策链真的产出可验收的账本行，并把校准指标一并落 JSON 报告：

1) 隔离根默认 ``C:\\Temp\\d1_rehearsal_precheck_<YYYYMMDD>``（``--root`` 覆盖），
   子进程跑::

       python -X utf8 agent_platform/run_shadow.py --rehearsal --root <root> --codes 600519

   （``--skip-run`` 跳过该步、只核对已有隔离账本；隔离根内参数/路径全重定向，
   不写生产账、不推飞书、不启停服务）
2) 核对账本行五字段：``decision_id`` / ``watch_reason`` / ``evidence_refs`` /
   ``policy_checks`` / ``evidence_sufficient``（WATCH 裁决必须有非空
   watch_reason，否则方向/降级原因不可解释）；
3) 跑 ``tools/d1_calibration.py --json`` 记录 n/Brier/ECE；校准门未达标（n<30 等）
   记为 **BLOCKED 警告**，不否决 rehearsal 链本身（扩席是另一道门）；
4) 输出 JSON 报告（默认 ``agent_platform/data/shadow/d1_rehearsal_<YYYYMMDD>.json``，
   ``--report-dir`` 可指 deliverables），含 ``verdict``=PASS/FAIL 与 ``reasons``。

纪律：只读生产账本/不改 D1 决策与权限/**不自动交易**/不重启服务；写动作仅限
隔离根与本次报告。

17:10 日常用法（计划任务/提醒动作体，不新建常驻服务）::

    python -X utf8 tools/d1_rehearsal_precheck.py
    python -X utf8 tools/d1_rehearsal_precheck.py --json
    python -X utf8 tools/d1_rehearsal_precheck.py --skip-run   # 只核对已有隔离账本
    python -X utf8 tools/d1_rehearsal_precheck.py --report-dir deliverables

退出码：0=PASS / 1=FAIL（有 reasons，见报告）/ 2=自身错误（参数/子进程不可用）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "agent_platform"
RUN_SHADOW = PLATFORM / "run_shadow.py"
CALIBRATION = ROOT / "tools" / "d1_calibration.py"
DEFAULT_REPORT_DIR = PLATFORM / "data" / "shadow"
DEFAULT_CODES = "600519"
SCHEMA = "d1_rehearsal_precheck/v1"
REQUIRED_FIELDS = ("decision_id", "watch_reason", "evidence_refs",
                   "policy_checks", "evidence_sufficient")
TRADING_FLAGS = {"auto_trade": False, "orders_placed": 0,
                 "trading_permitted": "unchanged"}
LOG_NAME = "last_run.log"


def default_root(run_date):
    """隔离根默认 C:\\Temp\\d1_rehearsal_precheck_<YYYYMMDD>（无 C:\\Temp 用临时目录）。"""
    base = (Path(r"C:\Temp") if os.path.isdir(r"C:\Temp")
            else Path(tempfile.gettempdir()))
    return base / ("d1_rehearsal_precheck_%s" % str(run_date).replace("-", ""))


def _split_codes(value):
    return [x.strip() for x in str(value or "").replace("，", ",").split(",")
            if x.strip()]


def _read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def _tail(path, limit=8):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-limit:]


def _run_rehearsal(root, codes, timeout=900):
    """子进程跑隔离根 rehearsal（真实链路；隔离根写入，生产账零写）。"""
    root = Path(root)
    cmd = [sys.executable, "-X", "utf8", str(RUN_SHADOW), "--rehearsal",
           "--root", str(root), "--codes", ",".join(codes)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
        rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        rc = 124
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = "timeout after %ss" % timeout
    except OSError as exc:
        rc = 125
        out, err = "", "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return {"skipped": False, "command": cmd, "rc": rc,
            "duration_s": round(time.time() - t0, 1),
            "stdout_tail": str(out).strip().splitlines()[-8:],
            "stderr_tail": str(err).strip().splitlines()[-8:],
            "log_tail": _tail(root / "shadow" / LOG_NAME)}


def _find_row(rows, run_date, code):
    """隔离账本中 当日+标的 的 (done 行, failed 行)；取最后一条。"""
    done, failed = [], []
    for row in rows:
        if str(row.get("symbol") or "") != code:
            continue
        if str(row.get("date") or "") != str(run_date):
            continue
        (done if row.get("status") == "done" else failed).append(row)
    return (done[-1] if done else None), (failed[-1] if failed else None)


def check_row_fields(row):
    """核对账本行五字段，返回 (checks, problems)。"""
    checks, problems = {}, []
    decision_id = str(row.get("decision_id") or "").strip()
    checks["decision_id"] = bool(decision_id)
    if not checks["decision_id"]:
        problems.append("账本行 decision_id 缺失/为空")
    rec = str(row.get("recommendation") or "").upper()
    watch_reason = str(row.get("watch_reason") or "").strip()
    checks["watch_reason"] = ("watch_reason" in row
                              and (bool(watch_reason) if rec == "WATCH" else True))
    if "watch_reason" not in row:
        problems.append("账本行缺 watch_reason 键")
    elif rec == "WATCH" and not watch_reason:
        problems.append("WATCH 裁决 watch_reason 为空（降级原因不可解释）")
    refs = row.get("evidence_refs")
    checks["evidence_refs"] = isinstance(refs, list) and len(refs) > 0
    if not checks["evidence_refs"]:
        problems.append("账本行 evidence_refs 缺失/为空")
    policy_checks = row.get("policy_checks")
    checks["policy_checks"] = (isinstance(policy_checks, list)
                               and len(policy_checks) > 0)
    if not checks["policy_checks"]:
        problems.append("账本行 policy_checks 缺失/为空")
    checks["evidence_sufficient"] = row.get("evidence_sufficient") is True
    if not checks["evidence_sufficient"]:
        problems.append("账本行 evidence_sufficient 非 true")
    return checks, problems


def run_calibration(calibration=CALIBRATION, outcomes_path=None, timeout=120):
    """子进程跑 d1_calibration.py --json，返回 (report_dict, error)。"""
    cmd = [sys.executable, "-X", "utf8", str(calibration), "--json"]
    if outcomes_path:
        cmd += ["--outcomes", str(outcomes_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {}, "calibration_failed: %s: %s" % (
            type(exc).__name__, str(exc)[:200])
    if proc.returncode != 0:
        return {}, "calibration_rc=%s: %s" % (
            proc.returncode, (proc.stderr or "")[-200:])
    try:
        rep = json.loads(proc.stdout)
    except ValueError:
        return {}, "calibration_unparsable: %s" % ((proc.stdout or "")[:200],)
    if not isinstance(rep, dict) or "n_directional" not in rep:
        return {}, "calibration_unexpected_output"
    return rep, None


def _resolve_report_path(report_dir, run_date):
    folder = Path(report_dir)
    if not folder.is_absolute():
        folder = ROOT / folder
    return folder / ("d1_rehearsal_%s.json" % str(run_date).replace("-", ""))


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


def _calibration_block(calibration_report, error):
    """校准段：n/Brier/ECE + PASS/BLOCKED/ERROR（BLOCKED 只是告警不否决链）。"""
    if error:
        return {"status": "ERROR", "n": None, "brier": None, "ece": None,
                "can_expand": None, "reasons": [], "error": error}
    can_expand = calibration_report.get("can_expand")
    return {"status": "PASS" if can_expand else "BLOCKED",
            "n": calibration_report.get("n_directional"),
            "brier": calibration_report.get("brier"),
            "ece": calibration_report.get("ece"),
            "can_expand": can_expand,
            "reasons": calibration_report.get("reasons") or []}


def run_precheck(root=None, codes=None, skip_run=False,
                 report_dir=DEFAULT_REPORT_DIR, run_date=None,
                 ledger_path=None, outcomes_path=None, rehearsal_timeout=900,
                 write=True):
    """执行一次预检，返回 (report, report_path)。"""
    run_date = str(run_date or date.today().isoformat())
    code_list = list(codes or _split_codes(DEFAULT_CODES))
    root = Path(root) if root else default_root(run_date)
    ledger = (Path(ledger_path) if ledger_path
              else root / "shadow" / "shadow_ledger.jsonl")
    reasons, warnings = [], []

    if skip_run:
        run = {"skipped": True, "command": [], "rc": None, "duration_s": 0.0,
               "stdout_tail": [], "stderr_tail": [], "log_tail": []}
    else:
        run = _run_rehearsal(root, code_list, rehearsal_timeout)
        if run.get("rc") != 0:
            reasons.append("rehearsal 子进程 rc=%s（隔离根 %s）"
                           % (run.get("rc"), root))

    rows = _read_jsonl(ledger)
    code_rows, row = {}, None
    for code in code_list:
        done_row, failed_row = _find_row(rows, run_date, code)
        entry = {"found": done_row is not None,
                 "status": (done_row or failed_row or {}).get("status", "missing")}
        if done_row is None and failed_row is not None:
            entry["error"] = str(failed_row.get("error") or "")[:200]
            reasons.append("标的 %s 账本行为 failed：%s"
                           % (code, entry["error"]))
        elif done_row is None:
            reasons.append("标的 %s 无 %s 的 done 账本行（隔离账本 %s）"
                           % (code, run_date, ledger))
        elif row is None:
            row = done_row
        code_rows[code] = entry

    if row is not None:
        checks, problems = check_row_fields(row)
        reasons.extend(problems)
    else:
        checks = {name: False for name in REQUIRED_FIELDS}

    calibration_report, calibration_error = run_calibration(
        outcomes_path=outcomes_path)
    calibration = _calibration_block(calibration_report, calibration_error)
    if calibration_error:
        reasons.append("校准工具不可用：%s" % calibration_error)
    elif calibration["status"] == "BLOCKED":
        warnings.append("校准门 BLOCKED（暂不扩席）：%s"
                        % ("；".join(calibration["reasons"]) or "指标未达标"))

    path = _resolve_report_path(report_dir, run_date)
    report = {
        "schema": SCHEMA,
        "date": run_date,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "verdict": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
        "warnings": warnings,
        "run": run,
        "isolated_root": str(root),
        "ledger": {"path": str(ledger), "exists": Path(ledger).exists(),
                   "rows": len(rows), "checks": checks, "codes": code_rows,
                   "row": row},
        "calibration": calibration,
        "report_path": str(path),
        "production_writes": False,
        "trading": dict(TRADING_FLAGS),
    }
    if write:
        _write_json_atomic(path, report)
    return report, path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="D1 rehearsal 17:10 日常预检（隔离根 + 五字段核对 + 校准记录）")
    parser.add_argument("--root", default=None,
                        help="隔离根（默认 C:\\Temp\\d1_rehearsal_precheck_<YYYYMMDD>）")
    parser.add_argument("--codes", default=DEFAULT_CODES,
                        help="预演标的（逗号分隔，默认 600519）")
    parser.add_argument("--skip-run", action="store_true",
                        help="跳过 run_shadow，只核对已有隔离账本")
    parser.add_argument("--ledger", default=None,
                        help="要核对的账本（默认 <root>/shadow/shadow_ledger.jsonl）")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR),
                        help="JSON 报告目录（默认 agent_platform/data/shadow；可指 deliverables）")
    parser.add_argument("--outcomes", default=None,
                        help="传给 d1_calibration --outcomes 的 sidecar（默认生产）")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="核对日期（默认今天；实际 run 时须为今天）")
    parser.add_argument("--timeout", type=int, default=900,
                        help="rehearsal 子进程超时秒（默认 900）")
    parser.add_argument("--no-write", action="store_true", help="只打印，不写报告")
    parser.add_argument("--json", action="store_true", help="stdout 输出报告 JSON")
    args = parser.parse_args(argv)
    run_date = args.date or date.today().isoformat()
    try:
        date.fromisoformat(run_date)
    except ValueError:
        parser.error("--date 需 YYYY-MM-DD 格式（如 2026-10-31）")
    try:
        report, path = run_precheck(
            root=args.root, codes=_split_codes(args.codes),
            skip_run=args.skip_run, report_dir=args.report_dir,
            run_date=run_date, ledger_path=args.ledger,
            outcomes_path=args.outcomes, rehearsal_timeout=args.timeout,
            write=not args.no_write)
    except Exception as exc:  # noqa: BLE001 - 预检工具 fail-visible，不静默
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__,
                              "detail": str(exc)[:400]},
                             ensure_ascii=False, indent=2))
        else:
            print("[d1_rehearsal_precheck] 自身错误：%s: %s"
                  % (type(exc).__name__, str(exc)[:300]), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        cal = report["calibration"]
        print("[d1_rehearsal_precheck] date=%s verdict=%s root=%s "
              "ledger_rows=%d checks=%s calibration=%s n=%s brier=%s ece=%s → %s"
              % (report["date"], report["verdict"], report["isolated_root"],
                 report["ledger"]["rows"],
                 ",".join("%s:%s" % (k, "OK" if v else "MISSING")
                          for k, v in report["ledger"]["checks"].items()),
                 cal["status"], cal["n"], cal["brier"], cal["ece"], path))
        for reason in report["reasons"]:
            print("[FAIL] %s" % reason)
        for warning in report["warnings"]:
            print("[warn] %s" % warning)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
