#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最终处置表：给 36 个冲突分支逐个下处置结论。

三条规则，全部以 master 为基准：
  D1 归档：无「master 缺失的代码/测试文件」，且分支侧版本更晚的文件数为 0
           → 打 tag + 删分支，不动任何内容
  D2 补缺：有 master 缺失的代码/测试文件
           → 只把这些文件加入集成分支，不合并、不改动既有文件（零冲突）
  D3 复核：有「master 上已存在、但分支侧更晚」的文件
           → 单独 diff，人工决定是否移植其中某几行

判定不依赖合并，因此不产生任何冲突。
"""
import json
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

_here = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("WB_REPO") or os.path.dirname(os.path.dirname(_here))
OUT = os.path.join(REPO, ".git", "wb-branch-audit")

ARTIFACT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".log", ".txt", ".csv",
            ".zip", ".bundle", ".mp4", ".webm")


def is_artifact(p):
    return p.lower().endswith(ARTIFACT)


def main():
    d = json.load(open(os.path.join(OUT, "_missing_vs_both.json"), encoding="utf-8"))
    nat = {r["branch"]: r for r in json.load(open(os.path.join(OUT, "_conflict_nature.json"), encoding="utf-8"))["rows"]}
    rec = json.load(open(os.path.join(OUT, "_recency.json"), encoding="utf-8"))
    master = d["master"]

    # recency: (branch, file, master_ct, branch_ct)
    newer = defaultdict(list)   # branch -> [files where branch newer]
    for br, f, cm, cb in rec:
        if cb > cm:
            newer[br].append(f)

    rows = d["rows"]
    out = []
    for r in rows:
        b = r["branch"]
        miss = r["missing"]
        miss_code = [f for f in miss if not is_artifact(f)]
        miss_art = [f for f in miss if is_artifact(f)]
        bt = [f for f in newer.get(b, [])]
        bt_code = [f for f in bt if not is_artifact(f)]

        if miss_code and bt_code:
            verdict = "D3"
        elif miss_code:
            verdict = "D2"
        elif bt_code:
            verdict = "D3"
        else:
            verdict = "D1"

        out.append({
            "branch": b, "line": r["line"], "verdict": verdict,
            "nconf": r["nconf"], "net": r["net"],
            "missing_code": miss_code, "missing_artifacts": miss_art,
            "branch_newer": bt, "branch_newer_code": bt_code,
            "code_lines": nat[b]["sum_by_nature"] if False else sum(
                c["theirs_lines"] for c in nat[b]["conflicts"] if c.get("nature") == "代码"),
            "subject": r["subject"],
        })

    json.dump({"master": master, "rows": out},
              open(os.path.join(OUT, "_disposition.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    cnt = defaultdict(int)
    for x in out:
        cnt[x["verdict"]] += 1
    print("处置分布:", dict(sorted(cnt.items())))
    print()

    for v in ("D2", "D3", "D1"):
        sel = [x for x in out if x["verdict"] == v]
        if not sel:
            continue
        title = {"D2": "D2 · 只补 master 缺失的新文件（零冲突）",
                 "D3": "D3 · 需人工看一眼「分支更晚」的文件",
                 "D1": "D1 · 归档不合并"}[v]
        print("=== %s  %d 个 ===" % (title, len(sel)))
        for x in sorted(sel, key=lambda y: -len(y["branch_newer_code"])):
            extra = ""
            if v == "D2":
                extra = "缺: " + ", ".join(f.split("/")[-1] for f in x["missing_code"][:4])
            elif v == "D3":
                extra = "分支更晚: " + ", ".join(f.split("/")[-1] for f in x["branch_newer_code"][:3])
            print("  %-46s %s" % (x["branch"][:46], extra))
        print()


if __name__ == "__main__":
    main()
