# -*- coding: utf-8 -*-
"""门户族服务保活（2026-11-16 WO-PORTAL-KEEPALIVE）。

背景：8091 portal-api / 8098 model-admin / 8789 watch-admin 是 D1 接口面的宿主；
2026-11-15 起多次出现「夜里进程没了、端口不监听」，接口面整段不可用
（Q2 SLO 读接口 availability 违约的根因之一就是这段 downtime）。

本脚本只做一件事：**端口不监听就拉起，监听就只看一眼**。
- 幂等：端口在监听 → 直接 OK 退出，不起第二个实例；
- 只启动本项目内的服务脚本，不杀进程、不改系统配置；
- 供计划任务每分钟 `--once` 调用；也可手动 / --status 查看。

用法::

    python -X utf8 tools/portal_keepalive.py --once            # 保活一轮
    python -X utf8 tools/portal_keepalive.py --once --dry-run  # 只看不做
    python -X utf8 tools/portal_keepalive.py --status --json   # 只看状态
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTAL = REPO / "portal"
LOG = REPO / "logs" / "portal_keepalive.log"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
START_WAIT_SEC = 12.0
PROBE_TIMEOUT_SEC = 3.0

#: 端口 / 启动脚本 / 工作目录 / 轻探活 URL（None = 只用端口判活）
SERVICES = [
    {"name": "portal-api", "port": 8091, "script": "portal_api.py",
     "workdir": PORTAL, "health": "http://127.0.0.1:8091/health", "critical": True},
    {"name": "model-admin", "port": 8098, "script": "api_model_admin.py",
     "workdir": PORTAL, "health": "http://127.0.0.1:8098/api/agents", "critical": False},
    {"name": "watch-admin", "port": 8789, "script": "watch_admin_server.py",
     "workdir": PORTAL, "health": "http://127.0.0.1:8789/watch-admin", "critical": True},
]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    line = "%s %s" % (_now(), msg)
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def listening(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def owner_pid(port: int) -> int | None:
    """尽力取监听进程 pid（Windows netstat 版；失败返回 None）。

    netstat 在本机输出为 OEM/GBK 码页，用 bytes 读取后宽松解码，
    避免 text=True 默认 utf-8 解码失败（2026-11-16 实测 0xbb 报错）。
    """
    try:
        raw = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             timeout=10).stdout or b""
    except Exception:  # noqa: BLE001
        return None
    text = raw.decode("gbk", errors="replace") if isinstance(raw, bytes) else str(raw)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(":" + str(port)) and parts[3] == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def http_probe(url: str | None, timeout: float = PROBE_TIMEOUT_SEC) -> tuple[int | None, str]:
    if not url:
        return None, ""
    try:
        req = urllib.request.Request(url, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return int(resp.status), ""
    except urllib.error.HTTPError as exc:
        return int(getattr(exc, "code", 0)), ""
    except Exception as exc:  # noqa: BLE001
        return None, type(exc).__name__


def start_service(svc: dict, python: str, dry_run: bool) -> dict:
    script = svc["workdir"] / svc["script"]
    if not script.is_file():
        return {"started": False, "error": "script_missing:%s" % script}
    if dry_run:
        return {"started": False, "dry_run": True, "would_start": str(script)}
    out_log = REPO / "logs" / ("%s.out.log" % svc["name"])
    out_log.parent.mkdir(parents=True, exist_ok=True)
    with out_log.open("ab") as out:
        proc = subprocess.Popen(
            [python, "-X", "utf8", svc["script"]],
            cwd=str(svc["workdir"]),
            stdout=out, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
        )
    return {"started": True, "pid": proc.pid}


def wait_listen(port: int, seconds: float = START_WAIT_SEC) -> float | None:
    t0 = time.time()
    while time.time() - t0 < seconds:
        if listening(port):
            return round(time.time() - t0, 2)
        time.sleep(0.4)
    return None


def check_one(svc: dict, python: str, dry_run: bool) -> dict:
    name, port = svc["name"], svc["port"]
    row = {"name": name, "port": port, "action": "none", "ok": False}
    if listening(port):
        pid = owner_pid(port)
        status, err = http_probe(svc.get("health"))
        row.update({"action": "already_alive", "ok": status == 200 or status is None,
                    "pid": pid, "http": status, "probe_error": err})
        log("[%s:%d] OK alive pid=%s http=%s" % (name, port, pid, status))
        return row
    log("[%s:%d] DOWN -> starting %s" % (name, port, svc["script"]))
    started = start_service(svc, python, dry_run)
    if dry_run:
        # dry-run 也如实报缺脚本：不假装「会成功」
        if started.get("error"):
            row.update({"action": "start_failed", "error": started["error"]})
            log("[%s:%d] DRY-RUN script missing: %s" % (name, port, started["error"]))
            return row
        row.update({"action": "dry_run_would_start", "ok": True,
                    "would_start": started.get("would_start")})
        log("[%s:%d] DRY-RUN would start" % (name, port))
        return row
    if started.get("error"):
        row.update({"action": "start_failed", "error": started["error"]})
        log("[%s:%d] START FAIL %s" % (name, port, started["error"]))
        return row
    took = wait_listen(port)
    status, err = http_probe(svc.get("health")) if took is not None else (None, "")
    row.update({"action": "started", "pid": started.get("pid"), "wait_s": took,
                "http": status, "probe_error": err, "ok": took is not None})
    if took is None:
        log("[%s:%d] STARTED pid=%s but port still closed after %.0fs"
            % (name, port, started.get("pid"), START_WAIT_SEC))
    else:
        log("[%s:%d] STARTED pid=%s listening after %ss http=%s"
            % (name, port, started.get("pid"), took, status))
    return row


def run_once(python: str, dry_run: bool, as_json: bool) -> int:
    rows = [check_one(svc, python, dry_run) for svc in SERVICES]
    critical_bad = [r for r in rows if not r.get("ok") and r["port"] in
                    {s["port"] for s in SERVICES if s.get("critical")}]
    if as_json:
        print(json.dumps({"schema": "portal_keepalive/v1", "at": _now(),
                          "dry_run": dry_run, "services": rows,
                          "ok": not critical_bad}, ensure_ascii=False, indent=1))
    log("SUMMARY ok=%s down=%s" % (not critical_bad,
                                   [r["name"] for r in rows if not r.get("ok")]))
    return 0 if not critical_bad else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="门户族服务保活（端口不监听则拉起）")
    ap.add_argument("--once", action="store_true", help="保活一轮（计划任务用）")
    ap.add_argument("--status", action="store_true", help="只报状态，不起服务")
    ap.add_argument("--dry-run", action="store_true", help="只打印将做什么")
    ap.add_argument("--json", action="store_true", help="结构化输出")
    ap.add_argument("--python", default=sys.executable, help="拉起服务用的解释器")
    args = ap.parse_args(argv)
    if args.status:
        rows = []
        for svc in SERVICES:
            alive = listening(svc["port"])
            status, err = http_probe(svc.get("health")) if alive else (None, "down")
            rows.append({"name": svc["name"], "port": svc["port"], "listening": alive,
                         "pid": owner_pid(svc["port"]) if alive else None,
                         "http": status, "probe_error": err})
        if args.json:
            print(json.dumps({"schema": "portal_keepalive_status/v1", "at": _now(),
                              "services": rows}, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                print("%-14s :%-5d %-9s pid=%-7s http=%s" % (
                    r["name"], r["port"], "LISTEN" if r["listening"] else "DOWN",
                    r["pid"], r["http"]))
        return 0
    if args.once:
        return run_once(args.python, args.dry_run, args.json)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
