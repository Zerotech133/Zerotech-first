# -*- coding: utf-8 -*-
"""Watch 24h observation probe (read-only, paper-only).

收盘后保持观察：每轮记录 8789/任务/engine 心跳；仅在服务/任务异常时发 Feishu
（生产目标 quant_group）。引擎在收盘窗口后 stale 属预期，不告警。

用法:
  python -X utf8 tools/watch_24h_probe.py --json
  python -X utf8 tools/watch_24h_probe.py --no-alert --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATUS = REPO / "agent_platform" / "data" / "watch_engine" / "status.json"
LOG = REPO / "agent_platform" / "data" / "watch_engine" / "observe_24h.jsonl"
CLOSED = REPO / "agent_platform" / "data" / "watch_engine" / "observe_24h.closed"
ALERT_STATE = REPO / "agent_platform" / "data" / "watch_engine" / "observe_alert_last.json"
CHECK_URL = "http://127.0.0.1:8789/watch-admin"


def _now():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _market_closed(now=None):
    now = now or datetime.now().astimezone()
    if now.weekday() >= 5:
        return True
    minutes = now.hour * 60 + now.minute
    return minutes < (9 * 60 + 25) or minutes > (15 * 60 + 5)


def _http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def _task_running(name):
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/fo", "list"],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        ).stdout
        return "Running" in out
    except Exception:
        return False


def _psutil_loop_running():
    """Return True/False when psutil can inspect the runner process.

    PowerShell/WMI subprocess liveness checks intermittently time out under
    load (observed 2026-11-05 12:43/12:48 false P0 while PID 11916 was alive).
    psutil reads the process table natively, so it is preferred.
    """
    try:
        import psutil  # local import: probe still works without psutil
    except Exception:
        return None
    try:
        me = psutil.Process()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.pid == me.pid:
                    continue
                name = (proc.info.get("name") or "").lower()
                cmd = " ".join(proc.info.get("cmdline") or [])
                if "powershell" in name and "watch_engine_loop.ps1" in cmd:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return False
    except Exception:
        return None


def _powershell_loop_running():
    """Legacy fallback: inspect Win32_Process command lines via PowerShell."""
    try:
        cmd = (
            "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
            "Where-Object { $_.CommandLine -like '*watch_engine_loop.ps1*' } | "
            "Measure-Object | Select-Object -ExpandProperty Count"
        )
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
        return int(out or "0") > 0
    except Exception:
        return False


def _loop_process_running():
    """Check the runner loop by process command line, not task state.

    schtasks status can transiently read Ready while the task is being restarted.
    The 24h probe must not emit a false production alert on that transient.  If
    psutil reports no process, fall back to the scheduled-task state: a task
    that is Running with a wrapper restarting is alive for alerting purposes.
    """
    state = _psutil_loop_running()
    if state is True:
        return True
    if state is None:
        # psutil unavailable: preserve the original process-command check only.
        return _powershell_loop_running()
    return _task_running("WatchEngineLoop")


def _engine():
    try:
        d = json.loads(STATUS.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"exists": False}
    hb = d.get("last_tick_at") or d.get("heartbeat_at") or d.get("last_run")
    age = None
    try:
        age = int((datetime.now().astimezone() - datetime.fromisoformat(str(hb))).total_seconds())
    except Exception:
        pass
    return {
        "exists": True, "engine_state": d.get("engine_state"),
        "last_tick_at": hb, "age_s": age, "armed_count": d.get("armed_count"),
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-alert", action="store_true")
    a = p.parse_args(argv)
    if CLOSED.exists():
        # 24h 观察窗口已由 tools/watch_24h_closeout.py 关闭：probe 变为 no-op，
        # 避免 WatchEngineLoop 继续每 10 分钟追加观察记录。
        if a.json:
            print(json.dumps({"closed": True, "closed_marker": str(CLOSED)}, ensure_ascii=False))
        else:
            print("[watch_24h] closed: %s" % CLOSED)
        return 0
    closed = _market_closed()
    watch_ok = _http_ok(CHECK_URL)
    admin_running = watch_ok  # 8789 HTTP is the authoritative WatchAdmin liveness
    loop_running = _loop_process_running()
    eng = _engine()
    problems = []
    if not watch_ok:
        problems.append("8789_watch_admin_down")
    if not loop_running:
        problems.append("WatchEngineLoop_process_not_running")
    if not closed and eng.get("age_s") is not None and eng["age_s"] > 180:
        problems.append("engine_heartbeat_stale_in_session")
    record = {
        "at": _now(), "market_closed": closed,
        "watch_8789": watch_ok, "watch_admin_task": admin_running,
        "watch_loop_task": loop_running, "engine": eng,
        "problems": problems, "alert_sent": False,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if problems and not a.no_alert:
        try:
            last = json.loads(ALERT_STATE.read_text(encoding="utf-8")) if ALERT_STATE.exists() else {}
        except Exception:
            last = {}
        recent = False
        try:
            recent = time.time() - float(last.get("ts") or 0) < 1800 and last.get("problems") == problems
        except Exception:
            recent = False
        if not recent:
            sys.path.insert(0, str(REPO))
            from agent_platform.watch_alert_feishu import make_feishu_sink
            sink = make_feishu_sink("quant_group")
            result = sink({
                "symbol": "WATCH-24H", "action": "ALERT", "plan_id": "observe-24h",
                "observed": ",".join(problems), "threshold": "service_tasks",
                "quote_as_of": record["at"],
            })
            record["alert_sent"] = result.get("status") == "sent"
            ALERT_STATE.write_text(json.dumps({
                "ts": time.time(), "problems": problems, "at": record["at"],
            }, ensure_ascii=False), encoding="utf-8")
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    if a.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print("[watch_24h] at=%s closed=%s watch=%s tasks=%s/%s problems=%s"
              % (record["at"], closed, watch_ok, admin_running, loop_running, problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
