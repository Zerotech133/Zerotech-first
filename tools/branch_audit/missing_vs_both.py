#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""区分两类「未落地」：
  MISSING  master 上根本没有这个文件  → 真实悬空工作，合并才能救回
  BOTH     master 上已有但版本不同    → 可能是旧版本/已被别的线取代

对 36 个冲突分支统计两类占比，判断「合并」是救回工作还是回滚 master。
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

_here = os.path.dirname(os.path.abspath(__file__))
REPO = subprocess.run(["git", "-C", _here, "rev-parse", "--show-toplevel"],
                      capture_output=True, text=True, encoding="utf-8").stdout.strip() or os.getcwd()
OUT = os.path.join(REPO, ".git", "wb-branch-audit")


def g(*a):
    return subprocess.run(["git", "-C", REPO] + list(a), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def exists(rev, path):
    return g("cat-file", "-e", "%s:%s" % (rev, path)).returncode == 0


def main():
    d = json.load(open(os.path.join(OUT, "_conflict_detail.json"), encoding="utf-8"))
    master = d["master"]
    rows = d["rows"]

    tot_missing = tot_both = 0
    print("%-46s %6s %7s %6s %6s" % ("branch", "net", "缺失", "存在但异", "冲突"))
    for r in rows:
        missing = [f for f in r["net_files"] if not exists(master, f)]
        both = [f for f in r["net_files"] if exists(master, f)]
        r["missing"] = missing
        r["both"] = both
        tot_missing += len(missing)
        tot_both += len(both)
        print("%-46s %6d %7d %6d %6d" % (r["branch"][:46], r["net"], len(missing), len(both), r["nconf"]))

    print()
    print("合计 net 文件 %d → master 完全缺失 %d（%.0f%%）/ 已存在但版本不同 %d（%.0f%%）" % (
        tot_missing + tot_both, tot_missing, 100.0 * tot_missing / max(1, tot_missing + tot_both),
        tot_both, 100.0 * tot_both / max(1, tot_missing + tot_both)))

    json.dump({"master": master, "rows": rows},
              open(os.path.join(OUT, "_missing_vs_both.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 全丢失判断：分支所有缺失文件里，有多少是「分支新增」而非「master 删过」
    print()
    print("=== 缺失文件最多的 10 个分支 ===")
    for r in sorted(rows, key=lambda x: -len(x["missing"]))[:10]:
        print("%3d 个缺失  %-44s 例: %s" % (len(r["missing"]), r["branch"][:44],
                                          ", ".join(f.split("/")[-1] for f in r["missing"][:3])))


if __name__ == "__main__":
    main()
