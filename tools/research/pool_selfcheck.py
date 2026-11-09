# -*- coding: utf-8 -*-
"""池子构建的回归自检：A1~A5。

为什么需要
----------
`chanlun_pool.py` 的输出是「每只票的生死线 + 分级 + 生命周期」，这三样
任何一样算错，池子就会给出错误指令（比如把生死线算到现价上方，等于让
人一进场就止损）。所以每一条硬规则都要有断言。

早期版本踩过的坑，正是这些断言要拦的：
· zg 只从信号对象取 → 1买/2买 全为空，374 只没有生死线（A1 拦）
· death 未夹在现价下方 3%~15% → 出现 −60% 的「生死线」，毫无意义（A1 拦）
· 「alpha 是否为负」按小数点判 → 1买 的 −0.01% 被判成负，全部降级（A5 拦）
· 生命周期只看涨幅不看信号年龄 → 30 天前的老信号被当「新鲜」（A3 拦）

用法:
    python tools/research/pool_selfcheck.py
    python tools/research/pool_selfcheck.py --pool portal/xxx.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_POOL = os.path.join(ROOT, "portal", "chanlun_pool_20261106.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=DEFAULT_POOL)
    args = ap.parse_args()

    with open(args.pool, encoding="utf-8") as f:
        d = json.load(f)
    pool = d["pool"]
    bad = 0

    def chk(cond, msg):
        nonlocal bad
        print(("  OK   " if cond else "  FAIL ") + msg)
        if not cond:
            bad += 1

    print("池子自检 · %s · %d 只\n" % (os.path.basename(args.pool), len(pool)))

    # A1 生死线必须存在、且在现价下方 3%~15%
    miss = [r for r in pool if not r.get("death_px")]
    chk(len(miss) == 0, "A1a 每只都有 death_px（缺失 %d 只）" % len(miss))
    bad_disp = [r for r in pool
                if r.get("death_disp_pct") is None
                or not (-15.5 <= r["death_disp_pct"] <= -2.5)]
    chk(len(bad_disp) == 0,
        "A1b death 夹在现价下方 3%%~15%%（越界 %d 只%s）"
        % (len(bad_disp),
           ("，如 " + ", ".join("%s %.1f%%" % (r["code"], r["death_disp_pct"])
                               for r in bad_disp[:3])) if bad_disp else ""))
    above = [r for r in pool if r.get("death_px") and r["death_px"] >= r["px"]]
    chk(len(above) == 0, "A1c death 必须在现价下方（上方 %d 只）" % len(above))

    # A2 zone 与 zg/zd 自洽
    inc = []
    for r in pool:
        zg, zd, px, z = r.get("zg"), r.get("zd"), r["px"], r.get("zone")
        if zg is None or zd is None:
            continue
        if z == "above" and not px > zg:
            inc.append(r["code"])
        elif z == "inside" and not (zd < px <= zg):
            inc.append(r["code"])
        elif z == "below" and not px <= zd:
            inc.append(r["code"])
    chk(len(inc) == 0, "A2 zone 与 zg/zd 自洽（不自洽 %d 只）" % len(inc))

    # A3 生命周期与阈值自洽
    th = d.get("thresholds", {})
    fp, ep = th.get("fresh_pct", 8.0), th.get("expired_pct", 20.0)
    fa, ea = th.get("fresh_age", 15), th.get("expired_age", 40)
    bp = th.get("broken_pct", -8.0)
    v3 = []
    for r in pool:
        st, pv, age, brk = (r["stage"], r["px_vs_sig_pct"], r["sig_age"],
                            r.get("break_pct"))
        if st == "broken":
            ok = brk is not None and brk <= bp
        elif st == "expired":
            ok = (brk is None or brk > bp) and (
                pv > ep or (age is not None and age > ea))
        elif st == "running":
            ok = (brk is None or brk > bp) and pv <= ep and (
                age is None or age <= ea) and (pv > fp or (age or 0) > fa)
        else:  # new
            ok = (brk is None or brk > bp) and pv <= fp and age is not None and age <= fa
        if not ok:
            v3.append("%s(%s)" % (r["code"], st))
    chk(len(v3) == 0, "A3 生命周期与阈值自洽（不符 %d 只%s）"
        % (len(v3), ("，如 " + ", ".join(v3[:3])) if v3 else ""))

    # A4 3买 必须全部 avoid
    v4 = [r for r in pool if r["type"] == "3买" and r["grade"] != "avoid"]
    chk(len(v4) == 0, "A4 3买 全部进回避（漏 %d 只）" % len(v4))
    v4b = [r for r in pool if r["stage"] == "broken" and r["grade"] != "avoid"]
    chk(len(v4b) == 0, "A4b 破位全部进回避（漏 %d 只）" % len(v4b))

    # A5 watch 不得含显著负 alpha 的类型
    v5 = [r for r in pool if r["grade"] == "watch"
          and r.get("type_alpha_t") is not None and r["type_alpha_t"] <= -1.0]
    chk(len(v5) == 0, "A5 watch 不含显著负 alpha 类型（违例 %d 只）" % len(v5))
    v5b = [r for r in pool if r["grade"] == "watch" and r.get("zone") == "above"]
    chk(len(v5b) == 0, "A5b watch 不含 above 侧（违例 %d 只）" % len(v5b))

    print("\n%s" % ("全部通过" if bad == 0 else "**失败 %d 项**" % bad))
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
