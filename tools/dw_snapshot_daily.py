# -*- coding: utf-8 -*-
"""dw_snapshot 每日刷新 + 一致性校验（《数据仓研发计划 v3》S1-06）。

背景：驾驶舱数据 portal/static/dw_snapshot.json 此前靠手动刷新，会再旧
（S1 现状表：「驾驶舱 dw_snapshot 手动刷新，无调度 ⚠️ 会再旧」）。本脚本是
每日计划任务的**脚本化载体**（计划任务本身属 L2，见 docs/plans 同批申请单）：

  1) 以仓库根为 cwd 调 tools/dw_dashboard.py 重生成 portal/static/dw_snapshot.json；
  2) 校验：generated_at=当日（且为本次刷新产物）、catalog 版本与当前 catalog.json
     一致、lagging_daily 结构/数量（可选 --max-lagging 上限）；
  3) 结果追加写 C:\\Temp\\dw_snapshot_daily.log（JSONL，一行一次运行）；
  4) 失败 exit 1（fail-visible；计划任务 LastTaskResult 非 0 可被退出码审计捕获）。

只读边界：本脚本只写 dashboard 的既定产物 snapshot 与自身日志；不写数据仓、
不改 catalog、不装/改 Windows 计划任务、不重启任何服务。

用法：
    python tools/dw_snapshot_daily.py [--json] [--max-lagging N] [--timeout 秒]
退出码：0=刷新且校验通过；1=失败（dashboard 失败 / 校验失败 / 异常，均会写日志）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DASHBOARD = REPO / "tools" / "dw_dashboard.py"
SNAPSHOT = REPO / "portal" / "static" / "dw_snapshot.json"
CATALOG = REPO / "quant_backtest" / "data" / "catalog.json"
LOG_PATH = Path(r"C:\Temp\dw_snapshot_daily.log")
PY312 = Path(r"C:\项目\py312\python.exe")
TIMEOUT_SECONDS = 300  # 实测 dashboard 约 27s（含 automation_supervisor 子进程）
FRESH_TOLERANCE_SECONDS = 300  # generated_at 允许与运行窗口的时钟偏差


def _python() -> str:
    """子进程解释器：优先 py312（dashboard 内子进程也用它），否则当前解释器。"""
    return str(PY312) if PY312.is_file() else sys.executable


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:  # GBK 控制台兜底
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
        sys.stdout.flush()


def _iso_now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_iso(value):
    """ISO 时间串 → datetime；不可解析返回 None。"""
    try:
        return dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _run_dashboard(python: str, timeout: float = TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """跑 tools/dw_dashboard.py（cwd=仓库根，输出落 stdout/stderr 由调用方记录）。"""
    return subprocess.run(
        [python, "-X", "utf8", str(DASHBOARD)],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout)


def _check(name: str, ok: bool, detail) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def catalog_version_match(catalog_version, snap_version) -> bool:
    """catalog 版本一致性：两侧均可读且相等（catalog.json 缺失时一律判失败）。"""
    return catalog_version is not None and snap_version is not None and catalog_version == snap_version


def build_checks(dashboard_rc, snap, catalog, snapshot_path: Path,
                 started_at: float, today: dt.date, max_lagging) -> list:
    """五项一致性校验（纯函数，便于单测）：dashboard rc / snapshot / generated_at / catalog / lagging。"""
    checks = [_check("dashboard_rc", dashboard_rc == 0, "rc=%s" % dashboard_rc)]

    snap_ok = isinstance(snap, dict)
    checks.append(_check("snapshot_parse", snap_ok,
                         str(snapshot_path) if snap_ok else "缺失或非 JSON 对象"))

    # snapshot 是否为本次运行新写（防「旧文件通过校验」）
    try:
        mtime = snapshot_path.stat().st_mtime
        checks.append(_check("snapshot_refreshed", mtime >= started_at - FRESH_TOLERANCE_SECONDS,
                             "mtime=%s" % dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")))
    except OSError as exc:
        checks.append(_check("snapshot_refreshed", False, "stat 失败：%s" % str(exc)[:120]))

    generated_at = None
    if snap_ok:
        generated_at = snap.get("generated_at")
        parsed = parse_iso(generated_at)
        checks.append(_check(
            "generated_at_today",
            parsed is not None and parsed.date() == today,
            "generated_at=%s / today=%s" % (generated_at, today.isoformat())))
        fresh_ok = False
        lag_seconds = None
        if parsed is not None:
            lag_seconds = round(time.time() - parsed.timestamp(), 1)
            fresh_ok = -FRESH_TOLERANCE_SECONDS <= lag_seconds <= FRESH_TOLERANCE_SECONDS
        checks.append(_check("generated_at_fresh", fresh_ok,
                             "距生成 %s 秒（容差 ±%d）" % (lag_seconds, FRESH_TOLERANCE_SECONDS)))
    else:
        checks.append(_check("generated_at_today", False, "无 snapshot"))
        checks.append(_check("generated_at_fresh", False, "无 snapshot"))

    catalog_version = (catalog or {}).get("version")
    snap_cat = (snap or {}).get("catalog") if snap_ok else None
    if not isinstance(snap_cat, dict):
        snap_cat = {}
    snap_version = snap_cat.get("version")
    checks.append(_check("catalog_version", catalog_version_match(catalog_version, snap_version),
                         "snapshot=%s / catalog.json=%s" % (snap_version, catalog_version)))

    catalog_tables = len(((catalog or {}).get("tables") or {}))
    snap_tables = snap_cat.get("tables")
    checks.append(_check("catalog_tables", snap_tables == catalog_tables,
                         "snapshot=%s / catalog.json=%s" % (snap_tables, catalog_tables)))

    # lagging_daily：结构（name/lag 齐备）与数量（可选上限）
    lagging = (snap or {}).get("lagging_daily") if snap_ok else None
    lagging_ok = isinstance(lagging, list) and all(
        isinstance(x, dict) and x.get("name")
        and isinstance(x.get("lag"), int) and not isinstance(x.get("lag"), bool)
        for x in lagging)
    count = len(lagging) if isinstance(lagging, list) else None
    checks.append(_check("lagging_structure", lagging_ok, "count=%s" % count))
    limit_ok = max_lagging is None or (count is not None and count <= max_lagging)
    checks.append(_check("lagging_count", limit_ok,
                         "count=%s / 上限=%s" % (count, "未设" if max_lagging is None else max_lagging)))
    return checks


def _append_log(record: dict, log_path: Path) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(python=None, runner=None, snapshot_path=None, catalog_path=None,
        log_path=None, timeout: float = TIMEOUT_SECONDS, max_lagging=None) -> dict:
    """刷新 + 校验一次（依赖可注入，供单测用临时目录/假 runner）。

    runner(python) → 带 returncode/stdout/stderr 的对象（默认真跑 dashboard）。
    返回报告 dict（含 rc/ok/checks/generated_at/lagging_count/log）。
    """
    started_at = time.time()
    started_iso = _iso_now()
    python = python or _python()
    snapshot_path = Path(snapshot_path or SNAPSHOT)
    catalog_path = Path(catalog_path or CATALOG)
    log_path = Path(log_path or LOG_PATH)

    dashboard_rc, stdout, stderr, error = None, "", "", None
    try:
        proc = (runner or (lambda py: _run_dashboard(py, timeout)))(python)
        dashboard_rc = getattr(proc, "returncode", None)
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
    except Exception as exc:  # noqa: BLE001 - 超时/启动失败统一判失败
        error = "%s: %s" % (type(exc).__name__, str(exc)[:300])

    snap = _read_json(snapshot_path) if snapshot_path.is_file() else None
    catalog = _read_json(catalog_path) or {}
    today = dt.date.today()
    checks = build_checks(dashboard_rc, snap if isinstance(snap, dict) else None,
                          catalog, snapshot_path, started_at, today, max_lagging)
    if error:
        checks.append(_check("dashboard_error", False, error))

    report = {
        "tool": "dw_snapshot_daily",
        "started_at": started_iso,
        "finished_at": _iso_now(),
        "duration_s": round(time.time() - started_at, 1),
        "snapshot": str(snapshot_path),
        "log": str(log_path),
        "dashboard_rc": dashboard_rc,
        "generated_at": (snap or {}).get("generated_at") if isinstance(snap, dict) else None,
        "catalog_version": ((snap or {}).get("catalog") or {}).get("version")
        if isinstance(snap, dict) and isinstance((snap or {}).get("catalog"), dict) else None,
        "lagging_count": len(snap.get("lagging_daily") or []) if isinstance(snap, dict) else None,
        "checks": checks,
        "stdout_tail": (stdout or "").strip()[-300:],
        "stderr_tail": (stderr or "").strip()[-300:],
    }
    report["ok"] = all(c["ok"] for c in checks)
    report["rc"] = 0 if report["ok"] else 1

    # 结果落日志（失败也要落；日志写不出=fail-visible 失败，但不再抛异常）
    try:
        _append_log(report, log_path)
    except OSError as exc:
        report["checks"].append(_check("log_written", False, str(exc)[:200]))
        report["ok"] = False
        report["rc"] = 1
    else:
        report["checks"].append(_check("log_written", True, str(log_path)))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dw_snapshot 每日刷新 + 一致性校验（只读校验面）")
    ap.add_argument("--json", action="store_true", help="输出机器可读报告")
    ap.add_argument("--max-lagging", type=int, default=None,
                    help="lagging_daily 数量上限（默认不设限，只记录）")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS,
                    help="dashboard 子进程超时秒数（默认 %(default)s）")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    report = run(timeout=args.timeout, max_lagging=args.max_lagging)
    if args.json:
        _safe_print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _safe_print("[dw_snapshot_daily] %s（%ss）" % (report["finished_at"], report["duration_s"]))
        for c in report["checks"]:
            _safe_print("  %s %-18s %s" % ("✓" if c["ok"] else "✗", c["check"], c["detail"]))
        _safe_print("RESULT: %s rc=%s generated_at=%s catalog_version=%s lagging_count=%s log=%s" % (
            "OK" if report["ok"] else "FAIL", report["rc"], report["generated_at"],
            report["catalog_version"], report["lagging_count"], report["log"]))
    return int(report["rc"])


if __name__ == "__main__":
    raise SystemExit(main())
