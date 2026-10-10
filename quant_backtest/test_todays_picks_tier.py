# -*- coding: utf-8 -*-
"""T1 聚合器口径金测（十五审 2026-10-10）：锁 tier_of 判定矩阵与
load_chanlun 坏行容错——一~十四审累计八条分支（共振计数/cross 旁路/
同批买卖并存/qpzm 回避/weak 否决/ST 封顶/veto 停挂/T2 三通道）此前
无任何自动化保护，全靠手跑回归。

挂 PytestWeekly（周一 05:30）每周复核；被测模块在 portal/，跨目录 import。
"""
import json
import os
import sys

import pytest

PORTAL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "portal"))
sys.path.insert(0, PORTAL)

from build_todays_picks import tier_of  # noqa: E402


def _e(name, *srcs):
    return {"code": "600000.SH", "name": name, "sources": list(srcs)}


STRONG_LEAD = {"s": "strong", "tag": "板块领涨"}
STRONG_COUNTER = {"s": "strong", "tag": "逆板块强势"}
CL_BUY = {"s": "chanlun", "tag": "3买", "dt": "2026-09-16"}
CL_SELL = {"s": "chanlun", "tag": "1卖", "dt": "2026-09-16"}
QPZM_BUY = {"s": "qpzm", "tag": "买入", "score": 85}
QPZM_BUY_LOW = {"s": "qpzm", "tag": "买入", "score": 70}
QPZM_AVOID = {"s": "qpzm", "tag": "回避"}
WEAK = {"s": "weak", "tag": "强失速"}
CROSS = {"s": "cross", "tag": "多策略共振"}


# ── T1 共振路径 ──────────────────────────────────────────────
def test_t1_strong_chanlun():
    assert tier_of(_e("亚邦股份", STRONG_LEAD, CL_BUY)) == "T1"


def test_t1_qpzm_chanlun():
    assert tier_of(_e("某票", QPZM_BUY, CL_BUY)) == "T1"


def test_t1_qpzm_strong():
    assert tier_of(_e("某票", QPZM_BUY, STRONG_LEAD)) == "T1"


def test_t1_cross_bypass():
    """cross 单源直接 T1（n_long>=2 派生，守卫在 load_cross 侧）。"""
    assert tier_of(_e("某票", CROSS)) == "T1"


def test_not_t1_counter_strong_chanlun():
    """逆板块强势不计 bull（三审方向盲区修正）→ 单 chanlun 买落 T2。"""
    assert tier_of(_e("某票", STRONG_COUNTER, CL_BUY)) == "T2"


# ── 否决闸门 ────────────────────────────────────────────────
def test_veto_cl_sell_same_batch():
    """同批买卖并存：买点不计共振（四审）→ chanlun 买通道 T2。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY, CL_SELL)) == "T2"


def test_veto_qpzm_avoid():
    """qpzm 回避一票否决 T1（四审）。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY, QPZM_AVOID)) == "T2"


def test_veto_weak():
    """weak 负面否决 T1（五审）。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY, WEAK)) == "T2"


def test_veto_st_caps_t3():
    """ST/*ST 不进 T1/T2，封顶 T3（七审）。"""
    assert tier_of(_e("*ST某股", STRONG_LEAD, CL_BUY)) == "T3"
    assert tier_of(_e("ST某股", CL_BUY)) == "T3"


def test_veto_suspended_t1():
    """否决源 err 失败当日 T1 停挂（十一审）：共振票降 T2。"""
    assert tier_of(_e("某票", STRONG_LEAD, CL_BUY), veto_ok=False) == "T2"
    assert tier_of(_e("某票", CROSS), veto_ok=False) == "T3"  # 无单策略通道


# ── T2 三通道 / CROWD / T3 ──────────────────────────────────
def test_t2_single_chanlun():
    assert tier_of(_e("某票", CL_BUY)) == "T2"


def test_t2_single_strong_lead():
    assert tier_of(_e("某票", STRONG_LEAD)) == "T2"


def test_t2_qpzm_score80():
    assert tier_of(_e("某票", QPZM_BUY)) == "T2"
    assert tier_of(_e("某票", QPZM_BUY_LOW)) == "T3"  # score<80 不抬 T2


def test_crowd_alert_band():
    nd = {"s": "ninedim", "tag": "九维出分", "band": "alert"}
    assert tier_of(_e("某票", nd)) == "CROWD"


def test_crowd_coexists_with_st():
    """ST 封顶只拦 T1/T2；九维拥挤警报可并存（七审明确口径）。"""
    nd = {"s": "ninedim", "tag": "九维出分", "band": "alert"}
    assert tier_of(_e("*ST某股", nd)) == "CROWD"


def test_t3_chanlun_sell_only():
    assert tier_of(_e("某票", CL_SELL)) == "T3"


# ── load_chanlun 坏行容错（十一审）─────────────────────────
def test_load_chanlun_skips_corrupt_lines(tmp_path, monkeypatch):
    fp = tmp_path / "chanlun_pool.jsonl"
    rows = [
        {"code": "600000.SH", "name": "浦发银行", "type": "3买",
         "dt": "2026-09-16", "detect_date": "2026-10-09", "price": 8.5},
        "NOT-JSON 坏行 {{{",
        {"code": "000001.SZ", "name": "平安银行", "type": "1卖",
         "dt": "2026-09-18", "detect_date": "2026-10-09"},
        {"code": "600519.SH", "name": "贵州茅台", "type": "2买",
         "dt": "2026-09-01", "detect_date": "2026-10-08"},  # 旧批次不入
    ]
    fp.write_text("\n".join(
        r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        for r in rows) + "\n", encoding="utf-8")
    import build_todays_picks as b
    monkeypatch.setattr(b, "QB", str(tmp_path.parent))
    # QB/data/chanlun_pool.jsonl 布局
    data_dir = tmp_path.parent / "data"
    data_dir.mkdir(exist_ok=True)
    fp2 = data_dir / "chanlun_pool.jsonl"
    fp2.write_text(fp.read_text(encoding="utf-8"), encoding="utf-8")
    stocks = {}
    meta = b.load_chanlun(stocks)
    assert meta["batch_date"] == "2026-10-09"
    assert meta["n"] == 2  # 坏行跳过、旧批次排除
    tags = {s["tag"] for e in stocks.values() for s in e["sources"]}
    assert tags == {"3买", "1卖"}


def test_load_chanlun_hard_fail_when_file_missing(tmp_path, monkeypatch):
    import build_todays_picks as b
    monkeypatch.setattr(b, "QB", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        b.load_chanlun({})  # 整文件缺失=源 err → main 捕获 → T1 停挂链路
