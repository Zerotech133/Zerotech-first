# -*- coding: utf-8 -*-
"""扩展池信号按年拆开：1买/2买/3买 的收益是稳定的，还是被某几年撑起来的。

动机：`chanlun_exit_policy_eval` 全样本 1买 raw +1.51%（t=9.18）、3买 −1.08%
（t=−8.50），但产线 pass 的 829 笔整体 −2.94%。两者差 4pp，只可能是
样本期或过滤口径的差别。必须拆开，否则会得出「信号有效」或「信号无效」
两个相反的错误结论。
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")


def stat(v, a):
    v = np.asarray(v, float)
    a = np.asarray(a, float)
    if len(v) < 5:
        return None
    tv = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) else 0
    ta = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if a.std(ddof=1) else 0
    return (len(v), v.mean() * 100, tv, a.mean() * 100, ta)


def main():
    rows = []
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("status") != "closed" or r.get("ret") is None:
                continue
            ed = str(r.get("entry_date") or "")[:7]
            if len(ed) < 7:
                continue
            rows.append({"m": ed, "y": ed[:4], "t": r["type"],
                         "ret": float(r["ret"]),
                         "alpha": float(r.get("alpha") or 0.0)})
    print("closed 信号 %d 条，%s ~ %s\n"
          % (len(rows), min(r["m"] for r in rows), max(r["m"] for r in rows)))

    print("=" * 84)
    print("[1] 按年 × 类型（ret = 持有20日收益，已扣成本；alpha = 扣 beta）")
    print("=" * 84)
    print("%-6s %-6s %6s %9s %7s %9s %7s"
          % ("年", "类型", "笔数", "ret均", "t", "alpha均", "t"))
    for y in sorted({r["y"] for r in rows}):
        for t in ("1买", "2买", "3买"):
            v = [r["ret"] for r in rows if r["y"] == y and r["t"] == t]
            a = [r["alpha"] for r in rows if r["y"] == y and r["t"] == t]
            s = stat(v, a)
            if not s:
                continue
            print("%-6s %-6s %6d %8.2f%% %7.2f %8.2f%% %7.2f"
                  % (y, t, s[0], s[1], s[2], s[3], s[4]))
        print()

    print("=" * 84)
    print("[2] 只留 2026 年（产线 pass 的集中期），对比全样本")
    print("=" * 84)
    for scope, lab in ((rows, "全样本"), ([r for r in rows if r["y"] == "2026"], "2026年")):
        print("  %s：" % lab)
        for t in ("1买", "2买", "3买"):
            v = [r["ret"] for r in scope if r["t"] == t]
            a = [r["alpha"] for r in scope if r["t"] == t]
            s = stat(v, a)
            if s:
                print("    %-4s n=%5d ret %+6.2f%% t=%5.2f | alpha %+6.2f%% t=%5.2f"
                      % (t, s[0], s[1], s[2], s[3], s[4]))

    print("\n" + "=" * 84)
    print("[3] 2026 年按月（看是不是某几个月拖累）")
    print("=" * 84)
    print("%-8s" % "月份", end="")
    types = ("1买", "2买", "3买")
    for t in types:
        print("%20s" % t, end="")
    print()
    for m in sorted({r["m"] for r in rows if r["y"] == "2026"}):
        print("%-8s" % m, end="")
        for t in types:
            v = [r["ret"] for r in rows if r["m"] == m and r["t"] == t]
            if len(v) >= 5:
                print("%14s" % ("%+.2f%%(n=%d)" % (np.mean(v) * 100, len(v))), end="")
            else:
                print("%14s" % "-", end="")
        print()


if __name__ == "__main__":
    main()
