# -*- coding: utf-8 -*-
"""P0-A /api/jev 13-route 503 drill.

Read-only preflight plus a time-boxed ingress drill:
- baseline every JEV route (HEAD probe, no business POST)
- insert a server-level 503 for ^/api/jev(?:[/?]|$)
- verify all 13 routes are 503, health/8091-direct/JEV-stack unaffected
- hold, then restore the exact nginx bytes
- verify every route returns to baseline and data hashes are unchanged
"""
from __future__ import annotations

import hashlib
import http.client
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(r"C:\项目\老田的AI助手")
HOST_CONF = ROOT / "portal" / "nginx.conf"
DIR = ROOT / "deliverables" / "d1_q2" / "20261107" / "P0-A_jev_drill_20261107_2359"
BACKUP = DIR / "nginx.conf.before"
TEMP_CONF = Path(r"C:\Temp\p0_jev_nginx_drill.conf")
APPROVAL = DIR / "approval.md"
BASELINE = DIR / "baseline_before.json"
RESULT = DIR / "drill_result.json"
TIMELINE = DIR / "timeline.log"
TZ = timezone(timedelta(hours=8))
START = datetime(2026, 11, 7, 23, 58, 0, tzinfo=TZ)
END = datetime(2026, 11, 8, 0, 8, 0, tzinfo=TZ)
BASE_8734 = "http://127.0.0.1:8734"
BASE_8091 = "http://127.0.0.1:8091"

ROUTES = [
    ("jev_root", "/api/jev/", "GET"),
    ("jev_capabilities", "/api/jev/capabilities", "GET"),
    ("jev_evidence", "/api/jev/evidence", "GET"),
    ("jev_evidence_item", "/api/jev/evidence/item", "GET"),
    ("jev_interpret", "/api/jev/interpret", "GET"),
    ("jev_intraday", "/api/jev/intraday/", "GET"),
    ("jev_platform", "/api/jev/platform", "GET"),
    ("jev_review_get", "/api/jev/review/", "GET"),
    ("jev_story", "/api/jev/story/", "GET"),
    ("jev_target", "/api/jev/target/", "GET"),
    ("jev_trace", "/api/jev/trace", "GET"),
    ("jev_wordcloud", "/api/jev/wordcloud", "GET"),
    ("jev_review_post", "/api/jev/review/", "POST"),
]


def now() -> datetime:
    return datetime.now(TZ)


def stamp() -> str:
    return now().isoformat(timespec="seconds")


def log(line: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with TIMELINE.open("a", encoding="utf-8") as f:
        f.write(f"{stamp()} {line}\n")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def http_status(url: str, method: str = "HEAD", timeout: float = 8.0) -> dict:
    u = urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        path = u.path or "/"
        if u.query:
            path += "?" + u.query
        conn.request(method, path)
        r = conn.getresponse()
        r.read(200)
        return {"url": url, "method": method, "status": r.status, "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "method": method, "status": None, "error": str(exc)}
    finally:
        conn.close()


def matrix() -> dict[str, dict]:
    return {name: http_status(BASE_8734 + path) for name, path, _ in ROUTES}


def other_checks() -> dict:
    return {
        "health_8734": http_status(BASE_8734 + "/api/health", "GET"),
        "d1_decisions_8734": http_status(BASE_8734 + "/api/d1/decisions", "GET"),
        "jev_direct_8091": http_status(BASE_8091 + "/api/jev/capabilities", "GET"),
        "jev_stack_not_targeted": http_status(BASE_8734 + "/api/jev-stack", "HEAD"),
    }


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
    return {name: (sha_path(path) if path.exists() else "") for name, path in paths.items()}


def approval_ok() -> bool:
    if not APPROVAL.exists():
        return False
    text = APPROVAL.read_text(encoding="utf-8")
    return "状态：APPROVED" in text and "2026-11-07T23:58:00+08:00" in text


def build_temp() -> dict:
    raw = HOST_CONF.read_bytes()
    marker = b"    location = /d1 {"
    if raw.count(marker) != 1:
        raise SystemExit(f"insertion marker count={raw.count(marker)}; abort")
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    rules = (
        b"    # P0-A-JEV-DRILL-TEMP: remove /api/jev exposure; auto-restore" + eol
        + b'    if ($request_uri ~ "^/api/jev(?:[/?]|$)") { return 503; }' + eol
        + eol
    )
    TEMP_CONF.write_bytes(raw.replace(marker, rules + marker, 1))
    return {
        "temp_conf": str(TEMP_CONF),
        "temp_sha256": sha_path(TEMP_CONF),
        "host_sha256": sha_path(HOST_CONF),
    }


def rows(before: dict, during: dict, after: dict) -> list[dict]:
    out = []
    for name, path, declared in ROUTES:
        b = before[name]["status"]
        d = during[name]["status"]
        a = after[name]["status"]
        out.append({
            "name": name,
            "path": path,
            "declared_method": declared,
            "probe_method": "HEAD",
            "before": b,
            "during": d,
            "after": a,
            "during_503": d == 503,
            "restored_to_baseline": a == b,
        })
    return out


def preflight() -> dict:
    if not approval_ok():
        raise SystemExit("approval missing or not APPROVED")
    raw = HOST_CONF.read_bytes()
    BACKUP.write_bytes(raw)
    built = build_temp()
    result = {
        "schema": "p0_a_jev_drill_preflight/v1",
        "checked_at": stamp(),
        "approval": str(APPROVAL),
        "git_head": git_head(),
        "host_conf": str(HOST_CONF),
        "host_sha256_before": sha_path(HOST_CONF),
        "backup": str(BACKUP),
        "backup_sha256": sha_path(BACKUP),
        "container_sha256_before": container_hash(),
        "temp_conf": built["temp_conf"],
        "temp_sha256": built["temp_sha256"],
        "nginx_test_before": nginx_test(),
        "route_matrix_before": matrix(),
        "other_before": other_checks(),
        "data_hashes_before": data_hashes(),
    }
    BASELINE.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"PREFLIGHT ok host={result['host_sha256_before']} temp={result['temp_sha256']}")
    return result


def restore() -> None:
    if BACKUP.exists():
        HOST_CONF.write_bytes(BACKUP.read_bytes())
        log(f"RESTORE bytes host={sha_path(HOST_CONF)}")


def execute(hold: int = 60) -> dict:
    started = now()
    if not (START <= started <= END):
        raise SystemExit(f"outside approved window: {started.isoformat()}")
    if not BASELINE.exists() or not BACKUP.exists():
        raise SystemExit("baseline/backup missing")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if sha_path(HOST_CONF) != baseline["host_sha256_before"]:
        raise SystemExit("nginx.conf changed after preflight; abort")

    result = {
        "schema": "p0_a_jev_drill_result/v1",
        "started_at": stamp(),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "applied": False,
        "restored": False,
        "aborted": False,
    }
    try:
        log("APPLY begin")
        HOST_CONF.write_bytes(TEMP_CONF.read_bytes())
        result["applied"] = True
        result["host_sha256_during"] = sha_path(HOST_CONF)
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
        during = matrix()
        others = other_checks()
        result["route_matrix_during"] = during
        result["other_during"] = others
        bad = {name: v["status"] for name, v in during.items() if v["status"] != 503}
        result["not_503"] = bad
        if bad:
            raise RuntimeError(f"routes not 503: {bad}")
        if others["health_8734"]["status"] != 200:
            raise RuntimeError(f"health not 200: {others['health_8734']}")
        if others["d1_decisions_8734"]["status"] != 200:
            raise RuntimeError(f"d1 decisions not 200: {others['d1_decisions_8734']}")
        if others["jev_direct_8091"]["status"] != 200:
            raise RuntimeError(f"direct 8091 jev not 200: {others['jev_direct_8091']}")
        if others["jev_stack_not_targeted"]["status"] == 503:
            raise RuntimeError("jev-stack was unexpectedly targeted")
        log("DURING verified 13x503; health/d1/direct ok; holding")
        time.sleep(hold)
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        result["aborted"] = True
        log(f"ABORT {exc}")
        restore()
        raise
    finally:
        restore()
        result["host_sha256_after"] = sha_path(HOST_CONF)
        t = nginx_test()
        result["nginx_test_after"] = t
        if t["rc"] == 0:
            result["nginx_reload_after"] = nginx_reload()
        else:
            result["nginx_reload_after"] = {"rc": None, "skipped": True}
        time.sleep(1)
        after = matrix()
        others_after = other_checks()
        result["route_matrix_after"] = after
        result["other_after"] = others_after
        result["data_hashes_after"] = data_hashes()
        result["restored"] = (
            not result["aborted"]
            and result["host_sha256_after"] == baseline["host_sha256_before"]
            and all(v["status"] != 503 for v in after.values())
            and result["data_hashes_after"] == baseline["data_hashes_before"]
        )
        result["ended_at"] = stamp()
        result["duration_seconds"] = round((now() - started).total_seconds(), 1)
        log(f"END restored={result['restored']} duration={result['duration_seconds']}s")
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def write_receipt(result: dict) -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    rs = rows(baseline["route_matrix_before"], result["route_matrix_during"], result["route_matrix_after"])
    lines = [
        "# P0-A /api/jev 13 条入口摘除回执",
        "",
        f"- 窗口：{result['window']['start']} — {result['window']['end']}",
        f"- 开始/结束：{result['started_at']} / {result['ended_at']}",
        f"- 耗时：{result['duration_seconds']}s；applied={result['applied']}；restored={result['restored']}；aborted={result['aborted']}",
        f"- nginx before/after：`{baseline['host_sha256_before']}` / `{result.get('host_sha256_after', '')}`",
        f"- 数据哈希不变：{result['data_hashes_after'] == baseline['data_hashes_before']}",
        "- 探针：HEAD；未向业务发送 POST；POST 路由仅验证 ingress 返回 503。",
        "- 边界：这是 8734 ingress 入口摘除演练，不是进程级故障演练。",
        "",
        "| # | 路由 | 声明方法 | before | during | after | 503 | 恢复基线 |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for i, r in enumerate(rs, 1):
        lines.append(
            f"| {i} | `{r['path']}` | {r['declared_method']} | {r['before']} | {r['during']} | {r['after']} | "
            f"{'PASS' if r['during_503'] else 'FAIL'} | {'PASS' if r['restored_to_baseline'] else 'FAIL'} |"
        )
    lines += [
        "",
        "## 其他检查",
        "",
        f"- `/api/health` before/during/after：{baseline['other_before']['health_8734']['status']} / {result['other_during']['health_8734']['status']} / {result['other_after']['health_8734']['status']}",
        f"- `/api/d1/decisions`：{baseline['other_before']['d1_decisions_8734']['status']} / {result['other_during']['d1_decisions_8734']['status']} / {result['other_after']['d1_decisions_8734']['status']}",
        f"- 8091 直连 `/api/jev/capabilities`：{baseline['other_before']['jev_direct_8091']['status']} / {result['other_during']['jev_direct_8091']['status']} / {result['other_after']['jev_direct_8091']['status']}",
        f"- `/api/jev-stack`（不应被规则命中）：{baseline['other_before']['jev_stack_not_targeted']['status']} / {result['other_during']['jev_stack_not_targeted']['status']} / {result['other_after']['jev_stack_not_targeted']['status']}",
        "",
        "## 证据",
        "",
        "- `baseline_before.json`",
        "- `drill_result.json`",
        "- `timeline.log`",
        "- `nginx.conf.before`",
    ]
    (DIR / "P0-A_jev_回执.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["preflight", "execute"], required=True)
    ap.add_argument("--hold", type=int, default=60)
    args = ap.parse_args()
    if args.phase == "preflight":
        pre = preflight()
        print(json.dumps({k: pre[k] for k in ("checked_at", "host_sha256_before", "temp_sha256", "confirm") if k in pre}, ensure_ascii=False))
    else:
        res = execute(hold=args.hold)
        write_receipt(res)
        print(json.dumps({
            "started_at": res["started_at"],
            "ended_at": res["ended_at"],
            "duration_seconds": res["duration_seconds"],
            "applied": res["applied"],
            "restored": res["restored"],
            "aborted": res["aborted"],
            "not_503": res.get("not_503"),
        }, ensure_ascii=False))
