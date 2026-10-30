# -*- coding: utf-8 -*-
"""数据管道只读自检工具（B2-b 情报/采集 7 席）。

目标清单：`agent_platform/config/data_pipeline_targets.json`，支持四种只读检查：
  - http            : GET /health 类存活
  - tcp             : 端口可连接（如 Neo4j bolt 17687）
  - file_freshness  : 目录/文件最新 mtime 不超过阈值（采集是否在产）
  - latest_json     : 目录内最新 *.json 可解析，并报告顶层键数/条目数（数据可读）
每次执行：逐目标检查 → 每席 `agent_platform/data/data_pipeline/<席>/status.json` + history.jsonl
→ 每席 `dev_events`（patrol_verdict，task=agent=席码）。不写业务、无出网（仅本机）。

环境/回滚：`DATA_PIPELINE_PROBE=off` 立即跳过；删除计划任务 + 本工具即回滚。
退出码：0 全部 ok；1 有 degraded；2 自身错误。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
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
DEFAULT_CONFIG = PLATFORM / "config" / "data_pipeline_targets.json"
KILL_ENV = "DATA_PIPELINE_PROBE"
KILL_VALUES = ("off", "0", "false", "no")
MAX_HISTORY = 2000


def kill_switch_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KILL_ENV) or "").strip().lower() in KILL_VALUES


def load_targets(config: Path) -> list:
    data = json.loads(Path(config).read_text(encoding="utf-8"))
    rows = data.get("targets") if isinstance(data, dict) else data
    return [
        t for t in rows or [] if isinstance(t, dict) and t.get("seat") and t.get("kind")
    ]


def _abs(path) -> Path:
    """相对路径一律按仓库根解析（计划任务 cwd 可能不同）。"""
    p = Path(str(path))
    return p if p.is_absolute() else (REPO / p)


def _http(url: str, timeout: float) -> dict:
    t0 = time.time()
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            data = r.read(65536)
            return {
                "ok": 200 <= r.status < 400,
                "status": r.status,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "bytes": len(data),
            }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": "http_%d" % exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "error": type(exc).__name__}


def _tcp(host: str, port: int, timeout: float) -> dict:
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {
                "ok": True,
                "host": host,
                "port": int(port),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }
    except OSError as exc:
        return {
            "ok": False,
            "host": host,
            "port": int(port),
            "error": type(exc).__name__,
        }


def _file_freshness(path: str, max_age_hours: float) -> dict:
    root = _abs(path)
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


def _latest_json(dir_path: str) -> dict:
    dir_path = str(_abs(dir_path))
    try:
        files = [p for p in Path(dir_path).glob("*.json") if p.is_file()]
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__, "path": str(dir_path)}
    if not files:
        return {"ok": False, "error": "no_json", "path": str(dir_path)}
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        obj = json.loads(latest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__, "file": str(latest)}
    keys = len(obj) if isinstance(obj, dict) else None
    items = None
    if isinstance(obj, dict):
        for k in ("items", "entries", "rows", "data"):
            if isinstance(obj.get(k), list):
                items = len(obj[k])
                break
    elif isinstance(obj, list):
        items = len(obj)
    return {
        "ok": True,
        "file": str(latest),
        "top_keys": keys,
        "items": items,
        "mtime": time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(latest.stat().st_mtime)
        ),
    }


def probe_target(t: dict, timeout: float = 5.0) -> dict:
    kind = str(t.get("kind"))
    if kind == "http":
        res = _http(str(t.get("url") or ""), timeout)
    elif kind == "tcp":
        res = _tcp(str(t.get("host") or "127.0.0.1"), int(t.get("port") or 0), timeout)
    elif kind == "file_freshness":
        res = _file_freshness(
            str(t.get("path") or ""), float(t.get("max_age_hours") or 24)
        )
    elif kind == "latest_json":
        res = _latest_json(str(t.get("path") or ""))
    else:
        res = {"ok": False, "error": "unsupported_kind", "kind": kind}
    res.update(
        {
            "kind": kind,
            "label": str(t.get("label") or ""),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    return res


def record(seat, result, status_root=None) -> Path:
    root = Path(status_root) if status_root else (PLATFORM / "data" / "data_pipeline")
    d = root / seat
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    hist = root / "history.jsonl"
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps({"seat": seat, **result}, ensure_ascii=False) + "\n")
    lines = hist.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_HISTORY:
        hist.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")
    return d


def emit_audit(seat: str, result: dict) -> dict:
    try:
        tools = str(REPO / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events  # noqa: WPS433

        k = result.get("kind")
        if k == "file_freshness":
            detail = "data_probe file age=%sh ok=%s path=%s" % (
                result.get("age_hours"),
                result.get("ok"),
                result.get("path"),
            )
        elif k == "latest_json":
            detail = "data_probe latest_json ok=%s items=%s file=%s" % (
                result.get("ok"),
                result.get("items"),
                result.get("file"),
            )
        elif k == "tcp":
            detail = "data_probe tcp %s:%s ok=%s" % (
                result.get("host"),
                result.get("port"),
                result.get("ok"),
            )
        else:
            detail = "data_probe http status=%s ok=%s" % (
                result.get("status"),
                result.get("ok"),
            )
        return events.emit(
            kind="patrol_verdict", task=seat, agent=seat, detail=detail[:300]
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据管道只读自检")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--targets", default="all")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--status-dir", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if kill_switch_on():
        if not args.quiet:
            print(
                json.dumps({"ok": False, "skipped": "kill_switch"}, ensure_ascii=False)
            )
        return 0
    try:
        targets = load_targets(Path(args.config))
        if args.targets != "all":
            wanted = {s.strip().upper() for s in args.targets.split(",") if s.strip()}
            targets = [t for t in targets if str(t["seat"]).upper() in wanted]
        if not targets:
            print(json.dumps({"ok": False, "error": "no_targets"}, ensure_ascii=False))
            return 2
        results = []
        for t in targets:
            seat = str(t["seat"]).upper()
            res = probe_target(t, args.timeout)
            record(seat, res, args.status_dir or None)
            results.append(
                {"seat": seat, "result": res, "audit": emit_audit(seat, res)}
            )
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
                    "%-10s %-15s ok=%s %s"
                    % (x["seat"], r.get("kind"), r.get("ok"), r.get("error") or "")
                )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
