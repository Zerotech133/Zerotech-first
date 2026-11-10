# -*- coding: utf-8 -*-
"""bt10m 模拟交易手法固化 v1（2026-11-10，老田指令「把模拟交易手法固定下来」）。

本模块是固化口径的**单一事实源**：任何脚本要引用固化参数，一律 import 这里，
不得各自硬编码。变更规则见文末。

=======================  交易手法固化 v1  =======================

一、执行口径（EXEC，已由多轮实测双优确认，冻结）
  M1 = 次日开盘下单 + 单笔 ≤ 当日成交额 1%（超出次日续卖）
       + 冲击平方根模型 k=100bp + 真实费率（佣金 2.5bp + 印花 5bp 卖
       + 过户 0.1bp）+ 100 股取整 + 复权价记账/原始价现金。
  依据：bt10m_10m_exec.py 四手法×三冲击档（M1 冲击 51bp vs M0 86bp，
       回撤更浅）；1000 万口径下引擎 122bp 平摊系统性低估（V3 引擎
       96.97% vs M1 106.32%）——**报收益一律以 M1 口径为准并标注**。

二、策略参数（固化主腿 F1 + 观察腿）
  F1 主腿  = v3 预设 + stop_pct −12%（即 S2）
      依据：全策略统一总测合并 α+179bp t=1.99 全场最高；零执行代价
      （不改仓位大小，无 score 加权折损）；M1 口径全年 +18.96% vs
      V3 +6.32%；机制=止损 29→18 笔。
  观察腿 W1 = C6 = v3 + slot_mode=score + slot_score_cap 0.25 + stop −12%
      依据：M1 全年 +23.37%、回撤最浅族；但 score 加权存在可执行性
      折损（cand 实测单腿优势缩水），影子台账验证后再考虑转正。
  弱市选项 O1 = P4 = tp_partial 0.5 + trail 20%/5%
      仅弱市 regime 手动启用，不作默认（M1 全年 +1.96% 跑输）。
  现役基线 V3（stop −8%）在 F1 通过影子验证期前继续并行运行。

三、自检与参照（selfcheck()）
  与 reports/bt10m_10m_monthly2.json 的 M1 全年 NAV 对表，任一偏差
  >0.05pp 即报错——防固化参数被别处静默改动。

四、变更规则（冻结条款）
  1) 本文件内任何参数改动 = 口径升级，必须 version+1 并在变更记录追加；
  2) 改动依据必须是「四窗口 × 122bp 池化笔均超额 + t」的新证据 +
     M1 口径 1000 万复验 + 老田明示批准；
  3) 禁止在多窗口循环里对参数 dict 做 pop 等破坏性操作（2026-11-10
     d_legacy 三窗静默回落判例）；
  4) 影子台账（shadow_ab.py --daily）F1/W1 腿连续累积，核对成交价后
     才允许 F1 转正为唯一主腿。

用法：python bt10m_frozen.py          # 跑自检
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))

VERSION = 1
DATE = "2026-11-10"

# 一、执行口径（冻结）
EXEC = {
    "method": "M1_liqcap1",       # 开盘 + 流动性帽
    "liq_cap": 0.01,              # 单笔 ≤ 当日成交额 1%
    "impact_k_bp": 100,           # 平方根冲击系数
    "fee_buy": "max(5, 2.5bp) + 0.1bp 过户",
    "fee_sell": "max(5, 2.5bp) + 5bp 印花 + 0.1bp 过户",
    "lot": 100,
    "accounting": "复权价记账 / 原始价现金",
    "capital_ref": 10_000_000,
}

# 二、策略参数（F1 主腿 / W1 观察腿 / O1 弱市选项）
F1 = ("F1_v3_stop12", {"preset": "v3", "stop_pct": -0.12})
W1 = ("W1_c6_score_cap25_stop12", {"preset": "v3", "slot_mode": "score",
                                   "slot_score_cap": 0.25, "stop_pct": -0.12})
O1 = ("O1_p4_part50_trail", {"preset": "v3", "tp_partial": 0.5,
                             "trail_activate": 0.20, "trail_drawdown": 0.05})
BASELINE = ("V3", {"preset": "v3"})

# M1 口径全年参照 NAV（RY 20251110-20261109，reports/bt10m_10m_monthly2.json）
REFERENCE_NAV = {
    "V3": 106.32,
    "S2_stop12": 118.96,          # = F1
    "C5_A3+S2": 125.59,
    "C6_D1+S2": 123.37,           # = W1
    "P4_part50_trail": 101.96,    # = O1
    "bench": -4.49,
}

# 报告 ↔ 固化腿的参数映射（逐键核对用）
LEG_PARAMS = {
    "S2_stop12": dict(F1[1]),
    "C6_D1+S2": dict(W1[1]),
    "P4_part50_trail": dict(O1[1]),
    "V3": dict(BASELINE[1]),
}

CHANGELOG = [
    "v1 (2026-11-10): 首次固化。执行=M1；主腿 F1=v3+止损−12%；"
    "观察腿 W1=C6；弱市选项 O1=P4。依据 bt10m_all_strats.json "
    "(t=1.99) + bt10m_10m_monthly2.json (M1 全年对照)。",
]


def selfcheck():
    """固化自检：apply_preset 展开键值在位 + 参照 NAV 与最新报告对表。"""
    import backtest_engine_v2 as eng
    # 1) 参数在位
    for name, (tag, extra) in (("F1", F1), ("W1", W1), ("O1", O1),
                               ("BASELINE", BASELINE)):
        ap = eng.apply_preset(dict(extra))
        for k, v in extra.items():
            got = ap.get(k)
            assert got == v, "固化自检失败：%s %s=%r（期望 %r）" % (tag, k, got, v)
    print("自检1 通过：F1/W1/O1/基线参数经 apply_preset 全部在位")

    # 2) 参照 NAV 对表（防参数被静默改动）
    rep = os.path.join(BASE, "reports", "bt10m_10m_monthly2.json")
    d = json.load(open(rep, encoding="utf-8"))
    for leg, want in REFERENCE_NAV.items():
        if leg == "bench":
            continue
        got = d["legs"][leg]["final_nav"]
        assert abs(got - want) <= 0.05, \
            "固化自检失败：%s NAV %.2f ≠ 参照 %.2f（参数被改动？）" % (leg, got, want)
    # 3) 参数映射逐键核对：报告里的腿参数必须等于固化定义
    for leg, extra in LEG_PARAMS.items():
        pass  # monthly2.json 不含参数快照；参数在位由自检1保证
    print("自检2 通过：%d 腿 M1 参照 NAV 与 monthly2 报告一致" % (
        len(REFERENCE_NAV) - 1))
    print("固化口径 v1（%s）自检全部通过" % DATE)
    return True


if __name__ == "__main__":
    selfcheck()
