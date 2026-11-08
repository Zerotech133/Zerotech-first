# -*- coding: utf-8 -*-
"""成本口径自检（第十八轮）：Q1~Q10。

自检要防的不是「写错公式」，是**静默出一个错的数**：
成本少扣 5bp 不会报错，只会让每个结论都好一点点。所以每条断言都
对准一个具体的、曾经真的发生过的错法。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import hf_cost as HC      # noqa: E402

PORTAL = os.path.join(ROOT, "portal")
RES = []


def ok(tag, cond, msg):
    RES.append((tag, bool(cond), msg))
    print("%s %-4s %s" % ("✅" if cond else "❌", tag, msg))


def main():
    G = json.load(open(os.path.join(PORTAL, "hf_cost_gauge.json"), encoding="utf-8"))

    # ---- Q1 刻度自洽：total = comm + 2×slip，且与 hf_phase5 校准表对齐 ----
    bad = []
    for L in HC.LEVELS:
        want = L["comm_bp"] + 2 * L["slip_bp"]
        got = HC.total_bp(L["comm_bp"], L["slip_bp"])
        if abs(got - want) > 1e-9:
            bad.append((L["k"], want, got))
    ok("Q1", not bad, "四档刻度 total = 佣金 + 2×单边滑点 %s" % (bad or "全对"))

    # ---- Q2 S3 档必须等于 hf_phase5 校准表的第 3 档 50.2bp ----
    s3 = HC.total_bp(20.0, 15.1)
    ok("Q2", abs(s3 - 50.2) < 1e-6,
       "S3 档 = %.1fbp，与 hf_phase5 校准表第 3 档 50.2bp 一致" % s3)

    # ---- Q3 默认参数必须逐位复现 shadow_book 的旧常量 ----
    # 这是「默认档不改任何一个已发布净值」的唯一保证。
    b, s = HC.rates(None, 0.0)
    ok("Q3", b == 0.00075 and s == 0.00125,
       "默认费率 (%.5f, %.5f) == shadow_book 旧常量 (0.00075, 0.00125)" % (b, s))

    # ---- Q4 零成本必须恒等（否则净口径从第一步就偏） ----
    same = all(abs(HC.net_ret(g, 0.0, 0.0) - g) < 1e-15
               for g in (-0.5, -0.05, 0.0, 0.0123, 0.8))
    ok("Q4", same, "零成本下 net_ret(g) ≡ g（-50%~+80% 五点）")

    # ---- Q5 成本越高净收益越低，且 122bp 的量级要对 ----
    n0 = HC.net_ret(0.10, 20, 0)
    n1 = HC.net_ret(0.10, 20, 51)
    ok("Q5", n0 > n1 and abs((n0 - n1) * 1e4 - 102) < 12,
       "+10%% 下 122bp 比 20bp 少 %.1fbp（应 ≈102bp）" % ((n0 - n1) * 1e4))

    # ---- Q6 影子盘：只佣金档必须复现 canonical ----
    c1 = G["shadow"]["check_comm_matches_canonical"]
    ok("Q6", c1.get("ok") is True,
       "重建「只佣金」档逐位复现 shadow_state_v3.json：%s"
       % ", ".join("%s %+0.2f%%" % (d["book"], d["rebuilt"]) for d in c1["detail"]))

    # ---- Q7 trade_book：零成本档必须复现 alpha_buy ----
    c2 = G["trade"]["check_none_matches_trade_book"]
    ok("Q7", c2.get("ok") is True,
       "重建「零成本」档复现 trade_book.alpha_buy（%.3f%% vs %.3f%%）"
       % (c2.get("rebuilt") or 0, c2.get("canonical") or 0))

    # ---- Q8 影子盘净值随成本单调不增（每个 book 都要） ----
    viol = []
    for bk in G["shadow"]["books"]:
        tot = [r["total"] for r in bk["rows"]]
        for i in range(1, len(tot)):
            if tot[i] > tot[i - 1] + 1e-9:
                viol.append((bk["k"], i, tot[i - 1], tot[i]))
    ok("Q8", not viol, "三本书的累计净值随成本单调不增 %s" % (viol or ""))

    # ---- Q9 trade 净 α 单调递减，且降幅与 bp 匹配 ----
    rows = G["trade"]["rows"]
    mono = all(rows[i]["alpha_mean"] < rows[i - 1]["alpha_mean"]
               for i in range(1, len(rows)))
    d0 = rows[0]["alpha_mean"]
    dn = rows[-1]["alpha_mean"]
    ok("Q9", mono and abs((d0 - dn) * 100 - 122) < 15,
       "笔均 α 单调递减 %+.3f%%→%+.3f%%，降 %.0fbp（应 ≈122bp）"
       % (d0, dn, (d0 - dn) * 100))

    # ---- Q10 换手与年化成本必须自洽 ----
    bad = []
    for bk in G["shadow"]["books"]:
        for r in bk["rows"]:
            want = r["turnover_yr"] * r["total_bp"] / 1e4 * 100
            if abs(want - r["ann_cost_pp"]) > 0.02:
                bad.append((bk["k"], r["level"], round(want, 2), r["ann_cost_pp"]))
    ok("Q10", not bad, "年化成本 = 换手 × 双边bp %s" % (bad or "全对"))

    n_ok = sum(1 for _, p, _ in RES if p)
    print("\n%d/%d 通过" % (n_ok, len(RES)))
    return 0 if n_ok == len(RES) else 1


if __name__ == "__main__":
    sys.exit(main())
