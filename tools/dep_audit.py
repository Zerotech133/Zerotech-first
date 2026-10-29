#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依赖锁校验 + 安全审计周检（P1-3，2026-10-30）。

三件事（全部只读，不安装任何包）：
  1. 锁校验：`uv lock --check --offline`（uv 在 py312/Scripts；缺失时退化为 mtime 检查）；
  2. 依赖漏洞：`python -m pip_audit -r requirements-dev.txt --format=json`（pip-audit 未装则记 skip）；
  3. 代码安全：`python -m bandit -r portal agent_platform tools -ll -f json`（未装则 skip + 安装指引）。

输出：一行摘要 + agent_platform/data/reports/dep_audit/<日期>.json|.md（history.jsonl 追加）。
用法：
  python tools/dep_audit.py            # 采集+落盘+打印
  python tools/dep_audit.py --check    # 有漏洞/锁不一致 → exit 1（周检告警）
  python tools/dep_audit.py --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT_DIR = REPO / "agent_platform" / "data" / "reports" / "dep_audit"
UV = Path(r"C:\项目\py312\Scripts\uv.exe")
REQ_DEV = REPO / "requirements-dev.txt"


def _run(cmd, timeout=180):
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def check_lock():
    if not UV.exists():
        lock = REPO / "uv.lock"
        py = REPO / "pyproject.toml"
        stale = (not lock.exists()) or (py.exists() and lock.stat().st_mtime < py.stat().st_mtime)
        return {"name": "uv_lock", "ok": not stale, "detail":
                "uv 缺失，退化 mtime 检查；stale=%s" % stale}
    rc, out, err = _run([str(UV), "lock", "--check", "--offline"], timeout=120)
    return {"name": "uv_lock", "ok": rc == 0, "rc": rc,
            "detail": ("lock 一致" if rc == 0 else ((out or err).strip().splitlines()[-1:] or [""])[0][:160])}


def check_pip_audit():
    try:
        import pip_audit  # noqa: F401
    except ImportError:
        return {"name": "pip_audit", "ok": None, "skipped": True,
                "detail": "pip-audit 未安装（pip install pip-audit 后启用）"}
    if not REQ_DEV.exists():
        return {"name": "pip_audit", "ok": None, "skipped": True,
                "detail": "requirements-dev.txt 缺失"}
    # 2026-10-30：requirements 含中文注释，pip-audit 内部按 locale(GBK) 读文件会崩；
    # 生成纯 ASCII 临时清单（只保留非注释/非空行）再审计。
    import tempfile
    lines = []
    for ln in REQ_DEV.read_text(encoding="utf-8").splitlines():
        t = ln.strip()
        if t and not t.startswith("#"):
            lines.append(t)
    fd, tmp_req = tempfile.mkstemp(prefix="dep_audit_req_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="ascii", errors="replace", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
        rc, out, err = _run([sys.executable, "-m", "pip_audit", "-r", tmp_req,
                             "--format", "json"], timeout=180)
    finally:
        try:
            os.unlink(tmp_req)
        except OSError:
            pass
    vulns = []
    try:
        data = json.loads(out) if out.strip() else {}
        deps = data.get("dependencies") if isinstance(data, dict) else data
        for dep in deps or []:
            for v in dep.get("vulns") or []:
                vulns.append("%s %s %s" % (dep.get("name"), dep.get("version"), v.get("id")))
    except ValueError:
        pass
    ok = rc == 0 and not vulns
    return {"name": "pip_audit", "ok": ok, "rc": rc, "vulns": vulns,
            "detail": ("无已知漏洞" if ok else
                       "rc=%d 漏洞=%d%s" % (rc, len(vulns),
                                           " 示例:" + ",".join(vulns[:3]) if vulns else
                                           " " + (err or "").strip().splitlines()[-1][:120]))}


def check_bandit():
    try:
        import bandit  # noqa: F401
    except ImportError:
        return {"name": "bandit", "ok": None, "skipped": True,
                "detail": "bandit 未安装（经批准后 pip install bandit 再启用）"}
    rc, out, err = _run([sys.executable, "-m", "bandit", "-r", "portal", "agent_platform",
                         "tools", "-ll", "-f", "json"], timeout=300)
    high = 0
    try:
        data = json.loads(out) if out.strip() else {}
        high = sum(1 for r in data.get("results") or []
                   if r.get("issue_severity") == "HIGH")
    except ValueError:
        pass
    return {"name": "bandit", "ok": (rc in (0, 1) and high == 0), "rc": rc,
            "high": high, "detail": "HIGH=%d（rc=%d）" % (high, rc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="依赖锁 + 安全审计周检（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="有失败项 → exit 1")
    args = ap.parse_args(argv)

    results = [check_lock(), check_pip_audit(), check_bandit()]
    failed = [r for r in results if r.get("ok") is False]
    line = "dep_audit lock=%s pip_audit=%s bandit=%s fails=%d" % (
        "ok" if results[0]["ok"] else "FAIL",
        "ok" if results[1].get("ok") else ("skip" if results[1].get("skipped") else "FAIL"),
        "ok" if results[2].get("ok") else ("skip" if results[2].get("skipped") else "FAIL"),
        len(failed),
    )
    payload = {"date": dt.date.today().isoformat(), "summary": line,
               "ok": not failed, "results": results}
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        day = payload["date"]
        (OUT_DIR / ("%s.json" % day)).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        with (OUT_DIR / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError as exc:
        print("[dep_audit] 落盘失败（不阻断）：%r" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(line)
        for r in results:
            mark = "✓" if r.get("ok") else ("-" if r.get("skipped") else "✗")
            print("  %s %-12s %s" % (mark, r["name"], r.get("detail", "")))
    return 1 if (args.check and failed) else 0


if __name__ == "__main__":
    sys.exit(main())
