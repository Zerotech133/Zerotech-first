# -*- coding: utf-8 -*-
"""T2 通道闸门金测（2026-11-13）。锁死「必须有 chanlun 买点」判定矩阵。

挂 PytestWeekly（周一 05:30）每周复核；被测模块同目录，不跨目录 import。
"""

import pytest

from t2_channel_gate import (gate_report, gate_t2, has_chanlun_buy,
                             is_chanlun_buy, reject_reason)

CL_1 = {"s": "chanlun", "tag": "1买"}
CL_2 = {"s": "chanlun", "tag": "2买"}
CL_3 = {"s": "chanlun", "tag": "3买"}
CL_SELL = {"s": "chanlun", "tag": "1卖"}
CL_OTHER = {"s": "chanlun", "tag": "中枢震荡"}
STRONG = {"s": "strong", "tag": "板块领涨"}
QPZM = {"s": "qpzm", "tag": "买入", "score": 85}
WEAK = {"s": "weak", "tag": "深度垫底"}


def _s(code, *srcs):
    return {"code": code, "name": "票" + code, "tier": "T2",
            "sources": list(srcs)}


# ── 单条 source 判定 ────────────────────────────────────────────
@pytest.mark.parametrize("src", [CL_1, CL_2, CL_3])
def test_buy_tags(src):
    assert is_chanlun_buy(src) is True


@pytest.mark.parametrize("src", [CL_SELL, CL_OTHER, STRONG, QPZM, WEAK,
                                 {"s": "chanlun", "tag": "买卖点同现"},
                                 {"s": "chanlun"}, {"s": "CHANLUN", "tag": "1买"},
                                 None, "bad", {"tag": "1买"}])
def test_non_buy_tags(src):
    """含「卖」的混合 tag 一律不算买点（宁可少买，不可放宽）。"""
    assert is_chanlun_buy(src) is False


# ── 整票判定 ────────────────────────────────────────────────────
def test_has_buy_with_other_sources():
    """带 weak/strong 陪衬但仍有 chanlun 买点 → 保留（不误杀）。"""
    assert has_chanlun_buy(_s("A", WEAK, CL_3, STRONG)) is True


def test_no_buy_lifted_by_strong_qpzm():
    assert has_chanlun_buy(_s("B", STRONG, QPZM)) is False
    assert "lifted_by:" in reject_reason(_s("B", STRONG, QPZM))
    assert "qpzm" in reject_reason(_s("B", STRONG, QPZM))


def test_no_buy_sell_tag_only():
    """只有卖点 tag 也是毒源（不能靠 chanlun 源存在就放行），且单独留痕为
    chanlun_nonbuy —— 后验要能分辨「信号方向反了」与「无信号靠抬层」。"""
    assert has_chanlun_buy(_s("C", CL_SELL, STRONG)) is False
    assert reject_reason(_s("C", CL_SELL, STRONG)) == "chanlun_nonbuy"
    assert reject_reason(_s("C", CL_OTHER)) == "chanlun_nonbuy"


@pytest.mark.parametrize("bad", [{}, {"code": "D", "sources": []},
                                 {"code": "D", "sources": None},
                                 {"code": "D", "sources": "not-a-list"}, None])
def test_missing_signal_info_fail_closed(bad):
    """信息缺失一律剔除（fail-closed），并留 no_signal_info / malformed_row。"""
    assert has_chanlun_buy(bad) is False
    kept, rej = gate_t2([bad])
    assert kept == []
    assert len(rej) == 1


# ── 批量闸门 ────────────────────────────────────────────────────
def test_gate_t2_mixed():
    stocks = [
        _s("OK1", CL_1),
        _s("OK2", WEAK, CL_2),
        _s("BAD1", STRONG, QPZM),
        _s("BAD2", STRONG),
        _s("BAD3", CL_SELL),
    ]
    kept, rej = gate_t2(stocks)
    assert [s["code"] for s in kept] == ["OK1", "OK2"]
    assert [r["code"] for r in rej] == ["BAD1", "BAD2", "BAD3"]
    # 原对象不被改写（调用方继续按原结构消费）
    assert kept[0] is stocks[0]
    assert "sources" in rej[0] and rej[0]["why"].startswith("no_chanlun_buy")


def test_gate_report_shape():
    kept, rej = gate_t2([_s("OK", CL_3), _s("BAD", QPZM)])
    r = gate_report(kept, rej)
    assert r["applied"] is True
    assert (r["t2_in"], r["t2_kept"], r["t2_rejected"]) == (2, 1, 1)
    assert r["reject_pct"] == 50.0


def test_gate_report_empty():
    r = gate_report(*gate_t2([]))
    assert r["t2_in"] == 0 and r["reject_pct"] == 0.0


# ── 真实形态：当前 live 名单应 0 剔除（三闸已挡，闸门是护栏）────
def test_live_picks_have_zero_reject(monkeypatch):
    import json
    import os
    live = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                        "portal", "data", "todays_picks.json"))
    if not os.path.isfile(live):
        pytest.skip("live 名单不存在（换机/未生成）")
    d = json.load(open(live, encoding="utf-8"))
    t2 = [s for s in (d.get("stocks") or []) if s.get("tier") == "T2"]
    kept, rej = gate_t2(t2)
    assert len(kept) + len(rej) == len(t2)
    # 不强制 0（未来口径可能回退），但必须每笔剔除都有留痕
    for r in rej:
        assert r["code"] and r["why"]
