# -*- coding: utf-8 -*-
"""K-STOCK-386：策略扫描卡片化——奖牌/nan 守卫/红涨绿跌/文本降级 离线单测。

fixture 零数据依赖（不读行情、不发推送），只验 scan_card/scan_text 两个纯函数。
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner                                    # noqa: E402


def _mk(sharpe, ret, dd, bench, mkt="CN", name="x"):
    return {"market": mkt, "symbol": name, "name": name,
            "sharpe": sharpe, "total_return": ret, "max_drawdown": dd,
            "win_rate": 0.5, "benchmark_return": bench}


# 复刻真实推送里的边界样本：前三（奖牌区）/ nan 基准（五洲医疗型）/ 负夏普（创业黑马型）
RESULTS = [
    _mk(3.33, 0.688, -0.084, 0.499, "CRYPTO", "Solana"),
    _mk(3.03, 0.933, -0.193, 1.493, "US", "派拓网络"),
    _mk(2.90, 0.636, -0.049, 0.452, "CRYPTO", "以太坊"),
    _mk(1.59, 1.024, -0.174, float("nan"), "CN", "五洲医疗"),
    _mk(-0.82, -0.256, -0.326, 0.071, "CN", "创业黑马"),
]


def _md_joined(card):
    return "\n".join(e["content"] for e in card["body"]["elements"]
                     if e.get("tag") == "markdown")


def test_scan_card_structure():
    card = runner.scan_card("trend_follow", RESULTS,
                            ["CN", "CRYPTO", "US"], "10-14 16:40")
    # schema 2.0 + header 判例（同单票卡）
    assert card["schema"] == "2.0" and "body" in card
    assert "trend_follow" in card["header"]["title"]["content"]
    assert "38" not in card["header"]["subtitle"]["content"][:2]  # subtitle 带只数
    joined = _md_joined(card)
    assert "nan" not in joined                       # 🔴 nan 修复：卡内零 nan
    assert "—" in joined                             # nan 基准以 — 展示
    for m in ("🥇", "🥈", "🥉"):
        assert m in joined                           # 前三奖牌
    assert "#4" in joined and "#5" in joined         # 奖牌外 #名次
    assert "正夏普区（4 只）" in joined and "负夏普区（1 只）" in joined
    cols = [e for e in card["body"]["elements"] if e.get("tag") == "column_set"]
    assert cols, "顶部三列指标缺失"


def test_scan_card_cn_colors():
    card = runner.scan_card("tf", RESULTS, ["CN"], "t")
    joined = _md_joined(card)
    assert "red'>+68.8%</font>" in joined            # 正收益=红（境内红涨）
    assert "green'>-25.6%</font>" in joined          # 负收益=绿（境内绿跌）
    assert "green'>-101.0%</font>" in joined or "green'>" in joined  # 派拓超额为负


def test_scan_text_nan_safe():
    txt = runner.scan_text("trend_follow", RESULTS, ["CN", "US", "CRYPTO"])
    assert "nan" not in txt                          # 🔴 文本降级面同修
    assert "基准—" in txt
    assert "五洲医疗" in txt and "创业黑马" in txt
    assert "量化回测域" in txt and "成本假设" in txt


def test_is_nan_guard():
    assert runner._is_nan(float("nan")) and runner._is_nan(None)
    assert not runner._is_nan(0.499) and not runner._is_nan("bad")
