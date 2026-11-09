#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把冲突工作量按文件性质拆分：生成物 / 文档 / 测试 / 代码。

生成物可由重跑生成器消解，文档与测试可机械取并集，只有代码需要判断。
"""
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

_here = os.path.dirname(os.path.abspath(__file__))
REPO = subprocess.run(["git", "-C", _here, "rev-parse", "--show-toplevel"],
                      capture_output=True, text=True, encoding="utf-8").stdout.strip() or os.getcwd()
OUT = os.path.join(REPO, ".git", "wb-branch-audit")

GEN_MARK = re.compile(rb'"generated_at"|"generator"|"generated_by"|"source":\s*"C:')

GEN_PATH = re.compile(r"(config/page_manifest\.json|portal_route_baseline\.json|compositions/registry\.json|"
                      r"dataset_refs_declared\.json|strategy_registry\.json|_registry\.json|"
                      r"research/reports/.*\.json|\.bundle\.json|page_manifest)")


def nature(rev, path):
    low = path.lower()
    if low.endswith(".md"):
        return "文档"
    if GEN_PATH.search(path):
        return "生成物"
    if "/tests/" in path or os.path.basename(path).startswith("test_"):
        return "测试"
    if low.endswith(".json") or low.endswith(".yaml") or low.endswith(".yml"):
        # 看内容是否带生成标记
        r = subprocess.run(["git", "-C", REPO, "cat-file", "blob", "%s:%s" % (rev, path)],
                           capture_output=True)
        if r.returncode == 0 and GEN_MARK.search(r.stdout[:4000]):
            return "生成物"
        return "配置/数据"
    return "代码"


def main():
    d = json.load(open(os.path.join(OUT, "_conflict_effort.json"), encoding="utf-8"))
    rows = d["rows"]

    agg = defaultdict(int)
    cnt = defaultdict(int)
    for r in rows:
        for c in r["conflicts"]:
            n = nature(r["branch"], c["path"])
            c["nature"] = n
            agg[n] += c["theirs_lines"]
            cnt[n] += 1
        r["sum_by_nature"] = defaultdict(int)
    total = sum(agg.values())
    print("%-10s %6s %8s %8s" % ("性质", "文件数", "分支行数", "占比"))
    for k in sorted(agg, key=lambda x: -agg[x]):
        print("%-10s %6d %8d %7.1f%%" % (k, cnt[k], agg[k], 100.0 * agg[k] / total))
    print("%-10s %6d %8d" % ("合计", sum(cnt.values()), total))
    print()

    # 按分支给出「代码类」行数 —— 这才是真需要判断的量
    print("%-46s %6s %6s %6s" % ("branch", "总行", "代码行", "生成物行"))
    per = []
    for r in rows:
        code = sum(c["theirs_lines"] for c in r["conflicts"] if c["nature"] == "代码")
        gen = sum(c["theirs_lines"] for c in r["conflicts"] if c["nature"] == "生成物")
        per.append((code, gen, r))
    for code, gen, r in sorted(per, key=lambda x: -x[0])[:18]:
        print("%-46s %6d %6d %6d" % (r["branch"][:46], r["sum_theirs"], code, gen))
    print()
    print("代码类合计 %d 行 / 分支 %d 个有代码冲突" % (
        sum(x[0] for x in per), sum(1 for x in per if x[0] > 0)))

    json.dump({"master": d["master"], "rows": rows},
              open(os.path.join(OUT, "_conflict_nature.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
