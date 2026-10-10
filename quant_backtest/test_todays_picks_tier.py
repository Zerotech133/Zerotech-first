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

from build_todays_picks import _stale_meta, tier_of  # noqa: E402
from build_todays_picks import _backfill_quotes  # noqa: E402


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
NINEDIM_ALERT = {"s": "ninedim", "tag": "九维出分", "score": 78, "band": "alert"}
NINEDIM_ALERT2 = {"s": "ninedim", "tag": "九维出分", "score": 86, "band": "alert2"}
NINEDIM_LOW = {"s": "ninedim", "tag": "九维出分", "score": 59, "band": "low"}


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


# ── stale 闸门 fail-closed（二十三审 2026-10-10）────────────
def test_stale_gate_basis_missing_pass_all():
    """行情湖不可达（basis 空）= 设计内全放行降级，不拦截。"""
    assert _stale_meta(None, "20260101", 4, "L", "f", "；t") is None
    assert _stale_meta(None, "", 4, "L", "f", "；t") is None


def test_stale_gate_src_day_missing_blocks():
    """src_day 空（生产端格式变更）fail-closed 拦截，不再静默放行。"""
    r = _stale_meta("20261009", "", 4, "L", "f", "；t")
    assert r and r["stale"] is True and r["n"] == 0


def test_stale_gate_src_day_unparseable_blocks():
    r = _stale_meta("20261009", "2026/10/", 4, "L", "f", "；t")
    assert r and r["stale"] is True and "不可解析" in r["note"]


def test_stale_gate_fresh_pass_expired_block():
    assert _stale_meta("20261009", "20261009", 4, "L", "f", "；t") is None
    r = _stale_meta("20261009", "20261001", 4, "L", "f", "；t")
    assert r and r["stale"] is True and "旧信号" in r["note"]


# ── 归档首写锁定（二十六审 2026-10-10）──────────────────────
def test_archive_first_write_wins(tmp_path):
    """周末重跑（basis 仍=上个交易日）不得覆写首版快照——后验地基保真。"""
    from build_todays_picks import _archive_once
    d1 = {"scan_run_date": "2026-10-09", "stocks": [{"code": "600000.SH"}]}
    arch, w1 = _archive_once(str(tmp_path), "20261009", d1)
    assert w1 is True and os.path.exists(arch)
    # 周六重跑产出变体清单 → 同名归档保留首次快照
    d2 = {"scan_run_date": "2026-10-09", "stocks": []}
    arch2, w2 = _archive_once(str(tmp_path), "20261009", d2)
    assert w2 is False and arch2 == arch
    kept = json.load(open(arch, encoding="utf-8"))
    assert kept["stocks"] == d1["stocks"]
    assert not os.path.exists(arch + ".tmp")


def test_archive_new_day_writes(tmp_path):
    """新交易日（basis 前进）正常落新档，首写锁定不误伤正常节奏。"""
    from build_todays_picks import _archive_once
    arch, w = _archive_once(str(tmp_path), "20261012", {"stocks": []})
    assert w is True and os.path.basename(arch) == "todays_20261012.json"


# ── 九维 alert 拥挤封顶 T1/T2（二十九审 2026-10-10）─────────
def test_t1_alert_band_capped_to_crowd():
    """bull≥2 共振票撞九维 alert → 不进 T1，落 CROWD 警报层（K33 延伸）。"""
    assert tier_of(_e("共振票", STRONG_LEAD, CL_BUY, NINEDIM_ALERT)) == "CROWD"
    assert tier_of(_e("共振票", STRONG_LEAD, CL_BUY, NINEDIM_ALERT2)) == "CROWD"


def test_t2_alert_band_capped_to_crowd():
    """单通道 T2 票撞九维 alert → 同样封顶落 CROWD。"""
    assert tier_of(_e("缠论票", CL_BUY, NINEDIM_ALERT)) == "CROWD"
    assert tier_of(_e("强势票", STRONG_LEAD, NINEDIM_ALERT2)) == "CROWD"


def test_ninedim_low_band_not_cap():
    """low/na 九维（观察无警报）不影响 T1/T2 正常判定。"""
    assert tier_of(_e("共振票", STRONG_LEAD, CL_BUY, NINEDIM_LOW)) == "T1"
    assert tier_of(_e("缠论票", CL_BUY, NINEDIM_LOW)) == "T2"


# ── 现价回填：湖权威覆盖（三十七审 2026-10-10）─────────────
def test_backfill_overwrites_placeholder(monkeypatch):
    """loader 占位价（chanlun 批次价等）不得阻塞湖权威价——1009 档实证
    76/766 票「close=信号日旧价+pct_chg=最新日」混搭（亚邦 4.32→5.07）。"""
    import pandas as pd
    import tdx_read
    monkeypatch.setattr(tdx_read, "latest_date", lambda t: "20261009")
    monkeypatch.setattr(
        tdx_read, "read_day",
        lambda *a, **k: pd.DataFrame({"ts_code": ["603188.SH"],
                                      "close": [5.07], "pct_chg": [0.6]}))
    stocks = {"603188.SH": {"code": "603188.SH", "name": "亚邦股份",
                            "close": 4.32, "pct_chg": None}}
    day, n = _backfill_quotes(stocks)
    assert day == "20261009" and n == 1
    assert stocks["603188.SH"]["close"] == 5.07
    assert stocks["603188.SH"]["pct_chg"] == 0.6


def test_backfill_keeps_placeholder_on_lake_miss(monkeypatch):
    """湖无行（停牌/退市）→ 保留 loader 占位不动，不误清空。"""
    import pandas as pd
    import tdx_read
    monkeypatch.setattr(tdx_read, "latest_date", lambda t: "20261009")
    monkeypatch.setattr(
        tdx_read, "read_day",
        lambda *a, **k: pd.DataFrame({"ts_code": [], "close": [], "pct_chg": []}))
    stocks = {"000582.SZ": {"code": "000582.SZ", "name": "北部湾港",
                            "close": 7.7, "pct_chg": None}}
    day, n = _backfill_quotes(stocks)
    assert day == "20261009" and n == 0
    assert stocks["000582.SZ"]["close"] == 7.7  # 占位保留
    assert stocks["000582.SZ"]["pct_chg"] is None
