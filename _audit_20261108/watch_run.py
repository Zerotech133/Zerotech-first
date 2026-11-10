# -*- coding: utf-8 -*-
"""等待指定 sha 的 CI run 完成，并打印步骤结论 + 失败清单。

用法：python _audit_20261108/watch_run.py <sha前缀> [token文件] [--max-min N]
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
import zipfile

API = "https://api.github.com/repos/Zerotech133/Zerotech-first"
LOGDIR = r"C:\项目\老田的AI助手\_audit_20261108\ci_logs"


def get(path: str, tok: str | None = None, raw: bool = False, timeout: int = 60):
    hdr = {"Accept": "application/vnd.github+json", "User-Agent": "wb"}
    if tok:
        hdr["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(API + path, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if raw else json.loads(data.decode())


def main() -> int:
    sha = sys.argv[1]
    tok = None
    if len(sys.argv) > 2 and not sys.argv[2].startswith("--"):
        tok = open(sys.argv[2], encoding="utf-8").read().strip()
    maxmin = 15
    for i, a in enumerate(sys.argv):
        if a == "--max-min":
            maxmin = int(sys.argv[i + 1])

    run = None
    deadline = time.time() + maxmin * 60
    while time.time() < deadline:
        for r in get("/actions/runs?per_page=15", tok).get("workflow_runs", []):
            if r["head_sha"].startswith(sha):
                run = r
                break
        if run:
            print("[run] #%s id=%s %s/%s %s" % (run["run_number"], run["id"], run["status"],
                                               run.get("conclusion"), run["created_at"]),
                  flush=True)
            if run.get("status") == "completed" and run.get("conclusion") != "cancelled":
                break
        else:
            print("[run] 等待 %s 的 run 出现 ..." % sha, flush=True)
        time.sleep(45)

    if not run:
        print("未找到 run")
        return 1
    if run.get("status") != "completed":
        print("仍未完成，稍后再看")
        return 1

    jobs = get("/actions/runs/%s/jobs" % run["id"], tok).get("jobs", [])
    for j in jobs:
        print("[job] %s -> %s/%s" % (j["name"], j["status"], j.get("conclusion")))
        for s in j.get("steps", []):
            print("   %2d %-34s %s" % (s["number"], s["name"], s.get("conclusion")))

    try:
        blob = get("/actions/runs/%s/logs" % run["id"], tok, raw=True, timeout=180)
    except Exception as exc:  # noqa: BLE001
        print("[logs] 下载失败 %r" % (exc,))
        return 0

    import os
    outdir = os.path.join(LOGDIR, "run_%s" % run["id"])
    os.makedirs(outdir, exist_ok=True)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    zf.extractall(outdir)
    print("[logs] -> %s" % outdir)
    for name in zf.namelist():
        if not name.endswith(".txt"):
            continue
        txt = open(os.path.join(outdir, name), encoding="utf-8", errors="replace").read()
        if "GATE" not in txt and "新增失败" not in txt:
            continue
        for ln in txt.splitlines():
            body = ln.split("Z ", 1)[-1] if ln[:4].isdigit() else ln
            if any(k in body for k in ("GATE ", "新增失败", "无新增失败", "环境覆盖层",
                                       "❌", "ERR]", "baseline")):
                print("  | " + body[:170])
    return 0


if __name__ == "__main__":
    sys.exit(main())
