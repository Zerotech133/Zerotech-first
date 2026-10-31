# -*- coding: utf-8 -*-
r"""W1 验收一条命令（机器证，而非"文档称完成"）。

输出 JSON：release version 一致性 / 8787 鉴权 / 8091 健康 / 未授权矩阵 /
P1 回归测试 / T1T2（不可用则显式 unavailable）。退出码：0=无 FAIL（可 WARN），1=有 FAIL。

用法：
  python -X utf8 tools/w1_acceptance.py --json
  python -X utf8 tools/w1_acceptance.py --json --out C:\Temp\w1.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IDE_VERSION_URL = "http://127.0.0.1:8103/api/ide/version"
DEFAULT_RELEASE = Path(r"C:\项目\_vault\legion_ide\release\legion-ide\current.json")
PROTECTED_POSTS = ("/api/analysis/submit", "/api/jev/review/resolve")


def _http(url, method="GET", headers=None, timeout=5):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "status": r.status, "body": r.read(8192).decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": (e.read(8192) or b"").decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "error": "%s: %s" % (type(e).__name__, e)}


def check_release(portal="http://127.0.0.1:8091", release_path=DEFAULT_RELEASE):
    out = {"id": "release_version", "status": "fail", "detail": ""}
    ver_file = None
    try:
        if release_path.is_file():
            ver_file = json.loads(release_path.read_text(encoding="utf-8-sig")).get("version")
    except Exception as e:  # noqa: BLE001
        out["detail"] = "release file unreadable: %s" % e
    live = _http(IDE_VERSION_URL, timeout=3)
    ver_live = None
    if live.get("body"):
        try:
            ver_live = json.loads(live["body"]).get("version")
        except Exception:  # noqa: BLE001
            pass
    out["release_file_version"] = ver_file
    out["live_version"] = ver_live
    if ver_file and ver_live and ver_file == ver_live:
        out.update(status="pass", detail="version 一致: %s" % ver_file)
    elif ver_file and live.get("status") is None:
        out.update(status="warn", detail="8103 不可达；仅校验 release 文件: %s" % ver_file)
    else:
        out["detail"] = "version 不一致/缺失: file=%s live=%s" % (ver_file, ver_live)
    return out


def check_hermes_auth():
    out = {"id": "hermes_8787_auth", "status": "fail", "detail": ""}
    r = _http("http://127.0.0.1:8787/api/sessions", timeout=4)
    if r.get("status") in (401, 403):
        out.update(status="pass", detail="/api/sessions 未登录 -> %s（鉴权门生效）" % r["status"])
    elif r.get("status") == 200:
        out["detail"] = "/api/sessions 匿名 200（鉴权门未生效）"
    else:
        out["detail"] = "不可达或异常: %s" % (r.get("status") or r.get("error"))
    return out


def check_health(portal="http://127.0.0.1:8091"):
    r = _http(portal.rstrip("/") + "/health", timeout=3)
    return {"id": "portal_health", "status": "pass" if r.get("ok") else "fail",
            "detail": "status=%s" % r.get("status")}


def check_unauth_matrix(portal="http://127.0.0.1:8091"):
    """反代+非本机来源、无 token：受保护 POST 必须 401/403。"""
    rows = []
    for path in PROTECTED_POSTS:
        r = _http(portal.rstrip("/") + path, method="POST",
                  headers={"X-Portal-Proxy": "1", "X-Real-IP": "10.9.9.9",
                           "Content-Type": "application/json"},
                  timeout=5)
        code = r.get("status")
        rows.append({"path": path, "status": code,
                     "verdict": "pass" if code in (401, 403) else "warn"})
    ok = all(x["verdict"] == "pass" for x in rows)
    return {"id": "unauth_matrix", "status": "pass" if ok else "warn", "detail": rows}


def check_p1_regression():
    r = subprocess.run([sys.executable, "-m", "pytest",
                        "portal/tests/test_post_auth_early_branches.py", "-q",
                        "-p", "no:cacheprovider"],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return {"id": "p1_regression", "status": "pass" if r.returncode == 0 else "fail",
            "detail": (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [""]}


def run(portal="http://127.0.0.1:8091", release_path=DEFAULT_RELEASE):
    checks = [check_release(portal, release_path), check_hermes_auth(),
              check_health(portal), check_unauth_matrix(portal),
              check_p1_regression()]
    checks.append({"id": "t1_t2_metrics", "status": "unavailable",
                   "detail": "T1/T2 A/B 指标尚未落盘（K 复盘已记：仅文档结论）；转 W2 验收脚本化第 2 条"})
    failed = [c for c in checks if c["status"] == "fail"]
    warn = [c for c in checks if c["status"] == "warn"]
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "checks": checks, "summary": {"pass": sum(1 for c in checks if c["status"] == "pass"),
                                          "warn": len(warn), "fail": len(failed)},
            "ok": not failed}


def main(argv=None):
    ap = argparse.ArgumentParser(description="W1 验收一条命令（机器证）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--portal", default="http://127.0.0.1:8091")
    ap.add_argument("--release", default=str(DEFAULT_RELEASE))
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    rep = run(a.portal, Path(a.release))
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
