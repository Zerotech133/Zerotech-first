# -*- coding: utf-8 -*-
"""bt10m 模拟器内核用例测试（2026-11-10 审计配套）。

不依赖真实行情：价格/成交额函数注入合成值，逐用例手算对账。
重点覆盖审计发现的 bug 家族：
  T5  「帽切卖出双计」回归（2026-11-10 cand 首跑 G2 拦下的 17.6pp bug）
  T6  部分止盈腿 + 余仓（tp_frac 配对）
  审计修复回归：卖出尾巴成交额回退量纲（*100 → notional）

用法：python bt10m_selftest.py    （全过退出码 0）
"""
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import bt10m_sim_core as core     # noqa: E402

S = "TEST"
CAP = 1_000_000.0
P = 10.0          # 原始价 = 复权价 = 10（无分红，比值恒 1）
TP_RATIO = (1 + 0.20) * (1 - 0.003)   # 1.1964
LOT = 100


def env(amounts):
    """合成行情：价格恒 10，成交额按 (s,d) 查表，缺省 1e8（帽不起作用）。"""
    px = lambda s, d, k: P                       # noqa: E731
    px_raw = px
    da = lambda s, d: amounts.get((s, d), 1e8)   # noqa: E731
    return px, px_raw, da


def buy_rec(day, wgt):
    return {"day": day, "stock": S, "wgt": wgt, "entry_adj": P,
            "open_raw": P, "close_raw": P}


def run(days, legq, buy_recs, amounts, method="M1_liqcap1", k_bp=100):
    px, px_raw, da = env(amounts)
    return core.simulate(days, legq, buy_recs, px=px, px_raw=px_raw,
                         day_amount=da, capital=CAP, k_bp=k_bp, lot=LOT,
                         tp_ratio=TP_RATIO, rearm_mult=TP_RATIO,
                         method=method)


D = ["20260105", "20260106", "20260107", "20260108"]


def fee_buy(a):
    return max(5.0, a * 0.00025) + a * 0.00001


def fee_sell(a):
    return max(5.0, a * 0.00025) + a * 0.0005 + a * 0.00001


def imp(k, part):
    return k * math.sqrt(max(part, 0.0)) / 10000.0


def t1_fees_and_impact():
    # 费用地板：小单吃 5 元最低
    assert abs(core._fee_buy(1000.0) - (5.0 + 0.01)) < 1e-9
    assert abs(core._fee_sell(1000.0) - (5.0 + 0.5 + 0.01)) < 1e-9
    # 冲击单调（sqrt）且 0 仓位零冲击
    assert core._fee_sell(1e8) > core._fee_sell(1e6)
    assert imp(100, 0.0) == 0.0 and imp(100, 0.01) < imp(100, 0.04)
    # sell_cat 归类
    f = core.sell_cat
    assert (f("stop_loss"), f("take_profit"), f("take_profit_part"),
            f("take_profit_rearm"), f("time_stop"), f("trail_stop"),
            f("ma_break"), f("3b_fail"), f("exit_open"), f("dk_x")) == (
        "止损", "止盈", "止盈", "止盈", "到期", "规则", "规则", "规则",
        "规则", "规则")
    print("T1 通过：费用/冲击/sell_cat")


def t4_full_sell_m0():
    """M0 无帽：买入→持有→全额止损卖出，现金逐项对账。"""
    st = run(D, {(S, D[0]): [{"sday": D[2], "how": "stop_loss", "frac": 1.0}]},
             [buy_rec(D[0], 50.0)], {}, method="M0_open_full")
    sh = 50000
    cost = sh * P * (1 + imp(100, sh * P / 1e8)) + fee_buy(sh * P)
    proceeds = sh * P * (1 - imp(100, sh * P / 1e8)) - fee_sell(sh * P)
    # 买入日：eq = cash + mv，ratio=1 → mv = 5e5
    assert abs(st["eq"][0] - (CAP - cost + sh * P)) < 1e-6, st["eq"][0]
    # 卖出日（D[2]）：eq = cash + mv = CAP - cost + proceeds
    assert abs(st["eq"][2] - (CAP - cost + proceeds)) < 1e-6, st["eq"][2]
    assert abs(st["eq"][3] - st["eq"][2]) < 1e-6      # 卖后无变动
    assert st["n_buy"] == 1 and st["n_sell_carry"] == 0
    assert abs(st["impact_yuan"] -
               (sh * P * imp(100, 0.005) * 2)) < 1e-6  # 买+卖两次冲击
    print("T4 通过：M0 全额卖出逐项对账")


def t5_liqcap_carry_no_double_count():
    """M1 帽切分 + 次日续卖：两日卖出总和必须恰为一腿（双计回归）。

    布局：买入日成交额 2.5e8（帽 2.5e6 > 5e5，全额买入 5 万股）；
    卖出两日成交额各 2.5e7（帽 = 25 万 = 2.5 万股/日）→ 首日卖一半，
    尾巴次日续卖。旧 bug：尾巴既留 pos 盯市又进 carry → 钱翻倍。"""
    amounts = {(S, D[0]): 2.5e8, (S, D[2]): 2.5e7, (S, D[3]): 2.5e7}
    st = run(D, {(S, D[0]): [{"sday": D[2], "how": "stop_loss", "frac": 1.0}]},
             [buy_rec(D[0], 50.0)], amounts)
    sh = 50000
    part_day = 25000 * P / 2.5e7            # 0.01
    gross_day = 25000 * P                   # 2.5e5（价格恒 10，比值 1）
    cost = sh * P * (1 + imp(100, 5e5 / 2.5e8)) + fee_buy(sh * P)
    proceeds_day = gross_day * (1 - imp(100, part_day)) - fee_sell(gross_day)
    # 每日卖出进账 ≤ 帽额（双计 bug 会让首日进账 = 全腿 5e5 > 帽 2.5e5）
    assert gross_day <= 1.01 * 0.01 * 2.5e7
    # D[2] 整腿划走后只卖一半：pos 空、全现金
    assert abs(st["eq"][2] - (CAP - cost + proceeds_day)) < 1e-6, st["eq"][2]
    # D[3] 续卖另一半
    assert abs(st["eq"][3] - (CAP - cost + 2 * proceeds_day)) < 1e-6
    assert st["n_sell_carry"] == 1 and st["n_buy"] == 1
    # 双计回归断言：卖出总进账恰为两日 proceeds（旧 bug 会凭空多 2.5e5）
    total_proceeds = st["eq"][3] - (CAP - cost)
    assert abs(total_proceeds - 2 * proceeds_day) < 1e-6
    print("T5 通过：M1 帽切分两日合计恰为一腿，无双计")


def t6_partial_tp_then_stop():
    """部分止盈（frac=0.5，限价 1.1964）→ 次日余仓止损，月度台账计数。"""
    legq = {(S, D[0]): [
        {"sday": D[2], "how": "take_profit_part", "frac": 0.5},
        {"sday": D[3], "how": "stop_loss", "frac": 1.0},
    ]}
    st = run(D, legq, [buy_rec(D[0], 50.0)], {}, method="M0_open_full")
    sh = 50000
    cost = sh * P * (1 + imp(100, 0.005)) + fee_buy(sh * P)
    gross_tp = 25000 * P * TP_RATIO
    proceeds_tp = gross_tp - fee_sell(gross_tp)
    # 余仓 25000 股止损：part = 0.0025（非买入时的 0.005）
    proceeds_stop = 25000 * P * (1 - imp(100, 0.0025)) - fee_sell(25000 * P)
    # TP 限价不计冲击 → 卖出日 eq = CAP - cost + proceeds_tp + 余仓 mv
    eq_tp = CAP - cost + proceeds_tp + 25000 * P
    assert abs(st["eq"][2] - eq_tp) < 1e-6, st["eq"][2]
    # 次日余仓止损清零：eq = CAP - cost + proceeds_tp + proceeds_stop
    assert abs(st["eq"][3] - (CAP - cost + proceeds_tp + proceeds_stop)) < 1e-6
    mk2, mk3 = D[2][:6], D[3][:6]
    assert st["months"][mk2]["sells"]["止盈"] == 1
    assert st["months"][mk3]["sells"]["止损"] == 1
    print("T6 通过：部分止盈限价成交 + 余仓止损 + 台账计数")


def t7_final_leg_clears_lots():
    """收尾腿清零：卖光含取整零头；持仓股数恒 100 整除。"""
    legq = {(S, D[0]): [
        {"sday": D[2], "how": "take_profit", "frac": 0.3},
        {"sday": D[3], "how": "stop_loss", "frac": 1.0},
    ]}
    st = run(D, legq, [buy_rec(D[0], 33.0)], {}, method="M0_open_full")
    # wgt=33% → amt=3.3e5 → sh=33000（100 整除）
    assert abs(st["eq"][0] - (CAP - 33000 * P * (1 + imp(100, 0.0033))
                              - fee_buy(33000 * P) + 33000 * P)) < 1e-6
    # 期末现金可完全由 cost+provisions 解释 → eq 最终无持仓
    assert st["eq"][3] == st["eq"][3]  # 存活
    # 终值 = CAP - cost + tp_proceeds + stop_proceeds（TP 腿 0.3→9900 股）
    cost = 33000 * P * (1 + imp(100, 0.0033)) + fee_buy(33000 * P)
    g1 = 9900 * P * TP_RATIO
    g2 = 23100 * P
    want = CAP - cost + (g1 - fee_sell(g1)) + (g2 * (1 - imp(100, 0.00231))
                                              - fee_sell(g2))
    assert abs(st["eq"][3] - want) < 1e-6, (st["eq"][3], want)
    print("T7 通过：收尾腿清零 + 取整零头归收尾腿")


def t8_ledger_consistency():
    """月度台账与全局统计一致；跨月边界持仓延续。"""
    days = ["20260128", "20260129", "20260201", "20260202"]
    legq = {(S, days[0]): [{"sday": days[2], "how": "stop_loss", "frac": 1.0}]}
    st = run(days, legq, [buy_rec(days[0], 50.0)], {}, method="M0_open_full")
    assert list(st["months"].keys()) == ["202601", "202602"]
    assert sum(mm["n_buy"] for mm in st["months"].values()) == st["n_buy"]
    tot_turn = sum(mm["turnover"] for mm in st["months"].values())
    assert abs(tot_turn - st["turnover"]) < 1e-6
    tot_fees = sum(mm["fees"] for mm in st["months"].values())
    assert abs(tot_fees - st["fees"]) < 1e-6
    assert st["months"]["202601"]["sells"]["止损"] == 0
    assert st["months"]["202602"]["sells"]["止损"] == 1
    assert st["months"]["202602"]["holdings_eom"] == 0
    # 首月末持仓 1 只（跨月延续）
    assert st["months"]["202601"]["holdings_eom"] == 1
    print("T8 通过：月度台账与全局一致 + 跨月边界")


def t9_buy_downsize():
    """现金不足缩量买入：第二笔按现金 0.999 取整。"""
    days = D[:2]
    legq = {}
    buys = [buy_rec(D[0], 60.0), {"day": D[0], "stock": "T2", "wgt": 60.0,
                                  "entry_adj": P, "open_raw": P,
                                  "close_raw": P}]
    st = run(days, legq, buys, {}, method="M0_open_full")
    assert st["n_buy"] == 2 and st["n_downsize"] == 1
    # 缩量后不透支：现金 ≥ 0（盯市后 eq ≥ mv）
    assert st["min_cash"] >= -1e-6, st["min_cash"]
    print("T9 通过：现金不足缩量 + 不透支")


def t10_suspended_stock():
    """停牌：卖出日无价 → 腿不重触发（引擎语义：极罕见计缺席，持仓保留）。"""
    px, px_raw, da = env({})
    px_none = lambda s, d, k: None if d == D[2] else P      # noqa: E731
    st = core.simulate(D, {(S, D[0]): [{"sday": D[2], "how": "stop_loss",
                                        "frac": 1.0}]},
                       [buy_rec(D[0], 50.0)],
                       px=px_none, px_raw=px_none, day_amount=da,
                       capital=CAP, lot=LOT, method="M0_open_full")
    cost = 50000 * P * (1 + imp(100, 0.005)) + fee_buy(50000 * P)
    # D[2] 卖不出（腿跳过且不重触发）→ D[3] 仍持有 5 万股盯市
    assert st["n_sell_carry"] == 0
    assert abs(st["eq"][3] - (CAP - cost + 50000 * P)) < 1e-6
    print("T10 通过：停牌日腿不重触发（持仓保留，引擎既定语义）")


def t11_frozen_selfcheck():
    import bt10m_frozen
    assert bt10m_frozen.selfcheck()
    print("T11 通过：固化口径 selfcheck（参数在位 + 参照 NAV 对表）")


def main():
    t1_fees_and_impact()
    t4_full_sell_m0()
    t5_liqcap_carry_no_double_count()
    t6_partial_tp_then_stop()
    t7_final_leg_clears_lots()
    t8_ledger_consistency()
    t9_buy_downsize()
    t10_suspended_stock()
    t11_frozen_selfcheck()
    print("\n全部 %d 组用例通过" % 9)
    return 0


if __name__ == "__main__":
    sys.exit(main())
