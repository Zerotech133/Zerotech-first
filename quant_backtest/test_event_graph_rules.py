# -*- coding: utf-8 -*-
"""K270-P1-2 测试网补建：event_graph RULES 词表黄金用例。

背景：2026-10-08 工作区改动删了三个误判词（「向下修正/问询函/摘牌」），
铁律「宁 None 不错判」——但 RULES 变更无任何测试保护。本文件固化为
黄金用例：误判词必须返回 (None, None)；正例仍命中；复合词优先级。
"""
import event_graph as eg


# ── 误判词守卫（2026-10-08 删除的三词不得再命中）──────────────

def test_xiuzheng_no_longer_bear():
    """「向下修正」已删：转债转股价下修是中性流程，不是业绩预亏。"""
    assert eg._rule_judge("关于向下修正可转债转股价格的公告") == (None, None)


def test_inquiry_letter_no_longer_bear():
    """「问询函」已删：审核问询回复系正常流程；真处罚有立案/处罚等词。"""
    assert eg._rule_judge("关于对年报问询函的回复") == (None, None)
    # 真处罚仍命中（监管处罚组其余词完好）
    assert eg._rule_judge("公司收到中国证监会立案告知书") == ("bear", "监管处罚")
    assert eg._rule_judge("公司收到行政监管措施决定书") == ("bear", "监管处罚")


def test_zhaipai_no_longer_bear():
    """「摘牌」已删：债券兑付摘牌是正常兑付；真退市有退市/终止上市。"""
    assert eg._rule_judge("可转债兑付摘牌公告") == (None, None)
    assert eg._rule_judge("公司股票进入退市整理期") == ("bear", "退市风险")
    assert eg._rule_judge("公司股票终止上市") == ("bear", "退市风险")


# ── 正例仍命中（各组代表词）─────────────────────────────────

def test_bear_still_hits():
    assert eg._rule_judge("公司预计前三季度业绩预亏") == ("bear", "业绩预亏")
    assert eg._rule_judge("关于终止收购重大资产的公告") == ("bear", "终止重组")
    assert eg._rule_judge("控股股东部分股份被司法冻结") == ("bear", "诉讼冻结")
    assert eg._rule_judge("股东计划减持不超过2%") == ("bear", "股东减持")


def test_bull_still_hits():
    assert eg._rule_judge("公司预计年度业绩预增") == ("bull", "业绩预增")
    assert eg._rule_judge("公司中标重大项目") == ("bull", "中标合同")
    assert eg._rule_judge("公司回购股份方案公告") == ("bull", "回购增持")
    assert eg._rule_judge("公司获得政府补助") == ("bull", "政府补助")


def test_compound_word_priority():
    """RULES 顺序=优先级：「终止收购」先中终止重组(bear)，不落入 bull 收购。"""
    bias, tag = eg._rule_judge("公司终止收购某标的资产")
    assert (bias, tag) == ("bear", "终止重组")


def test_never_misjudge_on_none():
    """中性标题零命中——宁 None 不错判。"""
    assert eg._rule_judge("关于召开年度股东大会的通知") == (None, None)
    assert eg._rule_judge("2026年第三季度报告") == (None, None)
    assert eg._rule_judge("") == (None, None)
