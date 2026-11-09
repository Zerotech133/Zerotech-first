#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""复现 inbox_gateway._store_lock 的 open 竞争（PermissionError: Errno 13）。

背景：2026-11-07 fast 门 agent_key 段报
  agent_platform/tests/test_inbox_gateway_lifecycle.py::
  test_submit_concurrent_windows_spawn_processes
失败，子进程栈停在
  inbox_gateway.py:110  with path.open("a+b") as lock:
  PermissionError: [Errno 13] Permission denied: .../.inbox_gateway.lock

该用例单独跑 3/3 全绿（未复现），但门禁里跑挂了 → 需判定是"环境抖动"还是
"确定性缺陷的窄窗口"。本脚本按同样形态（4 个 spawn 子进程、同 store、同幂等键）
循环 N 轮，统计子进程非零退出次数与异常行。

用法：
  python tools/research/repro_inbox_lock_race.py --rounds 25
  python tools/research/repro_inbox_lock_race.py --rounds 25 --json out.json
退出码：0 = 全部轮次干净；1 = 至少一轮复现（说明缺陷可复现，不是纯环境抖动）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "agent_platform"

RUNNER = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from gateway import inbox_gateway\n"
    "inbox_gateway.set_store(sys.argv[2])\n"
    "env = inbox_gateway.submit('question', {'q': 1},\n"
    "                           idempotency_key='process-key')\n"
    "with open(sys.argv[3], 'w', encoding='utf-8') as f:\n"
    "    json.dump(env, f, ensure_ascii=False)\n"
)


def one_round(workdir: Path, procs_n: int = 4):
    """返回 (bad_children, err_text)。"""
    runner = workdir / "_spawn_submit.py"
    runner.write_text(RUNNER, encoding="utf-8")
    store = workdir / "store"
    res = workdir / "results"
    res.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(PLATFORM))
    handles, procs = [], []
    for i in range(procs_n):
        ef = open(workdir / ("err%d.log" % i), "wb")
        handles.append(ef)
        procs.append(subprocess.Popen(
            [sys.executable, str(runner), str(PLATFORM), str(store),
             str(res / ("r%d.json" % i))],
            env=env, stdout=subprocess.DEVNULL, stderr=ef))
    try:
        for p in procs:
            p.wait(timeout=120)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        for ef in handles:
            try:
                ef.close()
            except OSError:
                pass
    bad = [p.returncode for p in procs if p.returncode != 0]
    errs = []
    for i in range(procs_n):
        t = (workdir / ("err%d.log" % i)).read_text(encoding="utf-8", errors="replace")
        if t.strip():
            errs.append(t)
    return bad, "\n".join(errs)


def classify(err_text: str) -> str:
    if "PermissionError" in err_text and ".inbox_gateway.lock" in err_text:
        return "PermissionError@lock"
    if "PermissionError" in err_text:
        return "PermissionError@other"
    if err_text.strip():
        return "other"
    return "clean"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=25)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    hits = []
    t0 = time.time()
    for r in range(1, args.rounds + 1):
        wd = Path(tempfile.mkdtemp(prefix="inboxrace_%02d_" % r))
        bad, errs = one_round(wd)
        kind = classify(errs) if bad else "clean"
        if bad:
            hits.append({"round": r, "rc": bad, "kind": kind, "dir": str(wd),
                         "err": errs[-1200:]})
        print("round %02d/%d  rc=%s  kind=%s" % (r, args.rounds, bad or "ok", kind))

    dt = time.time() - t0
    print("\n%d 轮 / %.1fs；失败轮次 %d" % (args.rounds, dt, len(hits)))
    kinds = {}
    for h in hits:
        kinds[h["kind"]] = kinds.get(h["kind"], 0) + 1
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        print("  %-24s %d" % (k, v))
    if hits:
        print("\n首条异常尾部：")
        print(hits[0]["err"][-700:])
    if args.json:
        Path(args.json).write_text(
            json.dumps({"rounds": args.rounds, "hits": hits}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
