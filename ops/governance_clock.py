# -*- coding: utf-8 -*-
"""治理时钟（常驻进程内 loop）· WO-LEGION-CLOCK-20261026。

背景（2026-10-26 研究）：治理线缺"不依赖会话"的节奏——Hermes cron 是空壳、
计划任务增删属 L2 且 `ops.task_launch_failures` 现为黄、agent 会话建不了任务。
本模块沿用仓内已验证的 scheduler_loop 范式（`portal/publish_schedule_server.py:118-144`）：
15s tick + `day|HH:MM` fired 去重 + `last_tick` 心跳，**独立 daemon 进程**运行，
不寄生 8796 控制台；保活走 PowerWatch 式 5 分钟幂等拉起（另由启动脚本负责）。

锚点（默认，可配）：08:46 扫描（task_exit_audit --scan --window 24）；**17:50 预扫描**
（收口前刷新摘要，避免 18:05 拿 >6h 旧摘要触发 as-of 告警）；18:05 收口
（closeout_round --if-changed）。17:55 被 `LegionW42MorningBrief` 占用，故收口错峰 18:05。

纪律：
  · 单实例锁（O_EXCL + 陈旧锁回收），并发启动第二个立即退出（rc=3）；
  · 错过超过 max-delay 分钟不补跑（防"中午启动补跑早锚"），记 skipped_missed；
  · 每 tick 落 last_tick 心跳（健康卡据此判活）；日志 JSONL 落盘；
  · `--dry-run` 只记账不执行；`--once` 跑一个 tick 供测试/手动。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

_REPO = Path(__file__).resolve().parents[1]
_PLATFORM = _REPO / "agent_platform"
_TOOLS = _PLATFORM / "tools"

DEFAULT_ANCHORS = "08:46:scan,17:50:scan,18:05:closeout"
DEFAULT_TICK_S = 15.0
DEFAULT_MAX_DELAY_MIN = 120.0
LOCK_STALE_S = 6 * 3600.0

ACTIONS: dict[str, list[str]] = {
    "scan": [sys.executable, str(_TOOLS / "task_exit_audit.py"),
             "--scan", "--window", "24"],
    "closeout": [sys.executable, str(_TOOLS / "closeout_round.py"),
                 "--json", "--if-changed"],
}


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _hhmm(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def _anchor_ts(now: float, hhmm: str) -> float:
    """今天 hh:mm 的 epoch（本地时区）。"""
    day = dt.datetime.fromtimestamp(now).date()
    hh, mm = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(hh, mm)).astimezone().timestamp()


def parse_anchors(spec: str) -> list[tuple[str, str]]:
    """'08:46:scan,18:05:closeout' -> [('08:46','scan'), ...]"""
    out = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(":")
        if len(parts) != 3:
            raise ValueError("锚点格式应为 HH:MM:action：%r" % chunk)
        hh, mm, action = parts
        int(hh), int(mm)  # 校验
        if action not in ACTIONS:
            raise ValueError("未知 action %r（可选 %s）" % (action, list(ACTIONS)))
        out.append(("%02d:%02d" % (int(hh), int(mm)), action))
    if not out:
        raise ValueError("至少一个锚点")
    return out


def load_state(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_log(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _default_runner(cmd: list[str], timeout: float = 900.0) -> dict:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    tail = (r.stdout or r.stderr or "").strip()[-400:]
    return {"ok": r.returncode == 0, "rc": r.returncode, "out_tail": tail}


def run_tick(state: dict,
             *,
             now_fn: Callable[[], float] = time.time,
             runner: Callable[[list[str]], dict] = _default_runner,
             dry_run: bool = False,
             anchors: Optional[list[tuple[str, str]]] = None,
             state_path: Optional[Path] = None,
             log_path: Optional[Path] = None,
             max_delay_minutes: float = DEFAULT_MAX_DELAY_MIN) -> list[dict]:
    """执行一个 tick：到点未跑的锚点动作跑一次；返回本次触发记录。"""
    anchors = anchors if anchors is not None else parse_anchors(DEFAULT_ANCHORS)
    now = float(now_fn())
    day = _day(now)
    fired_map = state.setdefault("fired", {})
    fired: list[dict] = []

    for hhmm, action in anchors:
        key = "%s|%s" % (day, hhmm)
        if key in fired_map:
            continue
        anchor_ts = _anchor_ts(now, hhmm)
        if now < anchor_ts:
            continue
        delay_min = (now - anchor_ts) / 60.0
        if delay_min > float(max_delay_minutes):
            fired_map[key] = {"at": _iso(now), "action": action,
                              "ok": False, "skipped_missed": True}
            rec = {"at": _iso(now), "anchor": hhmm, "action": action,
                   "ok": False, "skipped_missed": True,
                   "delay_min": round(delay_min, 1)}
            fired.append(rec)
            if log_path:
                append_log(log_path, rec)
            continue

        if dry_run:
            result = {"ok": True, "dry_run": True, "cmd": ACTIONS[action]}
        else:
            t0 = time.time()
            result = runner(list(ACTIONS[action]))
            result["duration_s"] = round(time.time() - t0, 1)
        rec = {"at": _iso(now), "anchor": hhmm, "action": action,
               "delay_min": round(delay_min, 1), **result}
        fired_map[key] = {"at": _iso(now), "action": action,
                          "ok": bool(result.get("ok"))}
        fired.append(rec)
        if log_path:
            append_log(log_path, rec)

    state["last_tick"] = now
    state["last_tick_iso"] = _iso(now)
    if state_path:
        save_state(state_path, state)
    return fired


def acquire_lock(path: Path, stale_s: float = LOCK_STALE_S) -> Optional[Path]:
    """单实例锁：成功返回锁路径；已被持有返回 None；陈旧锁自动回收。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = stale_s + 1
        if age < stale_s:
            return None
        try:
            path.unlink()
        except OSError:
            return None
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"pid": os.getpid(), "started_at": _iso(time.time())}))
    return path


def release_lock(path: Optional[Path]) -> None:
    if not path:
        return
    try:
        Path(path).unlink()
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="治理时钟：常驻 tick loop（双锚：扫描/收口）")
    ap.add_argument("--once", action="store_true", help="只跑一个 tick（测试/手动）")
    ap.add_argument("--loop", action="store_true", help="常驻循环（默认行为）")
    ap.add_argument("--tick", type=float, default=DEFAULT_TICK_S)
    ap.add_argument("--anchors", default=DEFAULT_ANCHORS)
    ap.add_argument("--state-file", default=str(_PLATFORM / "data" / "governance_clock_state.json"))
    ap.add_argument("--log-file", default=str(Path(os.environ.get("TEMP", r"C:\Temp")) / "governance_clock.log"))
    ap.add_argument("--lock-file", default=str(_PLATFORM / "data" / "governance_clock.lock"))
    ap.add_argument("--max-delay-minutes", type=float, default=DEFAULT_MAX_DELAY_MIN)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    anchors = parse_anchors(args.anchors)
    state_path = Path(args.state_file)
    log_path = Path(args.log_file)
    lock = acquire_lock(Path(args.lock_file))
    if lock is None:
        print("[BUSY] 已有治理时钟实例（锁 %s）；拒绝重复启动" % args.lock_file,
              file=sys.stderr)
        return 3
    try:
        state = load_state(state_path)
        if args.once:
            fired = run_tick(state, dry_run=args.dry_run, anchors=anchors,
                             state_path=state_path, log_path=log_path,
                             max_delay_minutes=args.max_delay_minutes)
            print(json.dumps({"ok": True, "fired": fired,
                              "last_tick": state.get("last_tick_iso")},
                             ensure_ascii=False))
            return 0
        print("[clock] start anchors=%s tick=%.0fs state=%s"
              % (args.anchors, args.tick, state_path))
        while True:
            run_tick(state, dry_run=args.dry_run, anchors=anchors,
                     state_path=state_path, log_path=log_path,
                     max_delay_minutes=args.max_delay_minutes)
            time.sleep(max(1.0, float(args.tick)))
    except KeyboardInterrupt:
        return 0
    finally:
        release_lock(lock)


if __name__ == "__main__":
    raise SystemExit(main())
