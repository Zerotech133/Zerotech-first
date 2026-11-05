#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分支支配关系分析：判断哪些分支是另一个分支的祖先（可被一并合入）。

若 A 是 B 的祖先，则合并 B 会连带合入 A → A 不需要单独合并。
这能把「36 次合并」压缩到「若干个线头」。
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


def main():
    plan = json.load(open(os.path.join(OUT, "_plan_v3.json"), encoding="utf-8"))
    cls = {r["branch"]: r for r in json.load(open(os.path.join(OUT, "_conflict_class.json"), encoding="utf-8"))["rows"]}

    clean, manual = [], []
    for p in plan["plan"]:
        for b in p["ok"]:
            clean.append((p["line"], b))
        for s in p["skipped"]:
            manual.append((p["line"], s["branch"]))

    allb = [(l, b, "clean") for l, b in clean] + [(l, b, "manual") for l, b in manual]
    names = [b for _, b, _ in allb]

    # 两两判祖先
    anc = defaultdict(set)  # anc[B] = {A: A 是 B 的祖先}
    for i, a in enumerate(names):
        for b in names:
            if a == b:
                continue
            r = g("merge-base", "--is-ancestor", a, b)
            if r.returncode == 0:
                anc[b].add(a)

    dominated = set()
    for b, s in anc.items():
        dominated |= s

    print("参与分析分支 %d（clean %d / manual %d）" % (len(names), len(clean), len(manual)))
    print("被判为他人祖先（可被连带合入、无需单合）: %d 个" % len(dominated))
    print()

    byline = defaultdict(list)
    for l, b, k in allb:
        byline[l].append((b, k))

    print("=== 按线：线头（需合并）vs 被支配（可略） ===")
    heads_total = 0
    for l in sorted(byline, key=lambda x: -len(byline[x])):
        items = byline[l]
        heads = [(b, k) for b, k in items if b not in dominated]
        subs = [(b, k) for b, k in items if b in dominated]
        heads_total += len(heads)
        flag = "  " if len(items) == 1 else "**"
        print("%s %-10s 共%d  需合%d  可略%d" % (flag, l, len(items), len(heads), len(subs)))
        for b, k in heads:
            c = cls.get(b)
            tag = "clean" if k == "clean" else ("真冲突%d" % c["real"] if c else "?")
            print("       HEAD  %-46s [%s]" % (b[:46], tag))
        for b, k in subs:
            print("       skip  %-46s (被上方包含)" % b[:46])
    print()
    print("总计需合并的分支: %d（原 53，压缩掉 %d）" % (heads_total, len(names) - heads_total))

    json.dump({"heads": [b for l in byline for b, k in byline[l] if b not in dominated],
               "dominated": sorted(dominated),
               "byline": {l: [b for b, _ in v] for l, v in byline.items()}},
              open(os.path.join(OUT, "_dominance.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
