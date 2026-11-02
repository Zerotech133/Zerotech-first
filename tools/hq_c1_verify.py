#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C1 激活后自检器（P0，2026-11-03）：一条命令复跑 8091/API/公共鉴权/L1 口径。

只读 GET，不登录、不重启、不改服务，供 **每次 8091 重启后** 验收：
  1. 8091 `/health` 200 + `ok=true`；
  2. 8091 `/api/hq/{roles,agenda,directives,reports,commercial,snapshot}` 200 + `ok=true` + data；
  3. snapshot 必需键/计数：roles=5、directives 非空、reports.legions 非空、commercial.summary；
  4. 8734 公共入口（不跟随重定向）：`/hq`=302（→ /login?next=/hq）、`/api/hq/snapshot` 未登录=401；
  5. L1 口径：8091 监听 pid + 进程启动时间 > C1 代码 mtime（确认已换血，未换血=PENDING）。

输出：text（默认）/ `--json`（stdout 纯 JSON）/ `--markdown`。
状态：PASS｜PENDING（未激活/无法判定，不伪造通过）｜FAIL。
退出码：0=PASS；1=FAIL；2=PENDING（`--allow-pending` 时 PENDING 也返回 0）。

用法：
  python -X utf8 tools/hq_c1_verify.py
  python -X utf8 tools/hq_c1_verify.py --json
  python -X utf8 tools/hq_c1_verify.py --markdown --allow-pending
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTAL_8091 = "http://127.0.0.1:8091"
PUBLIC_8734 = "http://127.0.0.1:8734"

SCHEMA = "hq/c1_verify/v1"
HQ_APIS = ("roles", "agenda", "directives", "reports", "commercial", "snapshot")
SNAPSHOT_KEYS = ("schema", "roles", "agenda", "directives", "reports", "commercial",
                 "org", "tasks", "approvals", "budget", "heartbeat")
C1_CODE_FILES = ("portal/api_hq.py", "tools/hq_directives.py", "tools/hq_reports.py",
                 "portal/hq.html")
STATUS_PASS, STATUS_PENDING, STATUS_FAIL = "PASS", "PENDING", "FAIL"
LOGIN_MARKERS = ("登录", "login", "/login?next=")


# --------------------------------------------------------------------------- #
# HTTP（只读 GET；默认不跟随重定向，才能验收 302）
# --------------------------------------------------------------------------- #
class Resp:
    __slots__ = ("code", "body", "headers", "error", "url")

    def __init__(self, code=None, body="", headers=None, error="", url=""):
        self.code = code
        self.body = body or ""
        self.headers = headers or {}
        self.error = error or ""
        self.url = url

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, TypeError):
            return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """返回 None → 让 3xx 以 HTTPError 抛出，保留真实状态码。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http(url: str, timeout: float = 8.0) -> Resp:
    req = urllib.request.Request(url, headers={"User-Agent": "hq-c1-verify/1.0"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return Resp(r.status, r.read(400000).decode("utf-8", "replace"),
                        dict(r.getheaders()), "", url)
    except urllib.error.HTTPError as e:
        try:
            body = e.read(400000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return Resp(e.code, body, dict(e.headers or {}), "", url)
    except Exception as e:  # noqa: BLE001 —— 超时/连接拒绝/环境异常
        return Resp(None, "", {}, "%s: %s" % (type(e).__name__, e), url)


def _detail(resp: Resp, extra: str = "") -> str:
    if resp.error:
        return "网络/环境异常：" + resp.error
    loc = resp.headers.get("Location") or resp.headers.get("location") or ""
    bits = ["code=%s" % resp.code]
    if loc:
        bits.append("location=%s" % loc)
    if resp.body:
        bits.append("body=%s" % resp.body[:160].replace("\n", " "))
    if extra:
        bits.append(extra)
    return "；".join(bits)


# --------------------------------------------------------------------------- #
# 检查编排（fetch/process_info 可注入，便于测试）
# --------------------------------------------------------------------------- #
def _probe_process() -> dict:
    """查 8091 监听 pid + 进程启动时间 + C1 代码 mtime（L1 口径）。只读。"""
    info = {"pid": 0, "start": "", "code_mtime": 0.0, "error": ""}
    cmd = ("$c=Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction SilentlyContinue;"
           "if($c){($c|Select-Object -First 1 -ExpandProperty OwningProcess)}")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        info["pid"] = int((p.stdout or "0").strip() or 0)
    except Exception as e:  # noqa: BLE001
        info["error"] = "pid_lookup_failed:%s" % type(e).__name__
        return info
    if not info["pid"]:
        info["error"] = "8091_not_listening"
        return info
    try:
        ps = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d';"
              "if($p){$p.CreationDate.ToString('o')}" % info["pid"])
        r2 = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=20)
        info["start"] = (r2.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        info["error"] = "process_lookup_failed:%s" % type(e).__name__
        return info
    mtimes = []
    for rel in C1_CODE_FILES:
        p = ROOT / rel
        try:
            if p.is_file():
                mtimes.append(p.stat().st_mtime)
        except OSError:
            continue
    info["code_mtime"] = max(mtimes) if mtimes else 0.0
    return info


def _activation_row(process_info: dict | None) -> tuple[str, str, str]:
    """→ (status, detail, expected)。未换血/无法判定=PENDING（不伪 PASS）。"""
    info = process_info if process_info is not None else _probe_process()
    if info.get("skip"):
        return STATUS_PENDING, "已跳过（--skip-process）", "pid/启动时间 > C1 代码 mtime"
    if info.get("error"):
        return STATUS_PENDING, "无法判定：%s pid=%s" % (info["error"], info.get("pid", 0)), \
            "pid/启动时间 > C1 代码 mtime"
    pid = info.get("pid") or 0
    start = str(info.get("start") or "")
    mtime = float(info.get("code_mtime") or 0.0)
    if not pid or not start:
        return STATUS_PENDING, "无法判定：pid=%s start=%r" % (pid, start), \
            "pid/启动时间 > C1 代码 mtime"
    try:
        st = _dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        start_ts = st.timestamp()
    except ValueError:
        return STATUS_PENDING, "无法解析启动时间：%r" % start, "pid/启动时间 > C1 代码 mtime"
    code_iso = (_dt.datetime.fromtimestamp(mtime).isoformat() if mtime else "?")
    detail = "pid=%s start=%s code_mtime=%s" % (pid, start, code_iso)
    if mtime and start_ts <= mtime:
        return STATUS_PENDING, "需 8091 换血（启动时间未晚于 C1 代码）；" + detail, \
            "pid/启动时间 > C1 代码 mtime"
    return STATUS_PASS, detail, "pid/启动时间 > C1 代码 mtime"


def verify(fetch=None, process_info=None, base_8091: str = PORTAL_8091,
           base_8734: str = PUBLIC_8734, timeout: float = 8.0) -> dict:
    """执行全部只读检查，返回报告 dict。fetch 可注入（测试用）。"""
    fetch = fetch or (lambda url: http(url, timeout=timeout))
    rows: list[dict] = []

    def add(group: str, name: str, status: str, detail: str = "",
            expected: str = "", actual: str = "") -> None:
        rows.append({"group": group, "name": name, "status": status,
                     "detail": detail, "expected": expected, "actual": actual})

    # ---------- 1) 8091 health ----------
    health = fetch(base_8091 + "/health")
    hp = health.json() if not health.error else None
    ok = health.code == 200 and isinstance(hp, dict) and hp.get("ok") is True
    add("8091", "/health", STATUS_PASS if ok else STATUS_FAIL,
        _detail(health), "200 + ok=true",
        "code=%s ok=%s" % (health.code, (hp or {}).get("ok") if isinstance(hp, dict) else None))

    # ---------- 2) 8091 /api/hq/* ----------
    api_data: dict = {}
    for name in HQ_APIS:
        resp = fetch("%s/api/hq/%s" % (base_8091, name))
        payload = resp.json() if not resp.error else None
        ok = (resp.code == 200 and isinstance(payload, dict)
              and payload.get("ok") is True and payload.get("data") is not None)
        if ok:
            api_data[name] = payload.get("data")
        add("api", "/api/hq/%s" % name, STATUS_PASS if ok else STATUS_FAIL,
            _detail(resp), "200 + ok=true + data",
            "code=%s ok=%s" % (resp.code,
                               payload.get("ok") if isinstance(payload, dict) else None))

    # ---------- 3) snapshot 必需键/计数 ----------
    snap = api_data.get("snapshot")
    snap_ok = isinstance(snap, dict)

    def content_row(name: str, check, detail_ok: str) -> None:
        if not snap_ok:
            add("snapshot", name, STATUS_PENDING, "snapshot 未获取（上项 FAIL/PENDING）",
                "snapshot 含必需键/计数", "unavailable")
            return
        try:
            ok = bool(check())
            extra = detail_ok if ok else "实际=%s" % _snap_digest(snap)
        except Exception as e:  # noqa: BLE001
            ok = False
            extra = "检查异常:%s" % type(e).__name__
        add("snapshot", name, STATUS_PASS if ok else STATUS_FAIL, extra,
            "snapshot 含必需键/计数", "" if ok else "mismatch")

    content_row("必需键齐全", lambda: all(k in snap for k in SNAPSHOT_KEYS),
                "keys=%d" % len([k for k in SNAPSHOT_KEYS if isinstance(snap, dict) and k in snap]))
    content_row("roles=5", lambda: (len((snap.get("roles") or {}).get("roles") or []) == 5
                                    and (snap.get("roles") or {}).get("summary", {}).get("roles") == 5),
                "roles=5")
    content_row("directives 非空", lambda: (len((snap.get("directives") or {}).get("directives") or []) > 0
                                           and (snap.get("directives") or {}).get("summary", {}).get("total", 0) > 0),
                "directives>0")
    content_row("reports.legions 非空", lambda: (len((snap.get("reports") or {}).get("legions") or []) > 0
                                                and all(r.get("status") in ("green", "amber", "red")
                                                        for r in (snap.get("reports") or {}).get("legions") or [])),
                "legions>0 且 status 合法")
    content_row("commercial.summary", lambda: isinstance((snap.get("commercial") or {}).get("summary"), dict)
                                             and bool((snap.get("commercial") or {}).get("summary")),
                "commercial.summary 非空")

    # ---------- 4) 8734 公共入口（不跟随重定向） ----------
    hq = fetch(base_8734 + "/hq")
    loc = hq.headers.get("Location") or hq.headers.get("location") or ""
    if hq.code == 302:
        if "login" in loc.lower() or loc.startswith("/login"):
            add("public", "/hq 未登录跳转", STATUS_PASS, _detail(hq), "302 → /login?next=/hq")
        else:
            add("public", "/hq 未登录跳转", STATUS_PENDING,
                "302 但 Location 非登录页：" + _detail(hq), "302 → /login?next=/hq")
    elif hq.code == 200 and any(m in hq.body for m in LOGIN_MARKERS):
        add("public", "/hq 未登录跳转", STATUS_PENDING,
            "200 登录页（与 C1 口径 302 不符，入口可达但不满足验收口径）；" + _detail(hq),
            "302 → /login?next=/hq")
    else:
        add("public", "/hq 未登录跳转", STATUS_FAIL, _detail(hq), "302 → /login?next=/hq")

    snap_pub = fetch(base_8734 + "/api/hq/snapshot")
    if snap_pub.code == 401:
        add("public", "/api/hq/snapshot 未登录=401", STATUS_PASS, _detail(snap_pub),
            "401（不暴露数据）")
    elif snap_pub.code == 200:
        add("public", "/api/hq/snapshot 未登录=401", STATUS_FAIL,
            "未登录返回 200：敏感子集鉴权失效！" + _detail(snap_pub), "401（不暴露数据）")
    elif snap_pub.code in (302, 403):
        add("public", "/api/hq/snapshot 未登录=401", STATUS_PENDING,
            "返回 %s（非 JSON 401 口径）；%s" % (snap_pub.code, _detail(snap_pub)),
            "401（不暴露数据）")
    else:
        add("public", "/api/hq/snapshot 未登录=401", STATUS_FAIL, _detail(snap_pub),
            "401（不暴露数据）")

    # ---------- 5) L1 口径：8091 已换血 ----------
    st, detail, expected = _activation_row(process_info)
    add("activation", "8091 新代码生效（启动时间>C1代码mtime）", st, detail, expected)

    fails = [r for r in rows if r["status"] == STATUS_FAIL]
    pend = [r for r in rows if r["status"] == STATUS_PENDING]
    passes = [r for r in rows if r["status"] == STATUS_PASS]
    verdict = (STATUS_FAIL if fails else STATUS_PENDING if pend else STATUS_PASS)
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "targets": {"portal_8091": base_8091, "public_8734": base_8734},
        "rows": rows,
        "summary": {"total": len(rows), "pass": len(passes),
                    "pending": len(pend), "fail": len(fails)},
        "verdict": verdict,
    }


def _snap_digest(snap: dict) -> dict:
    """失败时给一段可复核摘要（不转储大对象）。"""
    roles = (snap.get("roles") or {}).get("roles") or []
    directs = (snap.get("directives") or {}).get("directives") or []
    legions = (snap.get("reports") or {}).get("legions") or []
    return {"keys": [k for k in SNAPSHOT_KEYS if k in snap],
            "roles": len(roles), "directives": len(directs),
            "legions": len(legions),
            "commercial_summary": bool((snap.get("commercial") or {}).get("summary"))}


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def render_text(report: dict) -> str:
    lines = ["== C1 激活后自检（只读）==",
             "目标：8091=%s｜8734=%s" % (report["targets"]["portal_8091"],
                                         report["targets"]["public_8734"]),
             "时点：%s" % report["generated_at"]]
    for r in report["rows"]:
        lines.append("[%-7s] %-10s %-32s %s" % (r["status"], r["group"],
                                                r["name"], r["detail"]))
    s = report["summary"]
    lines.append("汇总：%d 项；PASS=%d；PENDING=%d；FAIL=%d；判定=%s"
                 % (s["total"], s["pass"], s["pending"], s["fail"], report["verdict"]))
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    s = report["summary"]
    out = ["# C1 激活后自检报告", "",
           "- 时点：`%s`" % report["generated_at"],
           "- 目标：`%s` / `%s`" % (report["targets"]["portal_8091"],
                                    report["targets"]["public_8734"]),
           "- 汇总：%d 项｜PASS=%d｜PENDING=%d｜FAIL=%d｜**判定=%s**"
           % (s["total"], s["pass"], s["pending"], s["fail"], report["verdict"]),
           "", "| 组 | 检查项 | 状态 | 明细 |", "|---|---|---|---|"]
    for r in report["rows"]:
        out.append("| %s | %s | %s | %s |" % (r["group"], r["name"], r["status"],
                                              str(r["detail"]).replace("|", "\\|")))
    return "\n".join(out)


def main(argv=None, fetch=None, process_info=None) -> int:
    ap = argparse.ArgumentParser(description="C1 激活后自检器（只读）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", action="store_true", help="stdout 输出纯 JSON")
    g.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown")
    ap.add_argument("--base-8091", default=PORTAL_8091)
    ap.add_argument("--base-8734", default=PUBLIC_8734)
    ap.add_argument("--timeout", type=float, default=8.0)
    ap.add_argument("--allow-pending", action="store_true", help="PENDING 也返回 0")
    ap.add_argument("--skip-process", action="store_true", help="跳过 8091 换血检查（记 PENDING）")
    args = ap.parse_args(argv)
    if args.skip_process and process_info is None:
        process_info = {"skip": True}
    report = verify(fetch=fetch, process_info=process_info,
                    base_8091=args.base_8091, base_8734=args.base_8734,
                    timeout=args.timeout)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    elif args.markdown:
        print(render_markdown(report))
    else:
        print(render_text(report))
    if report["verdict"] == STATUS_FAIL:
        return 1
    if report["verdict"] == STATUS_PENDING and not args.allow_pending:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
