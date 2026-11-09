#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冲突定性：把「冲突文件数」拆成「真冲突 / 假冲突」。

对每个冲突文件取三方 blob（base / master=ours / branch=theirs）比内容哈希：
  SAME           ours == theirs            → 假冲突，取任一侧即可，无需判断
  OURS_ONLY      ours != base, theirs==base → 我方已改，取 ours
  THEIRS_ONLY    theirs != base, ours==base → 分支侧改动，取 theirs
  BOTH           ours != base 且 theirs != base 且 ours != theirs → **真冲突**
  BOTH_ADDED     base 不存在，两侧都新增且不同 → 真冲突（两个不同版本的同名新文件）
  MOD_DEL        一侧改一侧删                  → 真冲突

只有 BOTH / BOTH_ADDED / MOD_DEL 需要人判断，其余可脚本化。
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


def blob(rev, path):
    """返回 (存在?, 内容字节)"""
    r = subprocess.run(["git", "-C", REPO, "cat-file", "blob", "%s:%s" % (rev, path)],
                       capture_output=True)
    if r.returncode != 0:
        return False, b""
    return True, r.stdout


def classify(base, ours, theirs, path):
    """返回 (类别, 说明)"""
    be, bb = blob(base, path) if base else (False, b"")
    oe, ob = blob(ours, path)
    te, tb = blob(theirs, path)

    if oe and te and ob == tb:
        return "SAME", "两侧内容一致，取任一即可"
    if not be and oe and te:
        return "BOTH_ADDED", "分叉点无此文件，两侧各自新增且内容不同"
    if be and oe and te:
        # 正常三方
        if ob == bb:
            return "THEIRS_ONLY", "master 未动，分支侧有改动"
        if tb == bb:
            return "OURS_ONLY", "分支侧未动，master 已改"
        return "BOTH", "两侧都改过同一文件"
    if oe and not te:
        return ("OURS_KEPT", "分支侧删除、master 保留/修改") if not be else ("MOD_DEL", "分支删了 master 改了")
    if te and not oe:
        return ("THEIRS_REMOVED", "master 侧无此文件，分支新增") if not be else ("MOD_DEL", "master 删了分支改了")
    return "GONE", "两侧都不存在"


def main():
    d = json.load(open(os.path.join(OUT, "_conflict_detail.json"), encoding="utf-8"))
    rows = d["rows"]
    master = d["master"]

    def work(r):
        out = []
        for c in r["conflicts"]:
            kind, why = classify(r["base"], master, r["branch"], c["path"])
            out.append({"path": c["path"], "merge_kind": c["kind"],
                        "class": kind, "why": why})
        r["detail"] = out
        r["real"] = sum(1 for x in out if x["class"] in ("BOTH", "BOTH_ADDED", "MOD_DEL"))
        r["fake"] = len(out) - r["real"]
        return r

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(work, rows))

    json.dump({"master": master, "rows": rows},
              open(os.path.join(OUT, "_conflict_class.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    import collections
    cls = collections.Counter()
    for r in rows:
        for x in r["detail"]:
            cls[x["class"]] += 1
    tr = sum(r["real"] for r in rows)
    tf = sum(r["fake"] for r in rows)
    print("冲突文件条目 %d → 真冲突 %d / 假冲突 %d（可直接脚本化）" % (tr + tf, tr, tf))
    print("分类:", dict(cls.most_common()))
    print()
    print("%-46s %4s %5s %5s   %s" % ("branch", "冲突", "真", "假", "真冲突文件"))
    for r in sorted(rows, key=lambda x: -x["real"]):
        real = [x["path"] for x in r["detail"] if x["class"] in ("BOTH", "BOTH_ADDED", "MOD_DEL")]
        print("%-46s %4d %5d %5d   %s" % (r["branch"][:46], r["nconf"], r["real"], r["fake"],
                                          ", ".join(p[:28] for p in real[:3])))


if __name__ == "__main__":
    main()
