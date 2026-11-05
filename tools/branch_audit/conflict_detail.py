#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对 36 个需人工分支采集冲突文件路径 + 冲突块规模，用于制定统一处置方案。

输出 .git/wb-branch-audit/_conflict_detail.json
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

REPO = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                      text=True, encoding="utf-8", cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
if not REPO:
    REPO = os.getcwd()
OUT = os.path.join(REPO, ".git", "wb-branch-audit")


def g(*a, **kw):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", **kw)


def main():
    plan = json.load(open(os.path.join(OUT, "_plan_v3.json"), encoding="utf-8"))
    tri = {r["branch"]: r for r in json.load(open(os.path.join(OUT, "_triage_v2.json"), encoding="utf-8"))}
    master = g("rev-parse", "master").stdout.strip()

    manual = []
    for p in plan["plan"]:
        for s in p["skipped"]:
            manual.append((p["line"], s))

    def probe(item):
        line, s = item
        b = s["branch"]
        base = g("merge-base", "master", b).stdout.strip()
        r = g("merge-tree", "--write-tree", "--merge-base=" + base, "master", b)
        # 冲突行形如: CONFLICT (content): Merge conflict in path
        #             CONFLICT (add/add): Merge conflict in path
        #             CONFLICT (modify/delete): path deleted in ... and modified in ...
        confs = []
        for ln in r.stdout.splitlines():
            m = re.match(r"CONFLICT \(([^)]+)\): (.*)$", ln)
            if not m:
                continue
            kind, rest = m.group(1), m.group(2)
            if rest.startswith("Merge conflict in "):
                path = rest[len("Merge conflict in "):]
            elif rest.startswith("Auto-merging "):
                continue
            else:
                # modify/delete: "path deleted in HEAD and modified in xxx.  Version xxx of path left in tree."
                path = rest.split(" deleted in ")[0].split(" modified in ")[0].strip()
            confs.append({"kind": kind, "path": path})
        last = g("log", "-1", "--format=%ct", b).stdout.strip()
        nsub = g("rev-list", "--count", "master.." + b).stdout.strip()
        t = tri.get(b, {})
        return {
            "line": line,
            "branch": b,
            "base": base,
            "master": master,
            "nconf": len(confs),
            "conflicts": confs,
            "net": t.get("net", 0),
            "net_files": t.get("net_files", []),
            "kinds": t.get("kinds", {}),
            "last_ct": int(last) if last.isdigit() else 0,
            "n_own_commits": int(nsub) if nsub.isdigit() else 0,
            "subject": g("log", "-1", "--format=%s", b).stdout.strip(),
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(probe, manual))

    json.dump({"master": master, "rows": rows}, open(os.path.join(OUT, "_conflict_detail.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # ---- 汇总 ----
    import collections
    path_cnt = collections.Counter()
    kind_cnt = collections.Counter()
    for r in rows:
        for c in r["conflicts"]:
            path_cnt[c["path"]] += 1
            kind_cnt[c["kind"]] += 1

    print("分支 %d 个 / 冲突文件条目合计 %d" % (len(rows), sum(r["nconf"] for r in rows)))
    print("冲突类型:", dict(kind_cnt))
    print("\n=== 出现在 2 个以上分支的热点冲突文件 top 20 ===")
    for p, n in path_cnt.most_common(20):
        if n >= 2:
            print("%3d 次  %s" % (n, p))
    hot = sum(n for p, n in path_cnt.items() if n >= 2)
    print("\n热点文件(>=2分支)占用冲突条目 %d / %d = %.0f%%" % (hot, sum(r["nconf"] for r in rows),
                                                    100.0 * hot / max(1, sum(r["nconf"] for r in rows))))
    print("独立文件种类 %d" % len(path_cnt))


if __name__ == "__main__":
    main()
