# -*- coding: utf-8 -*-
"""质量门自检工具（B2-d 研发/质量席）。

只读/可回滚：依次执行三项平台确定性检查（全部现有工具，不新增逻辑）：
  1. `python -m compileall -q agent_platform tools`（语法/字节码编译）
  2. `python tools/check_ports.py`（端口注册自检，非 strict）
  3. `python agent_platform/tools/scan_secrets.py --git-status`（默认明文凭据扫描口径）
每次执行：结果写 `agent_platform/data/quality_gate/status.json` + history.jsonl，
并给每席写 `agent_platform/data/quality_gate/<席>/status.json` + `dev_events`（patrol_verdict）。

环境/回滚：`QUALITY_GATE=off` 立即跳过；删除计划任务 + 本工具即回滚。
退出码：0 全过；1 有检查失败；2 自身错误。
用法：python agent_platform/tools/quality_gate.py --quiet
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
KILL_ENV = "QUALITY_GATE"
KILL_VALUES = ("off", "0", "false", "no")
DEFAULT_SEATS = ("QA1", "QA2", "SEC1", "SEC2")
TIMEOUT = 900
MAX_HISTORY = 2000


def kill_switch_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KILL_ENV) or "").strip().lower() in KILL_VALUES


def _run(argv: list, timeout: int = TIMEOUT) -> dict:
    t0 = time.time()
    try:
        proc = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-500:]
        return {"ok": proc.returncode == 0, "rc": proc.returncode,
                "elapsed_ms": int((time.time() - t0) * 1000), "tail": tail}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": None, "error": "timeout",
                "elapsed_ms": int((time.time() - t0) * 1000)}
    except OSError as exc:
        return {"ok": False, "rc": None, "error": type(exc).__name__}


def run_checks(py: str = sys.executable) -> dict:
    checks = {
        "compileall": _run([py, "-m", "compileall", "-q", "agent_platform", "tools"]),
        "ports": _run([py, "tools/check_ports.py"]),
        "secrets": _run([py, "agent_platform/tools/scan_secrets.py", "--git-status"]),
    }
    return {"schema": "quality_gate/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ok": all(c.get("ok") for c in checks.values()),
            "checks": checks}


def record(result: dict, seats, status_root=None) -> dict:
    root = Path(status_root) if status_root else (PLATFORM / "data" / "quality_gate")
    root.mkdir(parents=True, exist_ok=True)
    (root / "status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    hist = root / "history.jsonl"
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    lines = hist.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_HISTORY:
        hist.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")
    for seat in seats:
        d = root / seat
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(
            json.dumps({"seat": seat, **result}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return {"root": str(root), "seats": list(seats)}


def emit_audit(seat: str, result: dict) -> dict:
    try:
        tools = str(REPO / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events  # noqa: WPS433
        bad = [k for k, v in result.get("checks", {}).items() if not v.get("ok")]
        detail = "quality_gate ok=%s checks=%s%s" % (
            result.get("ok"), len(result.get("checks") or {}),
            (" failed=" + ",".join(bad)) if bad else "")
        return events.emit(kind="patrol_verdict", task=seat, agent=seat,
                           detail=detail[:300])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="平台质量门只读自检")
    ap.add_argument("--seats", default=",".join(DEFAULT_SEATS))
    ap.add_argument("--status-dir", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    seats = tuple(s.strip().upper() for s in args.seats.split(",") if s.strip())
    if kill_switch_on():
        if not args.quiet:
            print(json.dumps({"ok": False, "skipped": "kill_switch"}, ensure_ascii=False))
        return 0
    try:
        result = run_checks()
        record(result, seats, args.status_dir or None)
        audits = {s: emit_audit(s, result) for s in seats}
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    if not args.quiet:
        if args.json:
            print(json.dumps({"result": result, "audits": audits},
                             ensure_ascii=False, indent=1))
        else:
            for k, v in result["checks"].items():
                print("%-12s ok=%s rc=%s %sms" % (k, v.get("ok"), v.get("rc"),
                                                  v.get("elapsed_ms")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
