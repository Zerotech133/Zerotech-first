#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""军团 API 只读探针（直连 8096；不经 nginx /legion-api）。

背景（2026-10-30 P103）：`/legion-api/` 已加登录保护（未登录 302 /login），
外部脚本/探针若仍走 8734/legion-api 会被登录页拦截。本工具直连 8096
（本机可信域，只读 GET），供巡检/日报/外部探针使用。

用法：
  python tools/legion_probe.py           # 摘要
  python tools/legion_probe.py --json    # JSON
  LEGION_API_BASE=http://127.0.0.1:8096 python tools/legion_probe.py
退出码：0 正常；1 任一接口不可用。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("LEGION_API_BASE", "http://127.0.0.1:8096").rstrip("/")
TIMEOUT = float(os.environ.get("LEGION_PROBE_TIMEOUT", "5"))


def _get(path: str, timeout: float = TIMEOUT):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def probe() -> dict:
    import time
    out = {"ok": True, "base": BASE, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "errors": []}
    try:
        st, ops = _get("/api/legion/ops")
        out["ops_status"] = st
        out["members"] = len(ops.get("members") or ops.get("roster") or [])
        out["tasks"] = len(ops.get("tasks") or [])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["ok"] = False
        out["errors"].append("ops: %s: %s" % (type(exc).__name__, exc))
    try:
        st, topics = _get("/api/legion/academy/topics")
        out["topics_status"] = st
        out["topics"] = topics.get("n")
        out["by_stage"] = topics.get("by_stage")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        out["ok"] = False
        out["errors"].append("academy/topics: %s: %s" % (type(exc).__name__, exc))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="军团 API 只读探针（直连 8096）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = probe()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("legion_probe: ok=%s base=%s members=%s topics=%s"
              % (rep["ok"], rep.get("base"), rep.get("members"), rep.get("topics")))
        if rep.get("by_stage"):
            print("  by_stage", rep["by_stage"])
        for e in rep["errors"]:
            print("  ERROR", e)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
