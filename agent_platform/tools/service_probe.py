# -*- coding: utf-8 -*-
"""运行体/通信线 · 多目标探活工具（B2-a 真成员化）。

目标清单：`agent_platform/config/service_probe_targets.json`（席码 → http / file_freshness）。
每次执行：逐目标只读探活 → 每席状态写 `agent_platform/data/service_probe/<seat>/status.json`
→ 全局 `history.jsonl` → 每席 `dev_events`（kind=patrol_verdict，task=agent=席码）。
不写业务、不发消息、不重启服务、无出网（仅 127.0.0.1）。

环境/回滚：`SERVICE_PROBE=off` 立即跳过；删除计划任务 + 本工具即完全回滚。

退出码：0 全部 ok；1 有 degraded；2 自身错误。
用法：
  python agent_platform/tools/service_probe.py --targets all --json
  python agent_platform/tools/service_probe.py --targets LUSU1,JEV_GATE --quiet
"""

from __future__ import annotations

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

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
DEFAULT_CONFIG = PLATFORM / "config" / "service_probe_targets.json"
KILL_ENV = "SERVICE_PROBE"
KILL_VALUES = ("off", "0", "false", "no")
MAX_HISTORY = 2000


def kill_switch_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KILL_ENV) or "").strip().lower() in KILL_VALUES


def load_targets(config: Path) -> list:
    data = json.loads(Path(config).read_text(encoding="utf-8"))
    rows = data.get("targets") if isinstance(data, dict) else data
    out = []
    for t in rows or []:
        if isinstance(t, dict) and t.get("seat") and t.get("kind"):
            out.append(t)
    return out


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


def _file_freshness(path: str, max_age_hours: float) -> dict:
    """目录取最新文件 mtime；文件取其 mtime；age ≤ 阈值即 ok。"""
    root = Path(path)
    try:
        if root.is_dir():
            latest = max(
                (f.stat().st_mtime for f in root.rglob("*") if f.is_file()), default=0
            )
        elif root.is_file():
            latest = root.stat().st_mtime
        else:
            return {"ok": False, "error": "not_found", "path": str(path)}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__, "path": str(path)}
    if not latest:
        return {"ok": False, "error": "empty", "path": str(path)}
    age_h = (time.time() - latest) / 3600.0
    return {
        "ok": age_h <= max_age_hours,
        "age_hours": round(age_h, 2),
        "max_age_hours": max_age_hours,
        "path": str(path),
    }


def probe_target(t: dict, timeout: float = 5.0) -> dict:
    kind = str(t.get("kind"))
    if kind == "http":
        res = _http(str(t.get("url") or ""), timeout)
    elif kind == "file_freshness":
        res = _file_freshness(
            str(t.get("path") or ""), float(t.get("max_age_hours") or 24)
        )
    else:
        res = {"ok": False, "error": "unsupported_kind", "kind": kind}
    res.update(
        {
            "kind": kind,
            "label": str(t.get("label") or ""),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    # 拍板为 OPTIONAL_OFFLINE 的服务（如 9104 飞书卡片回调）：离线属设计内，不判 degraded
    if t.get("expected_offline"):
        res["expected_offline"] = True
        res["was_online"] = bool(res.get("ok"))
        if not res.get("ok"):
            res["note"] = "expected_offline：按拍板 OPTIONAL_OFFLINE，离线属正常"
            res["ok"] = True
    return res


def record(seat: str, result: dict, status_root=None) -> Path:
    root = Path(status_root) if status_root else (PLATFORM / "data" / "service_probe")
    seat_dir = root / seat
    seat_dir.mkdir(parents=True, exist_ok=True)
    (seat_dir / "status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    hist = root / "history.jsonl"
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps({"seat": seat, **result}, ensure_ascii=False) + "\n")
    lines = hist.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_HISTORY:
        hist.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")
    return seat_dir


def emit_audit(seat: str, result: dict) -> dict:
    try:
        tools = str(REPO / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events  # noqa: WPS433

        if result["kind"] == "http":
            detail = "service_probe http status=%s(%sms) ok=%s" % (
                result.get("status"),
                result.get("elapsed_ms"),
                result.get("ok"),
            )
        else:
            detail = "service_probe file_freshness age=%sh ok=%s path=%s" % (
                result.get("age_hours"),
                result.get("ok"),
                result.get("path"),
            )
        return events.emit(
            kind="patrol_verdict", task=seat, agent=seat, detail=detail[:300]
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="运行体/通信线多目标探活")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--targets", default="all", help="all 或逗号分隔席码（默认 all）")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--status-dir", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
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
        targets = load_targets(Path(args.config))
        if args.targets != "all":
            wanted = {s.strip().upper() for s in args.targets.split(",") if s.strip()}
            targets = [t for t in targets if str(t.get("seat")).upper() in wanted]
        if not targets:
            print(json.dumps({"ok": False, "error": "no_targets"}, ensure_ascii=False))
            return 2
        results = []
        for t in targets:
            seat = str(t["seat"]).upper()
            res = probe_target(t, args.timeout)
            record(seat, res, args.status_dir or None)
            audit = emit_audit(seat, res)
            results.append({"seat": seat, "result": res, "audit": audit})
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]},
                ensure_ascii=False,
            )
        )
        return 2

    ok_count = sum(1 for x in results if x["result"].get("ok"))
    summary = {
        "ok": ok_count == len(results),
        "total": len(results),
        "ok_count": ok_count,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": results,
    }
    if not args.quiet:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        else:
            for x in results:
                r = x["result"]
                print(
                    "%-12s %-14s ok=%s %s"
                    % (x["seat"], r.get("kind"), r.get("ok"), r.get("error") or "")
                )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
