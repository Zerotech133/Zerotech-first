#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""判断「已存在但版本不同」的文件谁更新：master 侧最后改动时间 vs 分支侧最后改动时间。

若 master 侧更晚，说明合并分支等于回滚 master；这类文件的正确处置是「保留 master」。
"""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

_here = os.path.dirname(os.path.abspath(__file__))
REPO = subprocess.run(["git", "-C", _here, "rev-parse", "--show-toplevel"],
                      capture_output=True, text=True, encoding="utf-8").stdout.strip() or os.getcwd()
OUT = os.path.join(REPO, ".git", "wb-branch-audit")


def g(*a):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def last_ct(rev, path):
    r = g("log", "-1", "--format=%ct", rev, "--", path)
    s = r.stdout.strip()
    return int(s) if s.isdigit() else 0


def main():
    d = json.load(open(os.path.join(OUT, "_missing_vs_both.json"), encoding="utf-8"))
    master = d["master"]
    rows = d["rows"]

    tasks = []
    for r in rows:
        for f in r["both"]:
            tasks.append((r["branch"], f))

    def probe(t):
        br, f = t
        return br, f, last_ct(master, f), last_ct(br, f)

    with ThreadPoolExecutor(max_workers=12) as ex:
        res = list(ex.map(probe, tasks))

    master_newer = sum(1 for _, _, cm, cb in res if cm > cb)
    branch_newer = sum(1 for _, _, cm, cb in res if cb > cm)
    same = len(res) - master_newer - branch_newer
    print("「已存在但版本不同」文件 %d 个：" % len(res))
    print("  master 侧更晚（合并=回滚）: %d (%.0f%%)" % (master_newer, 100.0 * master_newer / max(1, len(res))))
    print("  分支侧更晚（可能有新内容）: %d (%.0f%%)" % (branch_newer, 100.0 * branch_newer / max(1, len(res))))
    print("  同时间戳: %d" % same)

    print()
    print("=== 分支侧更晚的文件（需逐个人工看一眼，可能含真新内容） ===")
    for br, f, cm, cb in sorted([x for x in res if x[3] > x[2]], key=lambda x: -(x[3] - x[2]))[:25]:
        import datetime
        dm = datetime.datetime.fromtimestamp(cm).strftime("%m-%d %H:%M")
        db = datetime.datetime.fromtimestamp(cb).strftime("%m-%d %H:%M")
        print("  master %s < 分支 %s  %-44s %s" % (dm, db, f[:44], br.replace("task/", "")[:28]))

    json.dump(res, open(os.path.join(OUT, "_recency.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
