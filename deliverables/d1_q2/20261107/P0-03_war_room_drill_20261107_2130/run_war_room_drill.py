# -*- coding: utf-8 -*-
"""P0-03 supplement: remove only /api/war-room/snapshot on 8734.

- preflight: verify approval, back up exact bytes, build temp config, record baseline.
- execute: only 21:28-21:32; apply, verify target 503 while everything else stays up,
  hold 60s, restore exact bytes, verify 200 and data hashes unchanged.
- Never restarts 8091/8789/Watch; only docker exec nginx -t/-s reload.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(r"C:\项目\老田的AI助手")
HOST_CONF = ROOT / "portal" / "nginx.conf"
DRILL_DIR = ROOT / "deliverables" / "d1_q2" / "20261107" / "P0-03_war_room_drill_20261107_2130"
BACKUP = DRILL_DIR / "nginx.conf.before"
TEMP_CONF = Path(r"C:\Temp\p0_03_war_room_nginx_drill.conf")
APPROVAL = DRILL_DIR / "approval.md"
BASELINE = DRILL_DIR / "baseline_before.json"
RESULT = DRILL_DIR / "drill_result.json"
TIMELINE = DRILL_DIR / "timeline.log"
TZ = timezone(timedelta(hours=8))

SERVICES = {
    "war_room": ("http://127.0.0.1:8734/api/war-room/snapshot", "GET"),
    "war_room_query": ("http://127.0.0.1:8734/api/war-room/snapshot?probe=1", "GET"),
    "war_room_direct_8091": ("http://127.0.0.1:8091/api/war-room/snapshot", "GET"),
    "api_d1_decisions": ("http://127.0.0.1:8734/api/d1/decisions", "GET"),
    "api_health": ("http://127.0.0.1:8734/api/health", "GET"),
    "d1": ("http://127.0.0.1:8734/d1", "GET"),
}
TARGET_KEYS = ["war_room", "war_room_query"]
UNCHANGED_KEYS = ["war_room_direct_8091", "api_d1_decisions", "api_health", "d1"]


def now() -> datetime:
    return datetime.now(TZ)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def log(line: str) -> None:
    DRILL_DIR.mkdir(parents=True, exist_ok=True)
    with TIMELINE.open("a", encoding="utf-8") as f:
        f.write(f"{stamp()} {line}\n")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_path(p: Path) -> str:
    return sha256_bytes(p.read_bytes())


def status(url: str, method: str = "GET", timeout: float = 8.0) -> dict:
    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        conn.request(method, path)
        r = conn.getresponse()
        body = r.read(200)
        return {
            "url": url,
            "method": method,
            "status": r.status,
            "body_prefix": body.decode("utf-8", "replace")[:100],
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        return {"url": url, "method": method, "status": None, "body_prefix": "", "error": str(e)}
    finally:
        conn.close()


def matrix() -> dict:
    return {k: status(url, method) for k, (url, method) in SERVICES.items()}


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
    return (
        "状态：**APPROVED" in text
        and "P0-03-WARROOM-20261107-2130" in text
        and "21:30" in text
        and "21:40" in text
    )


def build_temp_config() -> dict:
    raw = HOST_CONF.read_bytes()
    marker = b"    location = /d1 {"
    if raw.count(marker) != 1:
        raise SystemExit(f"insertion marker count={raw.count(marker)}; abort")
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    rules = (
        b"    # P0-03-WARROOM-DRILL-2130-TEMP: only /api/war-room/snapshot removed; auto-restore" + eol
        + b'    if ($request_uri ~ "^/api/war-room/snapshot(?:[/?]|$)") { return 503; }' + eol
        + eol
    )
    TEMP_CONF.write_bytes(raw.replace(marker, rules + marker, 1))
    return {
        "temp_conf": str(TEMP_CONF),
        "temp_sha256": sha256_path(TEMP_CONF),
        "host_sha256": sha256_path(HOST_CONF),
        "inserted_lines": 3,
    }


def preflight() -> dict:
    if not approval_ok():
        raise SystemExit("approval record missing or not APPROVED")
    base = HOST_CONF.read_bytes()
    BACKUP.write_bytes(base)
    built = build_temp_config()
    result = {
        "schema": "d1_p0_03_war_room_preflight/v1",
        "checked_at": stamp(),
        "approval": str(APPROVAL),
        "approval_ok": True,
        "git_head": git_head(),
        "host_conf": str(HOST_CONF),
        "host_sha256_before": sha256_bytes(base),
        "backup": str(BACKUP),
        "backup_sha256": sha256_path(BACKUP),
        "container_sha256_before": container_hash(),
        "temp_conf": built["temp_conf"],
        "temp_conf_sha256": built["temp_sha256"],
        "temp_inserted_lines": built["inserted_lines"],
        "nginx_test_before": nginx_test(),
        "route_matrix_before": matrix(),
        "data_hashes_before": data_hashes(),
    }
    BASELINE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("PREFLIGHT ok; host=%s temp=%s" % (result["host_sha256_before"], result["temp_conf_sha256"]))
    return result


def restore_from_backup(reason: str) -> None:
    if not BACKUP.exists():
        return
    with HOST_CONF.open("wb") as f:
        f.write(BACKUP.read_bytes())
    log(f"RESTORE bytes written: {reason}; host={sha256_path(HOST_CONF)}")


def assert_during(m: dict) -> None:
    for k in TARGET_KEYS:
        assert m[k]["status"] == 503, (k, m[k])
    assert m["war_room_direct_8091"]["status"] == 200, m["war_room_direct_8091"]
    assert m["api_d1_decisions"]["status"] == 200, m["api_d1_decisions"]
    assert m["api_health"]["status"] == 200, m["api_health"]
    assert m["d1"]["status"] in (200, 302), m["d1"]


def assert_after(m: dict) -> None:
    for k in TARGET_KEYS:
        assert m[k]["status"] == 200, (k, m[k])
    assert m["war_room_direct_8091"]["status"] == 200, m["war_room_direct_8091"]
    assert m["api_d1_decisions"]["status"] == 200, m["api_d1_decisions"]
    assert m["api_health"]["status"] == 200, m["api_health"]
    assert m["d1"]["status"] in (200, 302), m["d1"]


def execute(hold: int) -> dict:
    start = now()
    if not (datetime(2026, 11, 7, 21, 28, tzinfo=TZ) <= start <= datetime(2026, 11, 7, 21, 32, tzinfo=TZ)):
        raise SystemExit(f"outside approved apply time: {start.isoformat()}")
    if not approval_ok():
        raise SystemExit("approval record missing or not APPROVED")
    if not BASELINE.exists() or not BACKUP.exists():
        raise SystemExit("preflight baseline/backup missing")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    base = BACKUP.read_bytes()
    if sha256_bytes(base) != baseline["host_sha256_before"]:
        raise SystemExit("baseline backup changed; abort")
    if sha256_path(HOST_CONF) != baseline["host_sha256_before"]:
        raise SystemExit("host nginx.conf changed after preflight; abort")
    built = build_temp_config()
    if built["temp_sha256"] != baseline["temp_conf_sha256"]:
        raise SystemExit("temp config hash changed; abort")

    result = {
        "schema": "d1_p0_03_war_room_result/v1",
        "started_at": stamp(),
        "applied": False,
        "restored": False,
        "aborted": False,
    }
    try:
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
        assert_during(m)
        log("APPLY verified: 8734 war-room 503; 8091 direct + d1/health unaffected")
        time.sleep(max(0, hold))
        result["held_seconds"] = max(0, hold)
    except Exception as e:  # noqa: BLE001
        result["aborted"] = True
        result["error"] = str(e)
        log(f"ABORT: {e}")
    finally:
        restore_from_backup("window restore")
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
        try:
            assert_after(m)
            routes_ok = True
        except AssertionError as e:
            result["after_route_error"] = str(e)
            routes_ok = False
        result["restored"] = (
            result.get("host_sha256_after") == baseline["host_sha256_before"]
            and routes_ok
            and result.get("data_hashes_after") == baseline.get("data_hashes_before")
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
