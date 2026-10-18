# -*- coding: utf-8 -*-
"""xq_portrait 聚合逻辑单测：注入假行，不依赖真库（portrait-dev 2026-10-18）。

口径对齐 tools/research/xq_anomaly_calibration.py：异动作用面 =
「帖子日(created_at) × 标的」的 bearish∧带码 计数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from xq_portrait import (  # noqa: E402
    daily_ticker_bearish,
    lineage,
    marginals,
    monthly_trend,
    percentile,
    ticker_concentration,
)


def row(event="个股研判", sentiment="neutral", content=None,
        tickers=(), pv="v1.3", day="2020-05-13", ok=1):
    return {
        "event_type": event, "sentiment": sentiment, "content_type": content,
        "tickers": list(tickers), "prompt_version": pv, "day": day, "parse_ok": ok,
    }


# ---------------------------------------------------------------- percentile

def test_percentile_basic():
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([1, 2, 3, 4, 5], 0) == 1
    assert percentile([1, 2, 3, 4, 5], 100) == 5
    # p90 of 1..10 线性插值：0.9*(10-1)+1 = 9.1
    assert abs(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) - 9.1) < 1e-9
    assert percentile([7], 50) == 7
    assert percentile([], 50) is None


# ---------------------------------------------------------------- marginals

def test_marginals_counts_and_crosstabs():
    rows = [
        row(event="行情观点", sentiment="bearish", content="tech"),
        row(event="行情观点", sentiment="bearish", content="tech"),
        row(event="个股研判", sentiment="bullish", content="fundamental"),
        row(event="个股研判", sentiment="neutral", content=None),
        row(event=None, sentiment="bearish", content="community"),
    ]
    m = marginals(rows)
    assert m["sentiment"] == {"bearish": 3, "bullish": 1, "neutral": 1}
    assert m["event_type"] == {"行情观点": 2, "个股研判": 2, "(空)": 1}
    assert m["content_type"] == {"tech": 2, "fundamental": 1, "community": 1, "(空)": 1}
    et_x_st = m["crosstab_event_sentiment"]
    assert et_x_st["行情观点"]["bearish"] == 2
    assert et_x_st["个股研判"]["bullish"] == 1
    ct_x_st = m["crosstab_content_sentiment"]
    assert ct_x_st["tech"]["bearish"] == 2
    assert ct_x_st["(空)"]["neutral"] == 1
    # 空输入不崩
    empty = marginals([])
    assert empty["sentiment"] == {} and empty["crosstab_event_sentiment"] == {}


# ---------------------------------------------------------------- monthly

def test_monthly_trend_zero_fill_and_bearish_share():
    rows = [
        row(sentiment="bearish", day="2019-01-15"),
        row(sentiment="bullish", day="2019-01-20"),
        row(sentiment="bearish", day="2019-03-02"),
    ]
    t = monthly_trend(rows)
    # 2019-01..03 连续补零
    assert [m["month"] for m in t] == ["2019-01", "2019-02", "2019-03"]
    assert t[0]["n"] == 2 and abs(t[0]["bearish_share"] - 0.5) < 1e-9
    assert t[1]["n"] == 0 and t[1]["bearish_share"] is None
    assert t[2]["n"] == 1 and t[2]["bearish_share"] == 1.0
    assert monthly_trend([]) == []


# ---------------------------------------------------------------- tickers

def test_ticker_concentration_top_and_rate():
    rows = [
        row(sentiment="bearish", tickers=["600519"]),
        row(sentiment="bearish", tickers=["600519", "00700"]),
        row(sentiment="bullish", tickers=["600519"]),
        row(sentiment="neutral", tickers=[]),
        row(sentiment="neutral", tickers=[]),
    ]
    tc = ticker_concentration(rows)
    assert tc["total"] == 5
    assert tc["with_ticker"] == 3
    assert abs(tc["with_ticker_rate"] - 0.6) < 1e-9
    top = tc["top20"]
    assert top[0]["ticker"] == "600519" and top[0]["n"] == 3
    assert top[0]["bearish"] == 2 and top[0]["bullish"] == 1
    assert top[1]["ticker"] == "00700" and top[1]["n"] == 1
    assert tc["distinct_tickers"] == 2


# ---------------------------------------------------------------- lineage

def test_lineage_version_completeness():
    rows = [
        row(pv="v1.2-re", content="tech"),
        row(pv="v1.2-re", content=None),
        row(pv="v1.3", content="news"),
        row(pv="v1.3", content="ad"),
        row(pv="v1.3-rule", content="ad"),
    ]
    lin = lineage(rows)
    assert lin["versions"]["v1.2-re"]["n"] == 2
    assert lin["versions"]["v1.2-re"]["content_type_filled"] == 1
    assert abs(lin["versions"]["v1.2-re"]["content_type_rate"] - 0.5) < 1e-9
    assert lin["versions"]["v1.3"]["content_type_filled"] == 2
    assert lin["versions"]["v1.3-rule"]["content_type_rate"] == 1.0
    # 血缘诚实验证钩子：v1.2-re（补标批次）若 content_type 未全覆盖要被点名
    assert "v1.2-re" in lin["incomplete_versions"]
    assert lin["versions"]["v1.3-rule"] not in lin["incomplete_versions"] or \
        lin["versions"]["v1.3-rule"]["content_type_rate"] == 1.0


# ---------------------------------------------------------------- anomaly

def test_daily_ticker_bearish_distribution():
    rows = [
        # 2020-05-13: 600519 bearish x3（同标的单日 3 条）, 00700 bearish x1
        row(sentiment="bearish", tickers=["600519"], day="2020-05-13"),
        row(sentiment="bearish", tickers=["600519"], day="2020-05-13"),
        row(sentiment="bearish", tickers=["600519"], day="2020-05-13"),
        row(sentiment="bearish", tickers=["00700"], day="2020-05-13"),
        # 2020-05-14: 600519 bearish x1
        row(sentiment="bearish", tickers=["600519"], day="2020-05-14"),
        # 非 bearish / 无码 / degraded 行全部不进作用面
        row(sentiment="bullish", tickers=["600519"], day="2020-05-13"),
        row(sentiment="bearish", tickers=[], day="2020-05-13"),
        row(sentiment="bearish", tickers=["600519"], day="2020-05-13", ok=0),
    ]
    d = daily_ticker_bearish(rows)
    # (day,ticker) 对：05-13 的 600519、05-13 的 00700、05-14 的 600519
    assert d["n_pairs"] == 3
    counts = sorted(d["pair_counts"])
    assert counts == [1, 1, 3]
    assert d["max"] == 3
    assert d["p50"] in (1.0, 1)
    assert d["n_pairs_ge2"] == 1
    assert abs(d["ge2_share"] - 1 / 3) < 1e-9
    assert d["top_pairs"][0] == {"day": "2020-05-13", "ticker": "600519", "n": 3}
    # 全空输入
    assert daily_ticker_bearish([])["n_pairs"] == 0
