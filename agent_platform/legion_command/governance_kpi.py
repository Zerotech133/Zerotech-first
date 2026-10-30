# -*- coding: utf-8 -*-
"""治理 KPI 聚合（只读；供 8796 `GET /api/governance/kpi`）。

数据源：tasks.json、git worktree、服务健康、最近质量门日志、夜间安全审计状态、
最新 OSV 报告、zhaoyun_deadlines.json。全部只读；单源失败写 null/unknown，不中断整体。
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[2]
TMP = Path(r"C:\Temp")
HEALTH_URLS = {
    "8796": "http://127.0.0.1:8796/api/health",
    "8091": "http://127.0.0.1:8091/health",
    "6006": "http://127.0.0.1:6006/health",
}


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _read_text_any(path: Path) -> str:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _http_ok(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_open(port: int, timeout: float = 0.8) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _hermes_health() -> dict:
    """Hermes 运行域只读健康（8787 WebUI / 8788 next / 8093 OrgRegistry / 8771 OpenHands / 8103 LDE）。"""
    checks = {
        "8787_webui": _http_ok("http://127.0.0.1:8787/"),
        "8788_next": _port_open(8788),
        "8093_org_registry": _http_ok("http://127.0.0.1:8093/"),
        "8771_openhands": _http_ok("http://127.0.0.1:8771/health"),
        "8103_lde": _port_open(8103),
    }
    return {
        "checks": checks,
        "ok": all(checks.values()),
        "note": "8788 next 由 HermesWebUI-Next-Guard(Disabled) 管理；生产入口=8787（邓芝·飞书通讯中枢）",
    }


def _gate_status(tmp: Path) -> str:
    logs = sorted(
        set(glob.glob(str(tmp / "test_gate_*_*.log")) + glob.glob(str(tmp / "gate_after_*.log"))
            + glob.glob(str(tmp / "audit_gate_*.log"))),
        key=lambda p: Path(p).stat().st_mtime, reverse=True)
    for lp in logs:
        txt = _read_text_any(Path(lp))
        if "GATE PASS" in txt:
            return "PASS"
        if "GATE FAIL" in txt:
            return "FAIL"
    return "UNKNOWN"


def _worktrees(repo: Path) -> Optional[int]:
    try:
        r = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15)
        if r.returncode != 0:
            return None
        return sum(1 for ln in (r.stdout or "").splitlines() if ln.startswith("worktree "))
    except (OSError, subprocess.SubprocessError):
        return None


def _deadlines(repo: Path, now: dt.datetime) -> list[dict]:
    data = _read_json(repo / "tools" / "zhaoyun_deadlines.json") or {}
    out = []
    for it in data.get("items") or []:
        rec = {"id": it.get("id"), "title": it.get("title"), "owner": it.get("owner")}
        if it.get("recurring"):
            rec.update({"state": "recurring", "due": it.get("recurring"), "days_left": None})
        elif it.get("due"):
            try:
                d = dt.datetime.fromisoformat(it["due"])
                now_cmp = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
                if d.tzinfo is not None:
                    d = d.astimezone().replace(tzinfo=None)
                rec["days_left"] = (d - now_cmp).days
                rec["due"] = it["due"]
                rec["state"] = "overdue" if d < now_cmp else ("due_soon" if (d - now_cmp).days <= 1 else "upcoming")
            except ValueError:
                rec.update({"state": "bad_due", "due": it.get("due"), "days_left": None})
        else:
            rec.update({"state": "unknown", "due": None, "days_left": None})
        out.append(rec)
    return out


def collect(repo: Path | None = None, tmp: Path | None = None,
            now: dt.datetime | None = None, health: dict | None = None) -> dict:
    repo = Path(repo or REPO)
    tmp = Path(tmp or TMP)
    now = now or dt.datetime.now()

    tasks = _read_json(repo / ".worktrees" / "tasks.json") or {}
    by_status: dict[str, int] = {}
    for v in tasks.values():
        s = str(v.get("status") or "unknown")
        by_status[s] = by_status.get(s, 0) + 1

    if health is None:
        health = {k: _http_ok(u) for k, u in HEALTH_URLS.items()}

    status = _read_json(tmp / "security_audit_status.json") or {}
    bandit = {
        "high": int(status.get("bandit_high") or 0),
        "medium": int(status.get("bandit_medium") or 0),
        "low": int(status.get("bandit_low") or 0),
        "regression": bool(status.get("bandit_regression")),
    } if status else None

    osv_paths = sorted(glob.glob(str(tmp / "pip_osv*.json")),
                       key=lambda p: Path(p).stat().st_mtime, reverse=True)
    osv = _read_json(Path(osv_paths[0])) if osv_paths else None
    osv_summary = {
        "packages": osv.get("vulnerable_packages"),
        "vulns": osv.get("vulnerabilities"),
        "scanned": osv.get("scanned"),
    } if osv else None

    return {
        "ok": True,
        "generated_at": now.astimezone().isoformat(timespec="seconds"),
        "board": {"total": len(tasks), "by_status": by_status},
        "worktrees": _worktrees(repo),
        "health": health,
        "gate": _gate_status(tmp),
        "bandit": bandit,
        "osv": osv_summary,
        "deadlines": _deadlines(repo, now),
        "hermes": _hermes_health(),
        "note": "只读 KPI 聚合；数据源不可用时字段为 null/UNKNOWN，不猜测",
    }
