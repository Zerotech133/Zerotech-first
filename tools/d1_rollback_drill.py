#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D1 P0-03 rollback drill preflight / runbook (read-only by default).

This tool deliberately does NOT remove a production entry point.  P0-03 requires a
real drill in an approved window; that action is a Lead operation because it makes
``/d1`` and the D1 API unavailable for a short period.  The default mode records a
baseline, checks the rollback prerequisites, and emits an executable runbook for the
approved window.

Usage::

    python -X utf8 tools/d1_rollback_drill.py
    python -X utf8 tools/d1_rollback_drill.py --json --out deliverables/.../P0-03_rollback_preflight.json
    python -X utf8 tools/d1_rollback_drill.py --execute   # blocked: requires approval

Exit codes: 0 preflight pass, 2 preflight fail, 3 execute blocked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover - repository always ships PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "compositions" / "lusu-d1-flow" / "manifest.yaml"
NGINX = ROOT / "portal" / "nginx.conf"
PAGE = ROOT / "portal" / "d1" / "index.html"
PAPER_STATE = ROOT / "agent_platform" / "data" / "paper" / "state.json"
DECISIONS = ROOT / "agent_platform" / "data" / "d1_decisions.jsonl"
HANDOFFS = ROOT / "agent_platform" / "data" / "d1_handoffs.jsonl"
SCHEMA = "d1_p0_03_rollback_preflight/v1"
DEFAULT_ENDPOINTS = (
    "/api/health",
    "/health",
    "/api/d1/research",
    "/api/d1/decisions",
    "/api/d1/handoffs",
    "/api/d1/meeting",
    "/api/d1/watch-plans",
    "/api/d1/training/episodes",
    "/api/war-room/snapshot",
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                count += 1
    return count


def _check(name: str, ok: bool, detail: str = "", critical: bool = True) -> dict:
    return {
        "name": name,
        "ok": bool(ok),
        "critical": bool(critical),
        "detail": str(detail)[:500],
    }


def _load_manifest(path: Path = MANIFEST) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML unavailable")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _git(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _http_status(url: str, timeout: float) -> tuple[int | None, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "d1-p0-03-preflight/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), "HTTPError"
    except Exception as exc:  # noqa: BLE001 - evidence records the exact failure
        return None, f"{type(exc).__name__}: {exc}"[:300]


def _parse_targets(text: str) -> list[str]:
    """Extract commit ids from the manifest's static_prechecks sentence."""
    seen: list[str] = []
    for raw in re.findall(r"\b[0-9a-f]{7,40}\b", str(text or "")):
        if raw not in seen:
            seen.append(raw)
    return seen


def _paper_baseline(path: Path = PAPER_STATE) -> dict:
    out = {"path": str(path), "exists": path.exists(), "sha256": "", "positions": None}
    if not path.exists():
        return out
    out["sha256"] = _sha256(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        pos = doc.get("positions")
        out["positions"] = len(pos) if isinstance(pos, dict) else None
        out["nav"] = doc.get("nav")
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return out


def _endpoint_url(endpoint: str) -> str:
    endpoint = str(endpoint or "").strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return endpoint
    return "http://127.0.0.1:8091" + (endpoint if endpoint.startswith("/") else "/" + endpoint)


def preflight(
    manifest_path: Path = MANIFEST,
    nginx_path: Path = NGINX,
    page_path: Path = PAGE,
    paper_state: Path = PAPER_STATE,
    decisions_path: Path = DECISIONS,
    handoffs_path: Path = HANDOFFS,
    http_timeout: float = 4.0,
    probe_http: bool = True,
    check_container: bool = True,
) -> dict:
    checks: list[dict] = []
    manifest = _load_manifest(manifest_path)
    status = manifest.get("status") or {}
    rollback = status.get("rollback") or {}
    probe = status.get("health_probe") or {}

    steps = rollback.get("steps") or []
    checks.append(_check("manifest.rollback.steps", len(steps) >= 4, f"steps={len(steps)}"))
    last_drilled = str(rollback.get("last_drilled") or "")
    checks.append(
        _check(
            "manifest.rollback.pending",
            last_drilled.startswith("pending"),
            f"last_drilled={last_drilled!r}",
        )
    )

    nginx_text = nginx_path.read_text(encoding="utf-8", errors="replace") if nginx_path.exists() else ""
    exact_route = bool(re.search(r"location\s*=\s*/d1\s*\{", nginx_text))
    prefix_route = bool(re.search(r"location\s+\^~\s*/d1/\s*\{", nginx_text))
    auth_guard = bool(re.search(r"location\s*=\s*/d1\s*\{[\s\S]{0,500}?auth_request\s+/_auth_guard", nginx_text))
    checks.append(_check("nginx.d1.exact_route", exact_route, "location = /d1"))
    checks.append(_check("nginx.d1.prefix_route", prefix_route, "location ^~ /d1/"))
    checks.append(_check("nginx.d1.auth_guard", auth_guard, "auth_request /_auth_guard"))
    checks.append(_check("page.d1.index", page_path.is_file(), f"{page_path} size={page_path.stat().st_size if page_path.exists() else 0}"))

    head = _git(["rev-parse", "HEAD"])
    head_ok = head.returncode == 0 and bool(head.stdout.strip())
    checks.append(_check("git.head", head_ok, (head.stdout or head.stderr).strip()[:200]))
    targets = _parse_targets(str(rollback.get("static_prechecks") or ""))
    target_missing = []
    for rev in targets:
        r = _git(["cat-file", "-e", f"{rev}^{{commit}}"])
        if r.returncode != 0:
            target_missing.append(rev)
    checks.append(
        _check(
            "git.rollback_targets",
            not target_missing,
            f"declared={targets} missing={target_missing}",
            critical=bool(targets),
        )
    )

    if check_container:
        try:
            r = subprocess.run(
                ["docker", "exec", "ai-portal", "nginx", "-t"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=25,
            )
            checks.append(
                _check(
                    "container.nginx_test",
                    r.returncode == 0,
                    ((r.stderr or "") + (r.stdout or "")).strip()[:400],
                    critical=False,
                )
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("container.nginx_test", False, f"{type(exc).__name__}: {exc}"[:300], critical=False))

    endpoints = probe.get("endpoints") or list(DEFAULT_ENDPOINTS)
    http_results = []
    for endpoint in endpoints:
        url = _endpoint_url(endpoint)
        code, err = _http_status(url, http_timeout) if probe_http else (None, "skipped")
        ok = code == 200 if probe_http else True
        http_results.append({"endpoint": endpoint, "url": url, "status": code, "ok": ok, "error": err})
        checks.append(_check("http" + (endpoint if str(endpoint).startswith("/") else "/" + str(endpoint)), ok, f"status={code} {err}".strip()))
    if not endpoints:
        checks.append(_check("http.endpoints", False, "manifest health_probe.endpoints empty"))

    paper = _paper_baseline(paper_state)
    checks.append(_check("paper.state.readable", paper.get("exists") and "error" not in paper, json.dumps(paper, ensure_ascii=False)[:400]))
    checks.append(
        _check(
            "ledger.d1_decisions",
            decisions_path.exists(),
            f"rows={_count_jsonl(decisions_path)} sha256={_sha256(decisions_path) if decisions_path.exists() else ''}",
        )
    )
    checks.append(
        _check(
            "ledger.d1_handoffs",
            handoffs_path.exists(),
            f"rows={_count_jsonl(handoffs_path)} sha256={_sha256(handoffs_path) if handoffs_path.exists() else ''}",
        )
    )

    critical_failures = [c["name"] for c in checks if c["critical"] and not c["ok"]]
    ok = not critical_failures
    runbook = [
        "0. 仅 Lead 在批准窗口执行；先跑本命令记录 baseline（含 HTTP/文件哈希）。",
        "1. 备份 portal/nginx.conf；记录 `git rev-parse HEAD` 与 D1 台账/paper state 哈希。",
        "2. 摘除入口：在 nginx 层临时将 /d1 精确路由及 ^~ /d1/ 前缀置为 503/404；",
        "   `docker cp` 到 ai-portal，`docker exec ai-portal nginx -t`，`nginx -s reload`；",
        "   同步暂停 /api/d1/* 对 8734 的暴露；不停止 8091。",
        "3. 验证：curl /d1 与 /api/d1/* 返回 503/404，/api/health 与其他平台入口仍 200。",
        "4. 恢复：还原 nginx.conf，`nginx -t`、`nginx -s reload`；确认 /d1 与 9 接口恢复 200。",
        "5. 复核：D1 台账行数、paper state 哈希、四 ID 链与 baseline 完全一致；总恢复 ≤10 分钟。",
        "6. 回填 manifest.status.rollback.last_drilled、起止时间、证据路径；不把本 preflight 当演练。",
    ]
    return {
        "schema": SCHEMA,
        "checked_at": _now(),
        "mode": "preflight",
        "real_drill_performed": False,
        "reason": "默认只读；真实摘除入口需 Lead 批准窗口，--execute 不执行生产变更",
        "ok": ok,
        "critical_failures": critical_failures,
        "checks": checks,
        "baseline": {
            "git_head": (head.stdout or "").strip(),
            "nginx_sha256": _sha256(nginx_path) if nginx_path.exists() else "",
            "page_sha256": _sha256(page_path) if page_path.exists() else "",
            "decisions": {"path": str(decisions_path), "rows": _count_jsonl(decisions_path)},
            "handoffs": {"path": str(handoffs_path), "rows": _count_jsonl(handoffs_path)},
            "paper": paper,
            "http": http_results,
        },
        "runbook": runbook,
    }


def render_markdown(result: dict) -> str:
    lines = [
        "# D1 P0-03 回滚演练预检 / runbook",
        "",
        f"- 检查时间：{result.get('checked_at')}",
        f"- 模式：{result.get('mode')}；real_drill_performed={result.get('real_drill_performed')}",
        f"- 结论：{'PASS' if result.get('ok') else 'FAIL'}",
        "",
        "## 检查",
        "",
        "| 检查 | 结果 | 详情 |",
        "| --- | --- | --- |",
    ]
    for c in result.get("checks") or []:
        detail = str(c.get("detail", "")).replace("|", "/").replace("\r", " ").replace("\n", "；")
        lines.append(f"| {c['name']} | {'PASS' if c['ok'] else 'FAIL'} | {detail} |")
    lines += ["", "## 回滚 runbook（批准窗口执行）", ""]
    lines += [f"{i + 1}. {s}" for i, s in enumerate(result.get("runbook") or [])]
    lines += ["", "> 本文件是 preflight，不是真实演练记录；last_drilled 仍 pending。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 P0-03 rollback drill preflight")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--out", default="", help="write JSON or markdown to path")
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--no-http", action="store_true", help="skip live HTTP checks")
    parser.add_argument("--no-container", action="store_true", help="skip docker nginx -t")
    parser.add_argument("--execute", action="store_true", help="blocked: production entry removal requires approval")
    args = parser.parse_args(argv)

    if args.execute:
        blocked = {
            "schema": SCHEMA,
            "ok": False,
            "blocked": True,
            "reason": "P0-03 真实摘除入口需 Lead 批准窗口与显式授权；本工具不执行生产变更",
            "checked_at": _now(),
        }
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
        return 3

    result = preflight(http_timeout=args.timeout, probe_http=not args.no_http, check_container=not args.no_container)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".json":
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            out.write_text(render_markdown(result), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
