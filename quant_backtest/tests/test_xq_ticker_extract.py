# -*- coding: utf-8 -*-
"""标的提取器 v1.2 契约测试（A 股代码提取升级，2026-10-18 老田拍板）。

三个核心修复的回归锚：
  ① 中文紧贴不再漏提（v1.1 的 \\b 病根：中文与数字同为 \\w 无词边界）
  ② 6 位码段白名单（232020 等 23 开头噪音段 52 行实证污染出局）
  ③ 金额语义排除（「150000 元」不收）
跑法：pytest tests/test_xq_ticker_extract.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xq_classify import extract_tickers, _TICKER_VERSION


def test_version_is_v12():
    assert _TICKER_VERSION == "v1.2"


# ── ① 中文紧贴挖掘（v1.1 漏提病根） ──────────────────────────

def test_chinese_adjacent_code():
    assert extract_tickers("贵州茅台600519，反弹目标位", "") == ["600519"]

def test_chinese_adjacent_code_desc():
    assert extract_tickers("", "宁德时代300750的产能利用率创新高") == ["300750"]

def test_sh_prefix_untouched():
    assert extract_tickers("SH600519 突破年线", "") == ["600519"]

def test_dot_suffix_format():
    assert extract_tickers("600519.SH 尾盘拉升", "") == ["600519"]


# ── ② 6 位码段白名单 ─────────────────────────────────────────

def test_noise_232020_rejected():
    assert extract_tickers("活动编号 232020 已登记", "") == []

def test_year_month_rejected():
    assert extract_tickers("数据截至 202410 更新", "") == []

def test_random_six_digits_rejected():
    assert extract_tickers("订单号 999888 已发货", "") == []

def test_all_major_segments_accepted():
    for code in ("600519", "601318", "603288", "688981",      # 沪
                 "000001", "002594", "300750",                 # 深/创业
                 "159915", "510300"):                          # ETF
        assert extract_tickers("代码 %s 走强" % code, "") == [code], code


# ── ③ 金额语义排除 ───────────────────────────────────────────

def test_amount_yuan_rejected():
    assert extract_tickers("需要资金 150000 元起步", "") == []

def test_amount_kuai_rejected():
    assert extract_tickers("亏了150000块", "") == []


# ── 保护性回归（既有行为不破坏） ─────────────────────────────

def test_hk_5digit_kept():
    assert extract_tickers("腾讯00700 业绩超预期", "") == ["00700"]

def test_decimal_protection():
    # 小数点后的 6 位不误提（净值/百分比防截取）
    assert extract_tickers("净值1.600519 保持", "") == []

def test_multi_codes_dedup_order():
    assert extract_tickers("600519 和 300750 谁强？我选600519", "") == ["600519", "300750"]

def test_fund_word_kept():
    assert extract_tickers("红利ETF 值得配置吗", "") == ["红利ETF"]

def test_html_noise_rejected():
    assert extract_tickers("债券B<br/>1、没有标的", "") == []
