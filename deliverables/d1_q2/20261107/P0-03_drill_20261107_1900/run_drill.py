# -*- coding: utf-8 -*-
"""P0-03 UI-only rollback drill runner.

- preflight: record baseline, backup nginx.conf, no production change.
- execute: only inside approved 19:00-19:10 window; apply temp 503 for /d1 UI,
  verify, hold briefly, restore from exact bytes, verify, write JSON.
- Never restarts 8091/8789/Watch; only docker exec nginx -t/-s reload.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r"C:\项目\老田的AI助手")
HOST_CONF = ROOT / "portal" / "nginx.conf"
DRILL_DIR = ROOT / "deliverables" / "d1_q2" / "20261107" / "P0-03_drill_20261107_1900"
BACKUP = DRILL_DIR / "nginx.conf.before"
TEMP_CONF = Path(r"C:\Temp\p0_03_nginx_drill.conf")
APPROVAL = DRILL_DIR / "approval.md"
BASELINE = DRILL_DIR / "baseline_before.json"
RESULT = DRILL_DIR / "drill_result.json"
TIMELINE = DRILL_DIR / "timeline.log"
TZ = timezone(timedelta(hours=8))

SERVICES = {
    "d1": "http://127.0.0.1:8734/d1",
    "d1_slash": "http://127.0.0.1:8734/d1/",
    "d1_research": "http://127.0.0.1:8734/d1/research",
    "d1_signoff": "http://127.0.0.1:8734/d1/q2/signoff",
    "d1_query": "http://127.0.0.1:8734/d1?x=1",
    "d1_q2_direct": "http://127.0.0.1:8734/d1_q2/signoff.html",
    "api_health": "http://127.0.0.1:8091/api/health",
    "api_d1_decisions": "http://127.0.0.1:8091/api/d1/decisions",
    "watch_health": "http://127.0.0.1:8789/health",
}


def now() -> datetime:
    return datetime.now(TZ)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def log(line: str) -> None:
    with TIMELINE.open("a", encoding="utf-8") as f:
        f.write(f"{stamp()} {line}\n")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_path(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def status(url: str, timeout: float = 5.0) -> dict:
    from urllib.parse import urlsplit

    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        conn.request("GET", path)
        r = conn.getresponse()
        body = r.read(200)
        return {"url": url, "status": r.status, "body_prefix": body.decode("utf-8", "replace")[:80], "error": ""}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": None, "body_prefix": "", "error": str(e)}
    finally:
        conn.close()


def matrix() -> dict:
    return {k: status(v) for k, v in SERVICES.items()}


def docker(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def nginx_test() -> dict:
    p = docker("exec", "ai-portal", "nginx", "-t")
    return {"rc": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


def nginx_reload() -> dict:
    p = docker("exec", "ai-portal", "nginx", "-s", "reload")
    return {"rc": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


def container_hash() -> str:
    p = docker("exec", "ai-portal", "sha256sum", "/etc/nginx/conf.d/default.conf")
    return p.stdout.split()[0] if p.returncode == 0 and p.stdout.split() else ""


def git_head() -> str:
    p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True)
    return p.stdout.strip()


def data_hashes() -> dict:
    paths = {
        "d1_decisions": ROOT / "agent_platform" / "data" / "d1_decisions.jsonl",
        "d1_handoffs": ROOT / "agent_platform" / "data" / "d1_handoffs.jsonl",
        "paper_state": ROOT / "agent_platform" / "data" / "paper" / "state.json",
    }
    return {k: (sha256_path(p) if p.exists() else "") for k, p in paths.items()}


def approval_ok() -> bool:
    if not APPROVAL.exists():
        return False
    text = APPROVAL.read_text(encoding="utf-8")
    return "状态：**APPROVED" in text and "19:00" in text and "19:10" in text


def preflight() -> dict:
    if not approval_ok():
        raise SystemExit("approval record missing or not APPROVED")
    if not TEMP_CONF.exists():
        raise SystemExit("temp drill config missing")
    base = HOST_CONF.read_bytes()
    BACKUP.write_bytes(base)
    base_hash = sha256_bytes(base)
    temp_hash = sha256_path(TEMP_CONF)
    result = {
        "schema": "d1_p0_03_drill_preflight/v1",
        "checked_at": stamp(),
        "approval": str(APPROVAL),
        "approval_ok": True,
        "git_head": git_head(),
        "host_conf": str(HOST_CONF),
        "host_sha256_before": base_hash,
        "backup": str(BACKUP),
        "backup_sha256": sha256_path(BACKUP),
        "container_sha256_before": container_hash(),
        "temp_conf_sha256": temp_hash,
        "nginx_test_before": nginx_test(),
        "route_matrix_before": matrix(),
        "data_hashes_before": data_hashes(),
    }
    BASELINE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("PREFLIGHT ok; host_hash=%s temp_hash=%s" % (base_hash, temp_hash))
    return result


def ensure_restore(reason: str) -> None:
    """Restore exact baseline bytes if backup exists; safe to call repeatedly."""
    if not BACKUP.exists():
        return
    base = BACKUP.read_bytes()
    with HOST_CONF.open("wb") as f:
        f.write(base)
    log(f"RESTORE triggered: {reason}; host={sha256_path(HOST_CONF)}")
    t = nginx_test()
    log(f"RESTORE nginx -t rc={t['rc']} {t['stderr']}")
    if t["rc"] == 0:
        r = nginx_reload()
        log(f"RESTORE nginx reload rc={r['rc']} {r['stderr']}")


def execute(hold: int) -> dict:
    start = now()
    if not (datetime(2026, 11, 7, 18, 58, tzinfo=TZ) <= start <= datetime(2026, 11, 7, 19, 2, tzinfo=TZ)):
        raise SystemExit(f"outside approved apply time: {start.isoformat()}")
    if not approval_ok():
        raise SystemExit("approval record missing or not APPROVED")
    if not BASELINE.exists() or not BACKUP.exists():
        raise SystemExit("preflight baseline/backup missing")
    base = BACKUP.read_bytes()
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    if sha256_bytes(base) != expected["host_sha256_before"]:
        raise SystemExit("baseline backup changed; abort")
    if sha256_path(HOST_CONF) != expected["host_sha256_before"]:
        raise SystemExit("host nginx.conf changed after preflight; abort")
    if not TEMP_CONF.exists():
        raise SystemExit("temp drill config missing")

    result = {"schema": "d1_p0_03_drill_result/v1", "started_at": stamp(), "applied": False, "restored": False, "aborted": False}
    try:
        DRILL_DIR.mkdir(parents=True, exist_ok=True)
        log("APPLY begin")
        with HOST_CONF.open("wb") as f:
            f.write(TEMP_CONF.read_bytes())
        result["applied"] = True
        result["host_sha256_during"] = sha256_path(HOST_CONF)
        result["container_sha256_during"] = container_hash()
        t = nginx_test()
        result["nginx_test_during"] = t
        log(f"APPLY nginx -t rc={t['rc']} {t['stderr']}")
        if t["rc"] != 0:
            raise RuntimeError("nginx -t failed after apply")
        r = nginx_reload()
        result["nginx_reload_during"] = r
        log(f"APPLY nginx reload rc={r['rc']} {r['stderr']}")
        time.sleep(2)
        m = matrix()
        result["route_matrix_during"] = m
        # Core assertions: UI 503, APIs 200, direct static 404.
        assert m["d1"]["status"] == 503, m["d1"]
        assert m["d1_slash"]["status"] == 503, m["d1_slash"]
        assert m["d1_research"]["status"] == 503, m["d1_research"]
        assert m["d1_signoff"]["status"] == 503, m["d1_signoff"]
        assert m["d1_query"]["status"] == 503, m["d1_query"]
        assert m["api_health"]["status"] == 200, m["api_health"]
        assert m["api_d1_decisions"]["status"] == 200, m["api_d1_decisions"]
        assert m["d1_q2_direct"]["status"] == 404, m["d1_q2_direct"]
        log("APPLY verified: /d1* 503; /api/d1 200; /api/health 200; direct 404")
        time.sleep(max(0, hold))
        result["held_seconds"] = max(0, hold)
    except Exception as e:  # noqa: BLE001
        result["aborted"] = True
        result["error"] = str(e)
        log(f"ABORT: {e}")
    finally:
        ensure_restore("window restore")
        time.sleep(1)
        result["restored_at"] = stamp()
        result["host_sha256_after"] = sha256_path(HOST_CONF)
        result["container_sha256_after"] = container_hash()
        t = nginx_test()
        result["nginx_test_after"] = t
        log(f"RESTORE nginx -t rc={t['rc']} {t['stderr']}")
        if t["rc"] == 0:
            r = nginx_reload()
            result["nginx_reload_after"] = r
            log(f"RESTORE nginx reload rc={r['rc']} {r['stderr']}")
        time.sleep(2)
        m = matrix()
        result["route_matrix_after"] = m
        result["data_hashes_after"] = data_hashes()
        result["finished_at"] = stamp()
        result["elapsed_seconds"] = round((now() - start).total_seconds(), 1)
        result["restored"] = (
            result.get("host_sha256_after") == expected["host_sha256_before"]
            and m["d1"]["status"] in (200, 302)
            and m["api_health"]["status"] == 200
            and m["api_d1_decisions"]["status"] == 200
            and result.get("data_hashes_after") == expected.get("data_hashes_before")
        )
        result["ok"] = bool(result["applied"] and result["restored"] and not result["aborted"])
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log(f"FINISH ok={result['ok']} restored={result['restored']} elapsed={result['elapsed_seconds']}s")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["preflight", "execute"], required=True)
    ap.add_argument("--hold", type=int, default=60)
    args = ap.parse_args()
    DRILL_DIR.mkdir(parents=True, exist_ok=True)
    if not TIMELINE.exists():
        TIMELINE.write_text("", encoding="utf-8")
    if args.phase == "preflight":
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
        return 0
    result = execute(args.hold)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
