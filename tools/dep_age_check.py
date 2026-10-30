#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依赖版本年龄检查（O8 P3b，warn-only，只读）。

读 ``requirements/lines/*.lock``（pip freeze 格式）或 ``uv.lock``（TOML），
查 PyPI 上该版本的发布时间；**小于 N 天**（默认 7）的版本给 warn，
用于落实「依赖升级与发布年龄」纪律（见
``docs/standards/2026-10-27_依赖升级与发布年龄_v1.md``）。

- 只读：不安装、不升级、不改 lock；
- 默认 warn-only（rc=0）；``--strict`` 时有 fresh/error 返回 rc=1；
- 不打印任何凭据；只打印包名/版本/发布时间。

用法::

    python -X utf8 tools/dep_age_check.py                       # 默认三条 lock
    python -X utf8 tools/dep_age_check.py --limit 20            # 冒烟
    python -X utf8 tools/dep_age_check.py --strict --json       # CI 用
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = [
    REPO / "requirements" / "lines" / "system_py311_full.lock",
    REPO / "requirements" / "lines" / "quant_backtest.lock",
    REPO / "requirements" / "lines" / "cowagent.lock",
    REPO / "uv.lock",
]
_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^;\s]+)$")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def parse_pip_freeze(text: str) -> list[tuple[str, str]]:
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _NAME_RE.match(line)
        if not m:
            continue
        key = (m.group(1), m.group(2))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def parse_uv_lock_text(text: str) -> list[tuple[str, str]]:
    data = tomllib.loads(text)
    out, seen = [], set()
    for pkg in data.get("package") or []:
        if not isinstance(pkg, dict):
            continue
        name, ver = pkg.get("name"), pkg.get("version")
        src = pkg.get("source") or {}
        if not name or not ver:
            continue
        # 跳过 virtual/editable 项目自身（非 PyPI 发布物）
        if isinstance(src, dict) and ("virtual" in src or "editable" in src):
            continue
        key = (str(name), str(ver))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def parse_file(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if "[[package]]" in text:
        return parse_uv_lock_text(text)
    return parse_pip_freeze(text)


def fetch_pypi_release_time(name: str, version: str, timeout: float = 10.0) -> datetime | None:
    """返回该版本最早文件的发布时间；版本不存在返回 None。"""
    url = "https://pypi.org/pypi/%s/json" % urllib.parse.quote(normalize_name(name))
    with safe_urlopen(url, timeout=timeout) as r:
        data = json.load(r)
    files = ((data.get("releases") or {}).get(str(version)) or [])
    times = []
    for f in files:
        raw = f.get("upload_time_iso_8601") or f.get("upload_time")
        if not raw:
            continue
        try:
            times.append(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            continue
    if not times:
        return None
    return min(times)


def check(entries, fetcher=fetch_pypi_release_time, now: datetime | None = None,
          min_age_days: int = 7, workers: int = 8, timeout: float = 10.0,
          limit: int = 0) -> dict:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(min_age_days))
    todo = list(entries)[: limit if limit and limit > 0 else None]
    results, errors = [], []

    def _work(item):
        name, version = item
        try:
            return name, version, fetcher(name, version, timeout), None
        except Exception as exc:  # noqa: BLE001
            return name, version, None, "%s: %s" % (type(exc).__name__, str(exc)[:160])

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futs = [ex.submit(_work, e) for e in todo]
        for fut in as_completed(futs):
            name, version, released, err = fut.result()
            if err:
                errors.append({"name": name, "version": version, "error": err})
                continue
            if released is None:
                errors.append({"name": name, "version": version,
                               "error": "version-not-found-on-pypi"})
                continue
            if released > cutoff:
                age_days = (now - released).total_seconds() / 86400.0
                results.append({
                    "name": name, "version": version, "status": "fresh",
                    "released": released.date().isoformat(),
                    "age_days": round(age_days, 1),
                })
    fresh = [r for r in results if r["status"] == "fresh"]
    fresh.sort(key=lambda r: (r["age_days"], r["name"]))
    return {
        "ok": not errors,
        "checked": len(todo),
        "fresh": fresh,
        "fresh_count": len(fresh),
        "errors": errors,
        "error_count": len(errors),
        "min_age_days": int(min_age_days),
        "cutoff": cutoff.date().isoformat(),
        "warn_only": True,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="依赖版本年龄检查（只读、默认 warn-only）")
    ap.add_argument("--path", action="append", default=None,
                    help="lock/requirements 文件（可多次；默认 requirements/lines/*.lock + uv.lock）")
    ap.add_argument("--min-age-days", type=int, default=7)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=0, help="只检查前 N 个包（冒烟用；0=全部）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="fresh/error 时 rc=1（默认 warn-only rc=0）")
    args = ap.parse_args(argv)

    paths = [Path(p) for p in (args.path or DEFAULT_PATHS)]
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("没有可读的 lock/requirements 文件", file=sys.stderr)
        return 2
    entries, files_info = [], []
    for p in paths:
        try:
            parsed = parse_file(p)
        except Exception as exc:  # noqa: BLE001
            print("解析失败 %s: %s" % (p, exc), file=sys.stderr)
            continue
        files_info.append({"path": str(p), "count": len(parsed)})
        entries.extend(parsed)
    # 去重（同名同版本）
    uniq, seen = [], set()
    for e in entries:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    result = check(uniq, min_age_days=args.min_age_days, workers=args.workers,
                   timeout=args.timeout, limit=args.limit)
    result["files"] = files_info
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("[dep_age_check] files=%d entries=%d checked=%d fresh=%d errors=%d cutoff=%s (warn-only)" % (
            len(files_info), len(uniq), result["checked"], result["fresh_count"],
            result["error_count"], result["cutoff"]))
        for r in result["fresh"]:
            print("  WARN fresh %s==%s released=%s age=%.1fd" % (
                r["name"], r["version"], r["released"], r["age_days"]))
        for e in result["errors"][:20]:
            print("  WARN error %s==%s %s" % (e["name"], e["version"], e["error"]))
        if result["fresh_count"] or result["error_count"]:
            print("  → 仅告警，不阻断；收紧纪律再加 --strict")
    if args.strict and (result["fresh_count"] or result["error_count"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
