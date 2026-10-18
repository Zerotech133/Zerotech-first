# -*- coding: utf-8 -*-
"""xq_index_map 模块契约测试（M-H4 前置件，2026-10-18 纯新增）。

覆盖面：
  ① 映射完整性：条数≥15、代码格式 .SH/.SZ/.BJ/.HK 后缀、别名表可命中
  ② 别名归一：大小写/全角/多余空白不敏感
  ③ extract_index_mentions：多指数混排、去重保序、未知词不误报
  ④ 未知指数 normalize/lookup 返回 None
跑法：pytest quant_backtest/tests/test_xq_index_map.py -q
（纯新增模块单测，不触任何生产链路与生产库。）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_index_map as xim

_VALID_SUFFIXES = (".SH", ".SZ", ".BJ", ".HK")


# ---------------------------------------------------------------------------
# ① 映射完整性
# ---------------------------------------------------------------------------

def test_table_has_at_least_15_entries():
    assert len(xim._INDEX_TABLE) >= 15


def test_table_codes_format():
    for name, code, _aliases, _note in xim._INDEX_TABLE:
        assert code.endswith(_VALID_SUFFIXES), "代码后缀非法: %s -> %s" % (name, code)


def test_table_required_members():
    """任务书点名指数必须在场。"""
    code_of = {name: code for name, code, _, _ in xim._INDEX_TABLE}
    for name, expect in [
        ("沪深300", "000300.SH"),
        ("上证指数", "000001.SH"),
        ("上证50", "000016.SH"),
        ("深证成指", "399001.SZ"),
        ("创业板指", "399006.SZ"),
        ("科创50", "000688.SH"),
        ("中证500", "000905.SH"),
        ("中证1000", "000852.SH"),
        ("北证50", "899050.BJ"),
        ("恒生指数", "HSI.HK"),
        ("恒生科技", "HSTECH.HK"),
        ("国企指数", "HSCEI.HK"),
    ]:
        assert code_of.get(name) == expect, "映射不符: %s 应为 %s" % (name, expect)


def test_table_no_duplicate_codes_or_names():
    names = [t[0] for t in xim._INDEX_TABLE]
    codes = [t[1] for t in xim._INDEX_TABLE]
    assert len(names) == len(set(names))
    assert len(codes) == len(set(codes))


def test_each_entry_has_aliases():
    for name, _code, aliases, _note in xim._INDEX_TABLE:
        assert len(aliases) >= 1, "别名集为空: %s" % name


# ---------------------------------------------------------------------------
# ② 别名归一（大小写/全角/多余空白）
# ---------------------------------------------------------------------------

def test_alias_casing_and_fullwidth():
    assert xim.lookup_index("hs300") == "000300.SH"
    assert xim.lookup_index("HS300") == "000300.SH"
    assert xim.lookup_index("CSI300") == "000300.SH"
    assert xim.lookup_index("ｈｓ３００") == "000300.SH"          # 全角字母数字
    assert xim.lookup_index("沪深　３００") == "000300.SH"        # 全角空格+数字
    assert xim.lookup_index("恒指") == "HSI.HK"
    assert xim.lookup_index("HSTECH") == "HSTECH.HK"


def test_alias_whitespace_tolerant():
    assert xim.lookup_index("沪深 300") == "000300.SH"
    assert xim.lookup_index(" 沪深300指数 ") == "000300.SH"
    assert xim.lookup_index("中证  1000") == "000852.SH"


def test_canonical_name_self_lookup():
    for name, code, _aliases, _note in xim._INDEX_TABLE:
        assert xim.lookup_index(name) == code, "规范名自查失败: %s" % name


def test_normalize_returns_canonical():
    assert xim.normalize_index_name("hs300") == "沪深300"
    assert xim.normalize_index_name("沪深300指数") == "沪深300"
    assert xim.normalize_index_name("ＨＳＩ") == "恒生指数"
    assert xim.normalize_index_name("沪指") == "上证指数"
    assert xim.normalize_index_name("H股") == "国企指数"


# ---------------------------------------------------------------------------
# ③ extract_index_mentions
# ---------------------------------------------------------------------------

def test_extract_single():
    assert xim.extract_index_mentions("今天沪深300跌了") == [("沪深300", "000300.SH")]


def test_extract_mixed_multi_order_preserved():
    text = "恒指收跌，创业板指和沪深300都弱，尾盘北证50翻红"
    got = xim.extract_index_mentions(text)
    assert got == [("恒生指数", "HSI.HK"), ("创业板指", "399006.SZ"),
                   ("沪深300", "000300.SH"), ("北证50", "899050.BJ")]


def test_extract_dedupe_keeps_first_position():
    text = "中证500先提，中证500指数再提一遍，还是中证五百"
    got = xim.extract_index_mentions(text)
    assert got == [("中证500", "000905.SH")]


def test_extract_unknown_words_no_false_positive():
    text = "沪深3000点保卫战？中证x500？上证5070个账户？"
    got = xim.extract_index_mentions(text)
    # 嵌入型噪音（3000/x500/5070）不得命中任何指数；「沪深3000」因区间屏蔽
    # 不允许「沪深300」跨入第4位数字——具体断言：无沪深300命中
    names = [n for n, _ in got]
    assert "沪深300" not in names
    assert "中证500" not in names


def test_extract_empty_and_none_safe():
    assert xim.extract_index_mentions("") == []
    assert xim.extract_index_mentions(None) == []


def test_extract_fullwidth_and_spaces_in_text():
    text = "ＨＳ３００和　恒生科技指数　齐跌"
    got = xim.extract_index_mentions(text)
    assert ("沪深300", "000300.SH") in got
    assert ("恒生科技", "HSTECH.HK") in got


def test_extract_longest_priority_no_inner_recut():
    text = "上证50走势"
    got = xim.extract_index_mentions(text)
    assert got == [("上证50", "000016.SH")]   # 不得被内嵌别名切成两条


# ---------------------------------------------------------------------------
# ④ 未知返回 None
# ---------------------------------------------------------------------------

def test_unknown_returns_none():
    assert xim.lookup_index("茅台指数") is None
    assert xim.lookup_index("道琼斯") is None          # 美股暂缓收录
    assert xim.lookup_index("") is None
    assert xim.lookup_index(None) is None
    assert xim.normalize_index_name("不存在的指数") is None
    assert xim.normalize_index_name("") is None
    assert xim.normalize_index_name(None) is None


def test_bare_hengsheng_not_alias():
    """「恒生」单字面不入表（恒生电子是A股个股），恒生电子相关文本不误报。"""
    assert xim.lookup_index("恒生") is None
    assert xim.extract_index_mentions("恒生电子涨停") == []
