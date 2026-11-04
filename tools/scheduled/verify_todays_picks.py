# -*- coding: utf-8 -*-
"""TodaysPicks 18:20 首跑验收（只读校验，不写生产数据）。

校验项：
  1. 当日 `todays_picks.json` 的 generated_at 落在验收日、且不早于 18:15；
  2. trade_basis 含验收日；counts.total > 0 且 T1/T2/T3 齐全；
  3. `picks_history/todays_<YYYYMMDD>.json` 存在，且其 generated_at/basis 为验收日；
  4. 计划任务 `\\TodaysPicks` 当日 LastRunTime 有效、LastTaskResult=0。

退出码：0 PASS / 2 FAIL / 3 PENDING（任务当日尚未跑完）。
输出 JSON 默认 `research/reports/todays_picks_acceptance_<YYYYMMDD>.json`。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PICKS = REPO / "portal" / "data" / "todays_picks.json"
HISTORY = REPO / "portal" / "data" / "picks_history"
REPORTS = REPO / "research" / "reports"
TASK_NAME = "TodaysPicks"
MIN_READY_TIME = "18:15:00"


def parse_ts(value: str) -> datetime | None:
    """解析 'YYYY-MM-DD HH:MM:SS'（兼容 ISO T 分隔与空格）。"""
    text = str(value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if fmt != "%Y-%m-%d" else text[:10], fmt)
        except ValueError:
            continue
    return None


def check_payload(payload: dict, expect_day: str, label: str,
                  min_ready_time: str = MIN_READY_TIME) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return [f"{label}: 不是 JSON 对象"]
    gen = parse_ts(payload.get("generated_at"))
    if gen is None:
        errors.append(f"{label}: generated_at 缺失/不可解析 ({payload.get('generated_at')!r})")
    else:
        if gen.strftime("%Y-%m-%d") != expect_day:
            errors.append(f"{label}: generated_at 日期 {gen:%Y-%m-%d} != {expect_day}")
        elif gen.strftime("%H:%M:%S") < min_ready_time:
            errors.append(f"{label}: generated_at {gen:%H:%M:%S} 早于 {min_ready_time}")
    basis = str(payload.get("trade_basis") or "")
    if expect_day not in basis:
        errors.append(f"{label}: trade_basis 不含 {expect_day}（{basis!r}）")
    counts = payload.get("counts") or {}
    total = counts.get("total")
    if not isinstance(total, int) or total <= 0:
        errors.append(f"{label}: counts.total 非正（{total!r}）")
    for key in ("T1_resonance", "T2_high_value", "T3_single"):
        if key not in counts:
            errors.append(f"{label}: counts 缺 {key}")
    return errors


def check_archive(archive: dict | None, path: Path) -> list[str]:
    if archive is None:
        return [f"归档缺失: {path}"]
    errors: list[str] = []
    gen = parse_ts(archive.get("generated_at"))
    if gen is None:
        errors.append(f"归档: generated_at 不可解析（{archive.get('generated_at')!r}）")
    if not isinstance((archive.get("counts") or {}).get("total"), int):
        errors.append("归档: counts.total 非整数")
    return errors


def check_task(task: dict, expect_day: str) -> tuple[list[str], bool, list[str]]:
    """返回 (错误列表, 是否当日已运行, 警告列表)。"""
    warnings: list[str] = []
    if task.get("error"):
        # 任务不存在/查询失败不是「尚未运行」，必须按失败处理，避免 PENDING 掩盖故障。
        return [f"任务: 快照失败（{task.get('error')}）"], False, warnings
    last_run = parse_ts(task.get("last_run") or "")
    if last_run is None:
        return [f"任务: last_run 不可解析（{task.get('last_run')!r}）"], False, warnings
    if last_run.strftime("%Y-%m-%d") != expect_day:
        return [], False, warnings  # 当日尚未运行 → PENDING，不算失败
    errors: list[str] = []
    last_result = int(task.get("last_result") or 0)
    if last_result not in (0, 3):
        errors.append(f"任务: LastTaskResult={last_result}（期望 0；3=T1 停挂但仍已落盘）")
    elif last_result == 3:
        warnings.append("任务 rc=3：否决源异常导致 T1 停挂（清单已落盘，属降级运行）")
    if str(task.get("state") or "").lower() not in ("ready", "running", "就绪"):
        errors.append(f"任务: state={task.get('state')!r}（期望 Ready/就绪）")
    return errors, True, warnings


def evaluate(picks: dict | None, archive: dict | None, task: dict,
             expect_day: str, archive_path: Path,
             min_ready_time: str = MIN_READY_TIME) -> dict:
    checks: list[dict] = []
    errors: list[str] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            errors.append(f"{name}: {detail}" if detail else name)

    payload_errors = [] if picks is None else check_payload(picks, expect_day, "当日清单", min_ready_time)
    if picks is None:
        payload_errors = [f"当日清单缺失: {PICKS}"]
    add("当日清单 generated_at/basis/counts", not payload_errors, "; ".join(payload_errors))

    archive_errors = check_archive(archive, archive_path)
    if archive is not None:
        gen = parse_ts(archive.get("generated_at"))
        if gen is not None:
            if gen.strftime("%Y-%m-%d") != expect_day:
                archive_errors.append(f"归档: generated_at 日期 {gen:%Y-%m-%d} != {expect_day}")
            elif gen.strftime("%H:%M:%S") < min_ready_time:
                archive_errors.append(
                    f"归档: generated_at {gen:%H:%M:%S} 早于 {min_ready_time}（可能是旧运行首写）")
        if expect_day not in str(archive.get("trade_basis") or ""):
            archive_errors.append(f"归档: trade_basis 不含 {expect_day}")
    add("picks_history 归档", not archive_errors, "; ".join(archive_errors))

    task_errors, ran_today, warnings = check_task(task, expect_day)
    add("计划任务当日运行", ran_today and not task_errors, "; ".join(task_errors))

    snapshot_error = bool(task.get("error"))
    if errors:
        status = "FAIL" if (ran_today or snapshot_error) else "PENDING"
    else:
        status = "PASS"
    if status == "PENDING":
        errors.append("任务当日尚未运行完成（18:20 后重试）")
    return {
        "schema_version": 1,
        "expect_day": expect_day,
        "status": status,
        "ok": status == "PASS",
        "warnings": warnings,
        "generated_at": (picks or {}).get("generated_at"),
        "trade_basis": (picks or {}).get("trade_basis"),
        "counts": (picks or {}).get("counts") or {},
        "archive_path": str(archive_path),
        "archive": {
            "exists": archive is not None,
            "generated_at": (archive or {}).get("generated_at"),
            "trade_basis": (archive or {}).get("trade_basis"),
            "total": ((archive or {}).get("counts") or {}).get("total"),
            "mtime": datetime.fromtimestamp(archive_path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            if archive_path.exists() else None,
        },
        "task": task,
        "checks": checks,
        "errors": errors,
    }


def task_snapshot(task_name: str = TASK_NAME) -> dict:
    ps = (
        f"$t=Get-ScheduledTask -TaskName '{task_name}' -ErrorAction Stop;"
        f"$i=Get-ScheduledTaskInfo -TaskName '{task_name}' -ErrorAction Stop;"
        "[pscustomobject]@{state=[string]$t.State;"
        "last_run=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss');"
        "last_result=$i.LastTaskResult;"
        "next_run=$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')} | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"task_snapshot 失败: {type(exc).__name__}: {exc}"}
    text = (out.stdout or "").strip()
    if out.returncode != 0 or not text:
        return {"error": f"task_snapshot rc={out.returncode}: {(out.stderr or '')[:200]}"}
    try:
        return json.loads(text)
    except ValueError as exc:
        return {"error": f"task_snapshot JSON 解析失败: {exc}: {text[:200]}"}


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TodaysPicks 18:20 首跑验收（只读）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="验收日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--picks", default=str(PICKS))
    ap.add_argument("--history-dir", default=str(HISTORY))
    ap.add_argument("--json-out", default="", help="JSON 输出路径（默认 research/reports/...）")
    ap.add_argument("--min-ready-time", default=MIN_READY_TIME, help="当日生成时间下限 HH:MM:SS")
    args = ap.parse_args(argv)

    day = args.date
    picks_path = Path(args.picks)
    picks = load_json(picks_path)
    archive_path = Path(args.history_dir) / f"todays_{day.replace('-', '')}.json"
    archive = load_json(archive_path)
    task = task_snapshot()
    result = evaluate(picks, archive, task, day, archive_path, args.min_ready_time)

    out = Path(args.json_out) if args.json_out else REPORTS / f"todays_picks_acceptance_{day.replace('-', '')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"status={result['status']} expect_day={day}")
    print(f"generated_at={result['generated_at']!r} trade_basis={result['trade_basis']!r}")
    counts = result["counts"]
    print(f"counts: total={counts.get('total')} T1={counts.get('T1_resonance')} "
          f"T2={counts.get('T2_high_value')} T3={counts.get('T3_single')}")
    print(f"archive: exists={result['archive']['exists']} path={archive_path}")
    print(f"archive.generated_at={result['archive']['generated_at']!r} "
          f"archive.total={result['archive']['total']} mtime={result['archive']['mtime']}")
    print(f"task: last_run={task.get('last_run')!r} last_result={task.get('last_result')!r} "
          f"state={task.get('state')!r}")
    for err in result["errors"]:
        print(f"[FAIL] {err}")
    print(f"json_out={out}")
    return 0 if result["status"] == "PASS" else (3 if result["status"] == "PENDING" else 2)


if __name__ == "__main__":
    sys.exit(main())
