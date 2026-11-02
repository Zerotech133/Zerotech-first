# -*- coding: utf-8 -*-
"""每日研究运营任务入口：sidecar 入库 → ACAD1 选池 → 观察计算 → 回测证据入库。

运行态日志（P4-1 / task-20）
---------------------------
- 开始写 running（source=scheduled/manual、scheduled_at、started_at、data_date、steps）；
- 每个步骤开始/结束写 running/success/failed/skipped（逐步落盘，崩溃也能看到进度）；
- 结束写 success 或 failed（异常也写 failed，含 rc / error / finished_at / duration_sec）；
- 状态文件默认 C:\\Temp\\zhice_job_status.json（可用 ZHICE_JOB_STATUS_PATH 覆盖），
  读写实现见 scripts/job_run_log.py；只写状态 JSON，**不写 DB、不改任务计划**；
- 原有 stdout/stderr 日志（C:\\Temp\\zhice_daily_task.log）保留，且追加运行态事件。

环境变量
--------
    ZHICE_JOB_SCHEDULED_AT   计划任务注入的计划时间（存在时 source 缺省视为 schedule）
    ZHICE_JOB_SOURCE         schedule | manual（显式覆盖来源）
    ZHICE_JOB_DATA_DATE      数据日（缺省=当天）
    ZHICE_JOB_STATUS_PATH    状态文件路径覆盖（测试/多实例）

测试钩子（仅测试用，生产不设置；用于验证状态机且不实际执行/不改生产）
---------------------------------------------------------------------
    ZHICE_DAILY_OPS_TEST=1              不执行真实步骤，只演练状态生命周期（每步记
                                        success + "test-mode: skipped execution"）；
    ZHICE_DAILY_OPS_TEST_FAIL=<step名>  配合 TEST=1 在该步模拟失败，验证 failed 分支。
生产路径（不设置上述 TEST 变量）与原先行为一致：按顺序 runpy 执行下列 11 个脚本。

Wrapper 尾部失败（PS1 的 acad1_rationale/agent_autopilot/agent_worker）
-------------------------------------------------------------------------
    `--record-tail-file <json>`：PS1 把尾部脚本的 [{name, rc, stderr}] 交回本脚本，
    追加 failed step（含 rc/stderr）到 job status、status=partial_failure，并写
    C:\\Temp\\zhice_daily_task_alert.json（可用 ZHICE_DAILY_ALERT_PATH 覆盖）。
    不改变 16 步主链的 STEPS/执行逻辑。
"""
from __future__ import annotations

import json
import os
import runpy
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = CODE / "scripts"
DEFAULT_JOB_STATUS_PATH = Path(r"C:\Temp\zhice_job_status.json")
DEFAULT_ALERT_PATH = Path(r"C:\Temp\zhice_daily_task_alert.json")
STDERR_CAP = 8000
_daily_log = open(r"C:\Temp\zhice_daily_task.log", "a", encoding="utf-8")
sys.stdout = _daily_log
sys.stderr = _daily_log
for _path in (str(CODE / "src"), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

STEPS: tuple[str, ...] = (
    "ingest_strategy_workspace.py",
    "adapt_platform_cn.py",
    "adapt_platform_global.py",
    "seed_selection_desk.py",
    "backfill_selection_market.py",
    "sync_instrument_names.py",
    "backfill_security_names.py",
    "build_base_signals.py",
    "build_rule_base_signals.py",
    "sync_candidate_to_acad1.py",
    "dw_adapter.py",
    "seed_acad1_research_cards.py",
    "acad1_evidence_bundle.py",
    "data_contract_check.py",
    "calc_observations.py",
    "ingest_observations_backtest.py",
)


def _load_job_log():
    """加载运行态日志模块；加载失败不阻断每日任务（只告警）。"""
    try:
        import job_run_log  # noqa: PLC0415  (scripts 目录已在 sys.path)
        return job_run_log
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-research] job_run_log import failed: {exc}", flush=True)
        return None


def _data_date() -> str:
    return os.environ.get("ZHICE_JOB_DATA_DATE") or date.today().isoformat()


def _source() -> str:
    explicit = (os.environ.get("ZHICE_JOB_SOURCE") or "").strip().lower()
    if explicit in ("schedule", "manual"):
        return explicit
    return "schedule" if os.environ.get("ZHICE_JOB_SCHEDULED_AT") else "manual"


def _latest_pool_date() -> str | None:
    try:
        import sqlalchemy as sa
        from zhice_os_v2.config.settings import get_settings
        e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
        with e.connect() as c:
            d = c.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
        return str(d) if d else None
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-research] latest_pool_date failed: {exc}", flush=True)
        return None


def _mark_skipped(job_log, names: tuple[str, ...] | list[str], reason: str) -> None:
    if not job_log:
        return
    for name in names:
        job_log.mark_step(name, "skipped", message=reason)


def _read_json_object(path: Path) -> dict | None:
    """读取 JSON object；缺失/损坏返回 None（不抛出，生产状态文件不可写时仍能发告警）。"""
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-research] tail record read failed ({path}): {exc}", flush=True)
        return None


def _write_json_atomic(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-research] tail record write failed ({path}): {exc}", flush=True)
        return False


def record_tail_results(result_file: str | Path) -> int:
    """记录 PS1 尾部脚本失败：job status partial_failure + failed steps + 告警文件。

    result_file 为 JSON（或 JSON 数组）：[{name, rc, stderr}]。
    - 不触碰 16 步主链 STEPS，也不改其 success 状态；只在既有 steps 末尾追加/更新失败尾步；
    - 写 C:\\Temp\\zhice_daily_task_alert.json（ZHICE_DAILY_ALERT_PATH 可覆盖）；
    - 告警/状态任一写入失败返回 2（PS1 仍会以非 0 退出）。
    """
    result_path = Path(result_file)
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-research] tail result unreadable ({result_path}): {exc}", flush=True)
        return 2
    items = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
    failures: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "tail_script")
        try:
            rc = int(item.get("rc") or 0)
        except (TypeError, ValueError):
            rc = 1
        if rc == 0:
            continue
        failures.append({"name": name, "rc": rc, "stderr": str(item.get("stderr") or "")})
    if not failures:
        print("[daily-research] tail record: no failed tail scripts", flush=True)
        return 0

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    status_path = Path(os.environ.get("ZHICE_JOB_STATUS_PATH") or DEFAULT_JOB_STATUS_PATH)
    alert_path = Path(os.environ.get("ZHICE_DAILY_ALERT_PATH") or DEFAULT_ALERT_PATH)
    payload = _read_json_object(status_path)
    if payload is None:
        payload = {
            "run_id": f"tail-{now}",
            "source": os.environ.get("ZHICE_JOB_SOURCE") or "manual",
            "scheduled_at": os.environ.get("ZHICE_JOB_SCHEDULED_AT") or now,
            "started_at": now,
            "data_date": os.environ.get("ZHICE_JOB_DATA_DATE") or date.today().isoformat(),
            "rc": None,
            "steps": [],
            "error": None,
        }
    steps = payload.get("steps")
    if not isinstance(steps, list):
        steps = []
        payload["steps"] = steps
    main_steps_count = len(steps)

    tail_failures: list[dict] = []
    for failure in failures:
        stderr = failure["stderr"]
        capped = stderr[-STDERR_CAP:] if len(stderr) > STDERR_CAP else stderr
        step = next(
            (s for s in steps if isinstance(s, dict) and s.get("name") == failure["name"]),
            None,
        )
        if step is None:
            step = {
                "name": failure["name"], "started_at": now, "finished_at": None,
                "rc": None, "message": "", "status": "pending",
            }
            steps.append(step)
        step.update({
            "status": "failed",
            "rc": failure["rc"],
            "message": f"wrapper tail failed rc={failure['rc']}",
            "finished_at": now,
            "stderr": capped,
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            "stderr_truncated": len(stderr) > STDERR_CAP,
            "tail": True,
        })
        tail_failures.append({
            "name": failure["name"], "rc": failure["rc"], "stderr": capped,
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            "stderr_truncated": len(stderr) > STDERR_CAP,
        })

    payload["status"] = "partial_failure"
    payload["rc"] = 1
    payload["finished_at"] = now
    payload["alert_file"] = str(alert_path)
    payload["tail_failures"] = tail_failures
    payload["main_steps_preserved"] = main_steps_count
    payload["error"] = "wrapper tail failed: " + ", ".join(
        f"{f['name']}(rc={f['rc']})" for f in tail_failures
    )
    try:
        started = datetime.fromisoformat(str(payload.get("started_at")))
        finished = datetime.fromisoformat(now)
        payload["duration_sec"] = round(max((finished - started).total_seconds(), 0.0), 2)
    except Exception:  # noqa: BLE001
        pass

    status_ok = _write_json_atomic(status_path, payload)
    alert = {
        "schema": "zhice.daily_alert/v1",
        "generated_at": now,
        "event": "daily_tail_partial_failure",
        "severity": "P1",
        "run_id": payload.get("run_id"),
        "source": payload.get("source"),
        "scheduled_at": payload.get("scheduled_at"),
        "data_date": payload.get("data_date"),
        "status": "partial_failure",
        "rc": 1,
        "main_rc": 0,
        "job_status_path": str(status_path),
        "failures": tail_failures,
        "message": payload["error"],
    }
    alert_ok = _write_json_atomic(alert_path, alert)
    print(
        f"[daily-research] tail partial_failure: {len(tail_failures)} failed; "
        f"alert={alert_path} status_ok={status_ok} alert_ok={alert_ok}",
        flush=True,
    )
    return 0 if status_ok and alert_ok else 2


def _record_tail_cli(argv: list[str]) -> int:
    try:
        idx = argv.index("--record-tail-file")
    except ValueError:
        print("usage: run_daily_research_ops.py --record-tail-file <json>", flush=True)
        return 2
    if idx + 1 >= len(argv):
        print("usage: run_daily_research_ops.py --record-tail-file <json>", flush=True)
        return 2
    return record_tail_results(argv[idx + 1])


def main() -> int:
    job_log = _load_job_log()
    steps = list(STEPS)
    source = _source()
    data_date = _data_date()
    scheduled_at = os.environ.get("ZHICE_JOB_SCHEDULED_AT") or None
    test_mode = os.environ.get("ZHICE_DAILY_OPS_TEST") == "1"
    test_fail = (os.environ.get("ZHICE_DAILY_OPS_TEST_FAIL") or "").strip()

    print(f"[daily-research] start source={source} data_date={data_date} "
          f"steps={len(steps)} test_mode={test_mode}", flush=True)
    payload = None
    if job_log:
        payload = job_log.start_run(
            source=source, data_date=data_date, scheduled_at=scheduled_at,
            steps=steps, script="scripts/run_daily_research_ops.py")
        print(f"[daily-research] job_status={job_log.status_path()} "
              f"run_id={(payload or {}).get('run_id')}", flush=True)

    try:
        for index, name in enumerate(steps):
            print("[daily-research]", name, flush=True)
            if test_mode:
                if job_log:
                    job_log.mark_step(name, "running", message="test-mode")
                if test_fail == name:
                    if job_log:
                        job_log.mark_step(name, "failed", rc=1,
                                          message="test-mode simulated failure")
                        _mark_skipped(job_log, steps[index + 1:], "skipped after failure")
                        job_log.finish_run(
                            "failed", rc=1,
                            error=f"test-mode simulated failure: {name}")
                    print(f"[daily-research] TEST simulated FAILED at {name}", flush=True)
                    return 1
                if job_log:
                    job_log.mark_step(name, "success", rc=0,
                                      message="test-mode: skipped execution")
                continue

            if job_log:
                job_log.mark_step(name, "running")
            try:
                runpy.run_path(str(SCRIPTS_DIR / name), run_name="__main__")
            except SystemExit as exc:
                code = exc.code if exc.code is not None else 0
                if code not in (0, None):
                    rc = int(code) if isinstance(code, int) else 1
                    if job_log:
                        job_log.mark_step(name, "failed", rc=rc,
                                          message=f"SystemExit({code!r})")
                        _mark_skipped(job_log, steps[index + 1:], "skipped after failure")
                        job_log.finish_run("failed", rc=rc,
                                           error=f"{name} SystemExit({code!r})")
                    print(f"[daily-research] FAILED {name} SystemExit({code!r})", flush=True)
                    raise
                if job_log:
                    job_log.mark_step(name, "success", rc=0)
            except Exception as exc:  # noqa: BLE001
                if job_log:
                    job_log.mark_step(name, "failed", rc=1, message=repr(exc))
                    _mark_skipped(job_log, steps[index + 1:], "skipped after failure")
                    job_log.finish_run("failed", rc=1, error=f"{name}: {exc}")
                traceback.print_exc()
                print(f"[daily-research] FAILED {name}: {exc}", flush=True)
                return 1
            else:
                if job_log:
                    job_log.mark_step(name, "success", rc=0)

        if job_log:
            job_log.finish_run("success", rc=0, data_date=_latest_pool_date() or data_date)
        print(f"[daily-research] done success ({len(steps)} steps)", flush=True)
        return 0
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        if job_log:
            job_log.finish_run("failed", rc=1, error=repr(exc))
        traceback.print_exc()
        print(f"[daily-research] FAILED unexpected: {exc}", flush=True)
        return 1
    finally:
        try:
            _daily_log.flush()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    if "--record-tail-file" in sys.argv:
        raise SystemExit(_record_tail_cli(sys.argv))
    raise SystemExit(main())
