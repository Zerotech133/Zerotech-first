# -*- coding: utf-8 -*-
"""HERMES_UI（糜竺）· 门面探活工具（真运行体成员的最小工具集）。

职责：对 Hermes WebUI（默认 http://127.0.0.1:8787）做一次**确定性只读探活**，
产出结构化结果 → 写状态/历史文件 → 写 dev_events 审计。不写业务、不发消息、不重启服务。

用法：
  python agent_platform/tools/hermes_ui_probe.py            # 人类可读
  python agent_platform/tools/hermes_ui_probe.py --json     # JSON 输出
  python agent_platform/tools/hermes_ui_probe.py --quiet    # 计划任务用（只写盘/事件）

环境/回滚：
  HERMES_UI_PROBE=off  → 立即跳过（kill switch）
  删除计划任务 + 本工具文件即完全回滚。

退出码：0 探活成功；1 探活失败/degraded；2 自身错误。
"""

from __future__ import annotations



import logging
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen


try:  # Phase B1: 8787 密码认证后自动带 hermes_session
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
    from agent_platform.legion_command.hermes_auth import auth_urlopen as safe_urlopen  # noqa: E402
except Exception:  # noqa: BLE001
    logging.getLogger(__name__).exception("swallowed exception at line 49")
    pass

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
DEFAULT_BASE = "http://127.0.0.1:8787"
KILL_ENV = "HERMES_UI_PROBE"
KILL_VALUES = ("off", "0", "false", "no")
MAX_HISTORY = 2000


def kill_switch_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KILL_ENV) or "").strip().lower() in KILL_VALUES


def _http(url: str, timeout: float) -> dict:
    t0 = time.time()
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            body = r.read(65536)
            return {
                "ok": 200 <= r.status < 400,
                "status": r.status,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "bytes": len(body),
                "body_head": body[:200].decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "error": "http_%d" % exc.code,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "error": type(exc).__name__,
        }


def probe(base: str = DEFAULT_BASE, timeout: float = 5.0) -> dict:
    base = base.rstrip("/")
    return {
        "schema": "hermes_ui_probe/v1",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base": base,
        "checks": {
            "health": _http(base + "/health", timeout),
            "root": _http(base + "/", timeout),
        },
    }


def record(result: dict, root=None) -> Path:
    data = Path(root) if root else (PLATFORM / "data" / "hermes_ui")
    data.mkdir(parents=True, exist_ok=True)
    hist = data / "history.jsonl"
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    lines = hist.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_HISTORY:
        hist.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")
    (data / "status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return data


def emit_audit(result: dict) -> dict:
    """写 dev_events（patrol_verdict）；失败静默（事件层不挡业务）。"""
    try:
        tools = str(REPO / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events  # noqa: WPS433

        h = result["checks"]["health"]
        r = result["checks"]["root"]
        detail = "HERMES_UI probe health=%s(%sms) root=%s(%sms)" % (
            h.get("status"),
            h.get("elapsed_ms"),
            r.get("status"),
            r.get("elapsed_ms"),
        )
        return events.emit(
            kind="patrol_verdict", task="HERMES_UI", agent="HERMES_UI", detail=detail
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def ok(result: dict) -> bool:
    """成功判据：/health 2xx/3xx；/ 不作硬要求（但会记录）。"""
    return bool((result.get("checks") or {}).get("health", {}).get("ok"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HERMES_UI 门面只读探活")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--status-dir",
        default="",
        help="状态输出目录（默认 agent_platform/data/hermes_ui）",
    )
    args = ap.parse_args(argv)

    if kill_switch_on():
        if not args.quiet:
            print(
                json.dumps(
                    {"ok": False, "skipped": "kill_switch", "env": KILL_ENV},
                    ensure_ascii=False,
                )
            )
        return 0
    try:
        result = probe(args.base, args.timeout)
        result["ok"] = ok(result)
        record(result, args.status_dir or None)
        audit = emit_audit(result)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]},
                ensure_ascii=False,
            )
        )
        return 2

    if not args.quiet:
        if args.json:
            print(
                json.dumps(
                    {"result": result, "audit": audit}, ensure_ascii=False, indent=1
                )
            )
        else:
            h = result["checks"]["health"]
            print(
                "HERMES_UI probe ok=%s health=%s elapsed=%sms base=%s"
                % (result["ok"], h.get("status"), h.get("elapsed_ms"), result["base"])
            )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
