#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冲突工作量量化：每个冲突文件在分支侧相对 base 改了多少行。

用途：判断「117 处冲突」是人力可完成还是不可完成，并据此分配处置策略。
同时给出「取 ours 会丢弃多少分支行」——这是统一策略的代价指标。
"""
import json
import os
import subprocess
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

_here = os.path.dirname(os.path.abspath(__file__))
REPO = subprocess.run(["git", "-C", _here, "rev-parse", "--show-toplevel"],
                      capture_output=True, text=True, encoding="utf-8").stdout.strip() or os.getcwd()
OUT = os.path.join(REPO, ".git", "wb-branch-audit")


def g(*a):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def numstat(a, b, path):
    r = g("diff", "--numstat", a, b, "--", path)
    if r.returncode != 0 or not r.stdout.strip():
        return 0, 0
    parts = r.stdout.split("\t")
    try:
        return int(parts[0]) if parts[0] != "-" else 0, int(parts[1]) if parts[1] != "-" else 0
    except (ValueError, IndexError):
        return 0, 0


def main():
    d = json.load(open(os.path.join(OUT, "_conflict_detail.json"), encoding="utf-8"))
    master = d["master"]
    rows = d["rows"]

    tot_theirs = tot_ours = 0
    path_tot = defaultdict(lambda: [0, 0])
    for r in rows:
        for c in r["conflicts"]:
            p = c["path"]
            add_t, del_t = numstat(r["base"], r["branch"], p)   # 分支侧改动量
            add_o, del_o = numstat(r["base"], master, p)        # master 侧改动量
            c["theirs_lines"] = add_t + del_t
            c["ours_lines"] = add_o + del_o
            tot_theirs += c["theirs_lines"]
            tot_ours += c["ours_lines"]
            path_tot[p][0] += c["theirs_lines"]
            path_tot[p][1] += c["ours_lines"]
        r["sum_theirs"] = sum(c["theirs_lines"] for c in r["conflicts"])

    json.dump({"master": master, "rows": rows,
               "total_theirs_lines": tot_theirs, "total_ours_lines": tot_ours},
              open(os.path.join(OUT, "_conflict_effort.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("冲突文件条目 %d" % sum(r["nconf"] for r in rows))
    print("分支侧改动行合计 %d 行 | master 侧改动行合计 %d 行" % (tot_theirs, tot_ours))
    print()
    print("=== 分支侧改动最大的 15 个冲突文件（合并时最可能丢内容） ===")
    for p, (t, o) in sorted(path_tot.items(), key=lambda x: -x[1][0])[:15]:
        print("%6d 行分支侧 / %6d 行 master 侧   %s" % (t, o, p))

    print()
    print("=== 按分支：分支侧改动总量（工作量代理指标） ===")
    for r in sorted(rows, key=lambda x: -x["sum_theirs"])[:20]:
        print("%6d 行  %-46s 冲突%2d" % (r["sum_theirs"], r["branch"][:46], r["nconf"]))

    # 分档
    tiers = {"<50行": 0, "50-200": 0, "200-1000": 0, ">1000": 0}
    for r in rows:
        s = r["sum_theirs"]
        if s < 50:
            tiers["<50行"] += 1
        elif s < 200:
            tiers["50-200"] += 1
        elif s < 1000:
            tiers["200-1000"] += 1
        else:
            tiers[">1000"] += 1
    print()
    print("分支分档:", tiers)


if __name__ == "__main__":
    main()
