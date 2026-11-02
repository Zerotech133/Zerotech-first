# -*- coding: utf-8 -*-
"""每日任务运行态日志：写/读 C:\\Temp\\zhice_job_status.json。

用途
----
给定时任务（scripts/run_daily_research_ops.py）提供可审计的业务运行态：
启动写 running、每步更新 steps、结束写 success/failed（异常也写 failed），
包含 scheduled_at / started_at / finished_at / source(schedule|manual) /
data_date / rc / steps。只写状态 JSON，**不写 DB、不改任务计划**。

状态文件字段
------------
    run_id        本次运行 id（时间戳 + pid）
    status        running | success | failed
    source        schedule | manual（计划任务/手工；可由 env 覆盖）
    scheduled_at  计划时间（env ZHICE_JOB_SCHEDULED_AT；缺省=started_at）
    started_at    实际开始时间（ISO8601，带时区）
    finished_at   结束时间（running 时为 null）
    data_date     数据日（env ZHICE_JOB_DATA_DATE；缺省=当天）
    rc            进程退出码（running 时为 null）
    steps         [{name, status(pending/running/success/failed/skipped),
                    started_at, finished_at, rc, message}]
    error         失败原因（success 时为 null）
    duration_sec  运行耗时（秒，结束后写入）
    updated_at    状态文件最后写入时间

用法
----
    python scripts/job_run_log.py                 # 打印当前状态 JSON
    python scripts/job_run_log.py status --path C:\\Temp\\xxx.json
    python scripts/job_run_log.py --self-test     # 写/读/成功/失败分支自测（不碰生产状态文件）

环境变量
--------
    ZHICE_JOB_STATUS_PATH     覆盖状态文件路径（测试/多实例；默认 C:\\Temp\\zhice_job_status.json）
    ZHICE_JOB_SCHEDULED_AT    计划任务注入的计划时间
    ZHICE_JOB_DATA_DATE       数据日覆盖
    ZHICE_JOB_SOURCE          schedule | manual 覆盖
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

DEFAULT_STATUS_PATH = r"C:\Temp\zhice_job_status.json"
STATUS_VALUES = ("running", "success", "failed")
STEP_VALUES = ("pending", "running", "success", "failed", "skipped")

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def status_path(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("ZHICE_JOB_STATUS_PATH") or DEFAULT_STATUS_PATH)


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"


def read_status(path: str | Path | None = None) -> dict | None:
    """读取状态 JSON；文件缺失/损坏时返回 None（不抛出，避免影响主任务）。"""
    p = status_path(path)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        print(f"[job_run_log] read_status failed ({p}): {exc}", flush=True)
        return None


def write_status(payload: dict, path: str | Path | None = None) -> bool:
    """原子写状态 JSON（同目录 tmp + os.replace）；失败只告警，不打断主任务。"""
    p = status_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data = dict(payload)
        data["updated_at"] = now_iso()
        tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[job_run_log] write_status failed ({p}): {exc}", flush=True)
        return False


def _duration_sec(started_at: str | None, finished_at: str) -> float | None:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at)
        return round(max((end - start).total_seconds(), 0.0), 2)
    except Exception:  # noqa: BLE001
        return None


def _default_data_date() -> str:
    return os.environ.get("ZHICE_JOB_DATA_DATE") or date.today().isoformat()


# ---------------------------------------------------------------------------
# 生命周期 API（供 run_daily_research_ops.py 调用；全部不抛出）
# ---------------------------------------------------------------------------
def start_run(source: str = "manual", data_date: str | None = None,
              scheduled_at: str | None = None, steps: list[str] | None = None,
              script: str = "scripts/run_daily_research_ops.py",
              path: str | Path | None = None) -> dict:
    started = now_iso()
    payload = {
        "run_id": new_run_id(),
        "status": "running",
        "source": source if source in ("schedule", "manual") else "manual",
        "scheduled_at": (scheduled_at or os.environ.get("ZHICE_JOB_SCHEDULED_AT")
                         or started),
        "started_at": started,
        "finished_at": None,
        "data_date": data_date or _default_data_date(),
        "rc": None,
        "steps": [
            {"name": name, "status": "pending", "started_at": None,
             "finished_at": None, "rc": None, "message": ""}
            for name in (steps or [])
        ],
        "error": None,
        "script": script,
        "pid": os.getpid(),
    }
    write_status(payload, path)
    return payload


def mark_step(name: str, status: str, rc: int | None = None, message: str = "",
              path: str | Path | None = None) -> dict | None:
    """更新单个 step 状态并立即落盘（崩溃/中断时也能看到进度）。"""
    if status not in STEP_VALUES:
        print(f"[job_run_log] invalid step status: {status}", flush=True)
        return None
    payload = read_status(path) or {
        "run_id": new_run_id(), "status": "running", "source": "manual",
        "scheduled_at": now_iso(), "started_at": now_iso(), "finished_at": None,
        "data_date": _default_data_date(), "rc": None, "steps": [], "error": None,
    }
    steps = payload.setdefault("steps", [])
    step = next((s for s in steps if s.get("name") == name), None)
    if step is None:
        step = {"name": name, "started_at": None, "finished_at": None,
                "rc": None, "message": ""}
        steps.append(step)
    previous = step.get("status")
    step["status"] = status
    step["rc"] = rc
    step["message"] = message
    if previous in (None, "pending") and status != "pending":
        step["started_at"] = step.get("started_at") or now_iso()
    if status in ("success", "failed", "skipped"):
        step["finished_at"] = now_iso()
    if payload.get("status") not in STATUS_VALUES:
        payload["status"] = "running"
    write_status(payload, path)
    return payload


def finish_run(status: str = "success", rc: int = 0, error: str | None = None,
               path: str | Path | None = None, data_date: str | None = None) -> dict | None:
    """结束运行：写 success/failed + finished_at + duration_sec + rc。"""
    if status not in ("success", "failed"):
        status = "failed" if rc else "success"
    payload = read_status(path) or {
        "run_id": new_run_id(), "source": "manual", "scheduled_at": now_iso(),
        "started_at": now_iso(), "data_date": _default_data_date(), "steps": [],
    }
    finished = now_iso()
    payload["status"] = status
    if data_date:
        payload["data_date"] = data_date
    payload["finished_at"] = finished
    payload["rc"] = int(rc)
    payload["error"] = error
    payload["duration_sec"] = _duration_sec(payload.get("started_at"), finished)
    write_status(payload, path)
    return payload


# ---------------------------------------------------------------------------
# 自测（默认写独立文件，不碰生产状态文件）
# ---------------------------------------------------------------------------
def _self_test() -> int:
    path = Path(os.environ.get("ZHICE_JOB_STATUS_SELFTEST_PATH")
                or r"C:\Temp\zhice_job_status.selftest.json")
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  {'PASS' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
        if not cond:
            failures.append(name)

    print(f"[job_run_log] self-test path: {path}")
    stock = ["step-a.py", "step-b.py"]

    # 1) running 态
    start_run(source="manual", data_date="2026-11-01", scheduled_at=None,
              steps=stock, path=path)
    running = read_status(path) or {}
    check("running.status", running.get("status") == "running")
    for field in ("scheduled_at", "started_at", "source", "data_date", "rc", "steps"):
        check(f"running.{field} present", field in running)
    check("running.finished_at null", running.get("finished_at") is None)
    check("running.rc null", running.get("rc") is None)
    check("running.steps pending", [s["status"] for s in running.get("steps", [])]
          == ["pending", "pending"])

    # 2) 逐步更新 + success
    mark_step("step-a.py", "running", path=path)
    mark_step("step-a.py", "success", rc=0, message="ok", path=path)
    mark_step("step-b.py", "running", path=path)
    mark_step("step-b.py", "success", rc=0, path=path)
    finish_run("success", rc=0, path=path)
    success = read_status(path) or {}
    check("success.status", success.get("status") == "success")
    check("success.rc", success.get("rc") == 0)
    check("success.finished_at", bool(success.get("finished_at")))
    check("success.duration_sec", isinstance(success.get("duration_sec"), (int, float)))
    check("success.steps all success",
          [s["status"] for s in success.get("steps", [])] == ["success", "success"])

    # 3) failed 分支（异常语义）
    start_run(source="manual", data_date="2026-11-01", steps=stock, path=path)
    mark_step("step-a.py", "success", rc=0, path=path)
    mark_step("step-b.py", "failed", rc=3, message="simulated exception", path=path)
    finish_run("failed", rc=1, error="simulated failure in self-test", path=path)
    failed = read_status(path) or {}
    check("failed.status", failed.get("status") == "failed")
    check("failed.rc", failed.get("rc") == 1)
    check("failed.error", bool(failed.get("error")))
    check("failed.steps",
          [s["status"] for s in failed.get("steps", [])] == ["success", "failed"])

    # 4) 状态文件是合法 JSON 且字段齐全
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        required = {"run_id", "status", "source", "scheduled_at", "started_at",
                    "finished_at", "data_date", "rc", "steps", "updated_at"}
        check("json.valid", isinstance(parsed, dict))
        check("json.required_fields", required.issubset(parsed.keys()),
              f"missing={sorted(required - set(parsed.keys()))}")
    except Exception as exc:  # noqa: BLE001
        check("json.valid", False, repr(exc))

    if failures:
        print(f"[job_run_log] SELF-TEST FAILED ({len(failures)}): {failures}")
        return 1
    print("[job_run_log] SELF-TEST PASS（写/读/running/success/failed 分支均通过）")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="写/读每日任务运行态 JSON")
    parser.add_argument("command", nargs="?", default="status",
                        choices=("status", "self-test"),
                        help="status=打印当前状态（默认）；self-test=本地自测")
    parser.add_argument("--path", default=None, help="覆盖状态文件路径")
    parser.add_argument("--self-test", action="store_true", dest="self_test_flag",
                        help="等价于 self-test 子命令")
    args = parser.parse_args()
    if args.self_test_flag or args.command == "self-test":
        return _self_test()
    status = read_status(args.path)
    if status is None:
        print(f"[job_run_log] 无状态文件: {status_path(args.path)}")
        return 2
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
