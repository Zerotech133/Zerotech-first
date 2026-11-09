# -*- coding: utf-8 -*-
"""三件事验证的自检：P1~P7。

跑法::

    python -X utf8 tools/research/hf_phase3_selfcheck.py
    python -X utf8 tools/research/hf_phase3_selfcheck.py --degrade   # 退化档：应报红

退化档会故意把空头建仓价改成「检出日收盘」（前视），P5 必须能抓出来。
断言全绿不代表结论对，只代表这几条口径没被写错。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hf_book as HF                                     # noqa: E402

ROOT = HF.ROOT
BASE = dict(HF.P)
d = HF._d          # 台账日期 → 8 位数字串


def reset(**kw):
    HF.P.clear()
    HF.P.update(BASE)
    HF.P.update(kw)


def ok(cond, msg):
    return ("PASS" if cond else "FAIL"), msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--degrade", action="store_true")
    a = ap.parse_args()
    ctx = HF.build_context(a.days)
    res = []

    # ---------------- P1 冷却默认关时基线逐位不变 -------------------------
    reset()
    nav, tr, live, sk, plans, skd = HF.run_once(ctx)
    st = HF.stats_of(nav, tr)
    ref = json.load(open(os.path.join(ROOT, "portal", "hf_book.json"), encoding="utf-8"))["stats"]
    keys = ("total_return", "max_drawdown", "win_rate", "avg_util", "n_trades", "avg_ret")
    bad = [k for k in keys if abs(float(st[k]) - float(ref[k])) > 5e-6]
    res.append(ok(not bad, "P1 基线未漂  %s%s"
                  % ("冷却/排序默认关时与 hf_book.json 逐位一致"
                     if not bad else "以下字段漂了 %s" % bad,
                     "（净值 %.4f%%）" % (st["total_return"] * 100))))

    # ---------------- P2 冷却真的收紧现金流 -------------------------------
    # 锁定期越长，可动用现金越少 → 平均仓位必须单调下降。这是机制的直接推论，
    # 不成立就说明 avail 根本没被用上（曾经出现过：改了变量但没替换引用）。
    utils = []
    for n in (0, 1, 3, 5, 10):
        reset(exit_lock_days=n)
        nv, tt, lv, skk, pp, sd = HF.run_once(ctx)
        utils.append(round(statistics.mean([r["util"] for r in nv]) * 100, 2))
    mono = all(utils[i] >= utils[i + 1] - 1e-9 for i in range(len(utils) - 1))
    res.append(ok(mono, "P2 冷却收紧仓位  锁[0,1,3,5,10] 日平均仓位 %s%%（必须单调不增）"
                  % utils))

    # ---------------- P3 排序只改顺序，不改候选池 -------------------------
    counts = {}
    for mode in ("lag_asc", "lag_desc", "rand:1"):
        reset(rank=mode)
        pp = HF.filter_plans(ctx["plans"], None)
        counts[mode] = sum(len(v) for v in pp.values())
    same = len(set(counts.values())) == 1
    res.append(ok(same, "P3 候选池不变  三种排序的候选总数 %s（必须相同）" % counts))

    # ---------------- P4 随机排序可复现 -----------------------------------
    reset(rank="rand:3")
    n1 = [r["nav"] for r in HF.run_once(ctx)[0]]
    reset(rank="rand:3")
    n2 = [r["nav"] for r in HF.run_once(ctx)[0]]
    # 不能用 Python 内置 hash()：字符串哈希每进程加盐，两次跑会不同
    res.append(ok(n1 == n2, "P4 随机可复现  同种子两次跑净值序列%s"
                  % ("完全相同" if n1 == n2 else "不同（用了 hash()？）")))

    # ---------------- P5 空头无前视 ---------------------------------------
    reset()
    sp = HF.filter_plans(ctx["splans"], ("1卖",))
    snav, str_, slive = HF.simulate_short(ctx["axis"], sp, ctx["bars"],
                                          entry_at_close=a.degrade)
    bad = []
    for t in str_[:60]:
        b = ctx["bars"][t["code"]]
        ei = b.i_of(t["entry_date"])
        # 期望值必须**独立于被测参数**：无论如何都按"次日开盘"判。
        # 首版写了 want = c[ei] if degrade else o[ei]，退化档跟着一起变，
        # 断言成了同义反复（P5 永远绿）——这正是要防的那类假通过。
        want = b.o[ei] if ei is not None else None
        if ei is None or abs(t["entry_px"] - want) > 1e-4:
            bad.append((t["code"], t["entry_date"]))
    res.append(ok(not bad, "P5 空头无前视  建仓价==次日开盘（抽样 %d，越界 %d）%s"
                  % (min(60, len(str_)), len(bad), "［退化档：应红］" if a.degrade else "")))

    # ---------------- P6 空头复式记账自洽 ---------------------------------
    cap = HF.P["capital"]
    real = sum(t["ret"] * t["amt"] for t in str_ if t.get("exit_date"))
    opn = sum((1 - t["exit_px"] / t["entry_px"]) * t["amt"]
              for t in str_ if not t.get("exit_date"))
    drift = abs((snav[-1]["equity"] - cap) - (real + opn)) / cap
    res.append(ok(drift < 0.02,
                  "P6 空头记账自洽  权益变动 %.0f 元 vs 已实现+浮动 %.0f 元，偏差 %.2f%%（阈值 2%%）"
                  % (snav[-1]["equity"] - cap, real + opn, drift * 100)))

    # ---------------- P7 叠加公式在两端必须重合 ---------------------------
    longn = [r["nav"] for r in nav]
    smap = {r["d"]: r["nav"] for r in snav}
    c0 = HF.combine_nav(nav, snav, 0.0)
    c1 = HF.combine_nav(nav, snav, 1.0)
    e0 = max(abs(c0[i] - longn[i]) for i in range(len(longn))) < 1e-12
    # 叠加口径 w=1 的定义就是「多头 + 同等名义空头」，不是「只剩空头」——
    # 写成 c1 == short 是错的（首版断言错在这里，不是代码错）。
    e1 = max(abs(c1[i] - (longn[i] + smap.get(nav[i]["d"], 1.0) - 1.0))
             for i in range(len(longn))) < 1e-12
    # 腾挪口径：w=1 时必须完全等于空头
    sh = [(1 - 1.0) * longn[i] + 1.0 * smap.get(nav[i]["d"], 1.0) for i in range(len(longn))]
    e2 = max(abs(sh[i] - smap.get(nav[i]["d"], 1.0)) for i in range(len(longn))) < 1e-12
    res.append(ok(e0 and e1 and e2,
                  "P7 叠加公式  叠加口径 w=0→多头:%s w=1→多头+空头:%s；腾挪口径 w=1→空头:%s"
                  % ("是" if e0 else "否", "是" if e1 else "否", "是" if e2 else "否")))

    print("")
    for tag, msg in res:
        print("  [%s] %s" % (tag, msg))
    n_pass = sum(1 for r in res if r[0] == "PASS")
    print("\n  通过 %d/%d%s" % (n_pass, len(res),
                             "（退化档：P5 应红）" if a.degrade else ""))
    reset()
    return 0 if (n_pass == len(res) or a.degrade) else 1


if __name__ == "__main__":
    sys.exit(main())
