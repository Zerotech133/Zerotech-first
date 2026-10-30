# -*- coding: utf-8 -*-
"""jev_prefilter 规则单测（K-ORG-20261026-JEV2STAGE）。

原则：纯函数、零 IO、零 LLM——夜间/白天都能跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

NC = Path(__file__).resolve().parents[2] / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import jev_prefilter as pf  # noqa: E402


def test_directional_keeps():
    """命中方向性词 → 必须 keep（宁可多判）。"""
    for title in (
        "某公司发布业绩预增公告，预计净利增长 50%",
        "控股股东拟减持不超过 2% 股份",
        "公司收到证监会立案告知书",
        "央行宣布降准 0.5 个百分点",
        "公司中标 12 亿元重大项目合同",
        "产品涨价 10%，公司毛利率有望提升",
    ):
        d = pf.decide(title)
        assert d["decision"] == "keep", (title, d)
        assert d["matched"], title


def test_routine_announcements_skip():
    """例行公告/治理类 → skip（真值大概率 neutral）。"""
    for title in (
        "关于召开 2026 年第二次临时股东大会的通知",
        "第六届董事会第十次会议决议公告",
        "独立董事关于公司关联交易的专项意见",
        "关于修订公司章程的公告",
        "2026 年第三季度报告披露提示性公告",
    ):
        d = pf.decide(title)
        assert d["decision"] == "skip", (title, d)
        assert d["bias_hint"] == "neutral"


def test_market_wrap_skip():
    """行情播报/盘面综述 → skip。"""
    for title in (
        "收盘：沪指涨 0.8% 报 3120 点，成交额 8500 亿元",
        "美股三大指数集体收涨，纳指涨 1.2%",
        "国际油价小幅下跌，WTI 原油期货报 78.5 美元",
    ):
        d = pf.decide(title)
        assert d["decision"] == "skip", (title, d)


def test_ambiguous_defaults_to_keep():
    """既无方向词也不命中例行模式 → 保守保留。"""
    for title in (
        "某上市公司发布关于控股子公司的说明",
        "行业观察：新能源车渗透率持续提升",
    ):
        d = pf.decide(title)
        assert d["decision"] == "keep", (title, d)
        assert "保守" in d["reason"]


def test_empty_title_skips():
    d = pf.decide("")
    assert d["decision"] == "skip"
    assert d["reason"] == "空标题"


def test_prefilter_jev_payload_shape():
    """skip 时返回可落库 JSON（与 news_ai 本地结构同构 + pref 证据）。"""
    j = pf.prefilter_jev("关于召开股东大会的通知", "announcement")
    assert j is not None
    for key in ("bias", "bias_cn", "conf", "scope", "event_type", "src", "pref", "reason", "meter"):
        assert key in j, key
    assert j["bias"] == "neutral"
    assert j["src"] == "prefilter"
    assert j["pref"]["matched"]
    assert j["meter"]["src"] == "prefilter"


def test_prefilter_jev_returns_none_for_keep():
    assert pf.prefilter_jev("公司业绩预增 50%", "announcement") is None

# ── 2026-10-30 BATCH4：误杀回归 + 回退判定 ─────────────────────────────
def test_audited_false_kills_now_keep():
    for title in (
        "中金公司A股放量跌停！换股股东“浮亏26%割肉”系误读",
        "开盘涨停！000678，6连板！",
        "据报道，美国宽带服务商 Brightspeed 最早可能在12月申请破产",
        "上海拟立法推动自动驾驶进出租车：智能驾驶概念股异动 大众交通盘中涨停后开板",
        "美国10年期国债收益率下行8.5个基点",
    ):
        d = pf.decide(title)
        assert d["decision"] == "keep", (title, d)


def test_routine_broadcast_still_skips():
    for title in (
        "收盘：沪指涨 0.8% 报 3120 点，成交额 8500 亿元",
        "美三大指数集体收涨，纳指涨 1.2%",
    ):
        assert pf.decide(title)["decision"] == "skip", title


def test_stock_code_guard_keeps():
    # 含 6 位个股代码 → 不接受例行 skip（防误杀个股事件）
    d = pf.decide("午盘播报：600519 涨 1.2%，白酒板块走强")
    assert d["decision"] == "keep"


def test_is_prefilter_judgment():
    assert pf.is_prefilter_judgment('{"src": "prefilter", "bias": "neutral"}') is True
    assert pf.is_prefilter_judgment('{"src": "local", "bias": "bull"}') is False
    assert pf.is_prefilter_judgment("not-json") is False
    assert pf.is_prefilter_judgment("") is False


# ── 2026-10-30 PREFILTEREVAL：SAFE 模式（只 skip routine 治理模板）──────
def test_safe_mode_skips_routine_governance():
    for title in (
        "第六届董事会第十次会议决议公告",
        "独立董事候选人声明",
        "关于修订公司章程的公告",
        "关于召开 2026 年第二次临时股东大会的通知",
        "2026 年第三季度报告披露提示性公告",
    ):
        d = pf.decide(title, mode="safe")
        assert d["decision"] == "skip", (title, d)


def test_safe_mode_keeps_market_and_directional():
    for title in (
        "收盘：沪指涨 0.8% 报 3120 点，成交额 8500 亿元",
        "开盘涨停！000678，6连板！",
        "中金公司A股放量跌停！",
        "据报道，某宽带服务商申请破产",
    ):
        d = pf.decide(title, mode="safe")
        assert d["decision"] == "keep", (title, d)


def test_aggressive_vs_safe_skip_volume():
    t = "收盘：沪指涨 0.8% 报 3120 点，成交额 8500 亿元"
    assert pf.decide(t, mode="aggressive")["decision"] == "skip"
    assert pf.decide(t, mode="safe")["decision"] == "keep"
