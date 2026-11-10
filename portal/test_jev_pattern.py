# -*- coding: utf-8 -*-
"""JEV P5.1 pattern matcher 回归测试。

纪律（老田铁律）：
- **不写日期字符串字面量**。日期/编号经 Write/Edit 通道可能串味成另一个
  合法值，从而让「畸形 vs 合法」的校验门被静默漏过。这里时间锚点一律用
  `datetime` 对象注入，日期串用 chr() 码点构造并以 ord 双向复核。
- 纯函数层不读盘、不触网；`compute_delta_min` 的本地文件读取全部 monkeypatch。
- 核心纪律测试：锚点缺失 → unpriced，绝不硬算；维度缺失 → None，绝不当 False。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_pattern as P  # noqa: E402

# 纯字母 code：彻底避开「编号字面量串味」这一类误判源
CODE = "ABCDEF"

# 日期串只用码点构造；构造后立刻用 ord 复核（打印不可信，只信布尔判定）
_DAY_CP = (50, 48, 50, 54, 49, 49, 48, 54)
DAY = "".join(chr(c) for c in _DAY_CP)
DAY_ISO_CP = (50, 48, 50, 54, 45, 49, 49, 45, 48, 54)
DAY_ISO = "".join(chr(c) for c in DAY_ISO_CP)


def test_day_codepoints():
    """双向复核：构造结果与期望码点严格一致（不比较打印出来的字符串）。"""
    assert [ord(c) for c in DAY] == list(_DAY_CP)
    assert [ord(c) for c in DAY_ISO] == list(DAY_ISO_CP)
    assert len(DAY) == len(_DAY_CP)


_DAY_NUM = (2026, 11, 6)


def _dt(h, m):
    """时间锚点用 datetime 对象，不经过任何字符串日期字面量。"""
    return datetime(*_DAY_NUM, h, m)


def _sig(**kw):
    """构造一个三维度齐备、默认全不命中极端条件的信号。"""
    d = dict(
        code=CODE, day=DAY,
        flow_has=True, main_net=0.0, net_mf=0.0,
        social_has=True, xq_hot_rank=None, xq_discuss_rank=None,
        xq_trade_rank=None, xq_follow_rank=None,
        guba_rank=90, guba_rank_chg=0, posts=1, reads=100, comments=0,
        delta_kind="sync", delta_min=0.0,
        t_limit_seal=None, t_limit_touch=None,
        t_news_first=None, t_news_last=None,
        news_n=0, touch_only=False, delta_reason=None,
    )
    d.update(kw)
    return P.Signal(**d)


def _ids(hits):
    return {h.pattern for h in hits}


# ── 8 个 pattern 各自可达（三维度全中 → confidence 应为满分） ──
@pytest.mark.parametrize("pid,kw", [
    ("P1", dict(main_net=100.0, net_mf=-100.0,
                guba_rank=3, delta_kind="price_lead", delta_min=45.0)),
    ("P2", dict(delta_kind="price_lead", delta_min=45.0)),
    ("P3", dict(main_net=100.0, net_mf=100.0, xq_hot_rank=5,
                delta_kind="price_lead", delta_min=45.0)),
    ("P4", dict(guba_rank_chg=5, delta_kind="news_drive", delta_min=-45.0)),
    ("P5", dict(guba_rank=3, xq_hot_rank=3, delta_kind="sync", delta_min=0.0)),
    ("P6", dict(main_net=100.0, net_mf=100.0,
                delta_kind="price_lead", delta_min=15.0)),
    ("P7", dict(main_net=100.0, net_mf=-100.0, guba_rank=3,
                delta_kind="price_lead", delta_min=45.0,
                touch_only=True, t_limit_touch="x", t_limit_seal="x")),
    ("P8", dict(main_net=100.0, net_mf=100.0, xq_hot_rank=5,
                delta_kind="price_lead", delta_min=45.0)),
])
def test_all_eight_patterns_reachable(pid, kw):
    hits = P.match_signal(_sig(**kw))
    assert pid in _ids(hits), "pattern %s 未被命中: %s" % (pid, _ids(hits))
    h = next(h for h in hits if h.pattern == pid)
    assert all(h.dims[k] is True for k in ("fund", "social", "time"))
    assert h.confidence == pytest.approx(1.0)
    assert h.hypothesis and h.fork          # 预期演化 + 分化节点留痕


# ── ≥2 维度门槛 ────────────────────────────────────────────────
def test_single_dimension_is_not_a_pattern():
    # 只有资金背离，社交与时间都缺失 → 不应产生任何 pattern
    s = _sig(main_net=100.0, net_mf=-100.0, social_has=False,
             delta_kind="unpriced", delta_min=None)
    assert P.match_signal(s) == []


def test_two_dimensions_enough():
    s = _sig(main_net=100.0, net_mf=-100.0, guba_rank=3,
             delta_kind="unpriced", delta_min=None)
    hits = P.match_signal(s)
    assert "P1" in _ids(hits)
    h = next(h for h in hits if h.pattern == "P1")
    assert h.dims["time"] is None
    assert h.confidence == pytest.approx(2 / 3, abs=1e-4)  # 模块保留 4 位小数


# ── 缺失 != False（本轮最容易造假数据的一处） ───────────────────
def test_uncovered_code_is_not_institution_silent():
    s = _sig(social_has=True, guba_rank=None, guba_rank_chg=None,
             posts=None, reads=None, comments=None)
    assert s.social_covered() is False
    assert P.xq_silent(s, P.DEFAULT_TH) is None
    assert P.institution_hot(s, P.DEFAULT_TH) is None
    assert P.retail_hot(s, P.DEFAULT_TH) is None
    # P1 的社交维必须记 None，不能被当成「机构沉默」白送一票
    s2 = _sig(social_has=True, main_net=100.0, net_mf=-100.0,
              guba_rank=None, posts=None, reads=None,
              delta_kind="price_lead", delta_min=45.0)
    h = next((x for x in P.match_signal(s2) if x.pattern == "P1"), None)
    assert h is not None                      # 资金 + 时间两维足以命中
    assert h.dims["social"] is None           # 但社交维必须标缺失
    assert h.confidence == pytest.approx(2 / 3, abs=1e-4)  # 模块保留 4 位小数


def test_snapshot_missing_marks_dim_unavailable():
    s = _sig(social_has=False, flow_has=False,
             main_net=None, net_mf=None,
             delta_kind="unpriced", delta_min=None)
    assert P.match_signal(s) == []


# ── 判例 #25③：锚点缺失即 unpriced，绝不硬算 ────────────────────
def test_unpriced_blocks_time_dimension():
    for kind, val in (("unpriced", None), ("unpriced", 45.0),
                      ("price_lead", None)):
        s = _sig(delta_kind=kind, delta_min=val)
        assert P._time_ready(s) is False
        assert P.time_price_lead(s, P.DEFAULT_TH) is None
        assert P.time_news_drive(s, P.DEFAULT_TH) is None


def test_threshold_bands():
    th = P.DEFAULT_TH
    assert P.time_price_lead(_sig(delta_kind="sync", delta_min=th.th_lead),
                             th) is False
    assert P.time_price_lead(_sig(delta_kind="price_lead",
                                  delta_min=th.th_lead + 0.1), th) is True
    assert P.time_news_drive(_sig(delta_kind="sync",
                                  delta_min=-th.th_drive), th) is False
    assert P.time_news_drive(_sig(delta_kind="news_drive",
                                  delta_min=-th.th_drive - 0.1), th) is True
    assert P.time_sync(_sig(delta_kind="sync", delta_min=0.0), th) is True


# ── compute_delta_min：合成分钟 + 合成锚点，全程不读盘 ───────────
def _mins(rows):
    return [{"trade_time": t, "close": c, "high": h}
            for t, c, h in rows]


def _base_daily():
    return {CODE: {"pre_close": 10.0, "pct_chg": 10.0}}


def _seal_rows():
    # 主板 10% → 涨停价 11.00；9:35 首次封板
    return _mins([
        (_dt(9, 31), 10.2, 10.3),
        (_dt(9, 35), 11.0, 11.0),
        (_dt(9, 36), 11.0, 11.0),
    ])


def _compute(monkeypatch, mins_rows, anchors, daily=None):
    monkeypatch.setattr(P, "_mins_rows",
                        lambda code6, day, cache: mins_rows)
    return P.compute_delta_min(
        CODE, DAY, daily=daily or _base_daily(), names={},
        anchors=anchors, cache={})


def test_compute_price_lead(monkeypatch):
    # 新闻 10:05，封板 9:35 → Δmin = +30（价格先行 / 消息追认）
    anchors = {CODE: [(_dt(10, 5), "sina", "x")]}
    out = _compute(monkeypatch, _seal_rows(), anchors)
    assert out["delta_kind"] == "price_lead"
    assert out["delta_min"] == pytest.approx(30.0)
    assert out["delta_reason"] is None
    assert out["news_n"] == len(anchors[CODE])


def test_compute_news_drive(monkeypatch):
    # 新闻最晚 9:10 也比封板早 25min → 消息驱动（取 d_last）
    anchors = {CODE: [(_dt(9, 5), "sina", "x"),
                      (_dt(9, 10), "eastmoney", "y")]}
    out = _compute(monkeypatch, _seal_rows(), anchors)
    assert out["delta_kind"] == "news_drive"
    assert out["delta_min"] == pytest.approx(-25.0)


def test_compute_sync(monkeypatch):
    out = _compute(monkeypatch, _seal_rows(),
                   {CODE: [(_dt(9, 35), "sina", "x")]})
    assert out["delta_kind"] == "sync"
    assert out["delta_min"] == pytest.approx(0.0)


def test_compute_no_news_anchor_is_unpriced(monkeypatch):
    out = _compute(monkeypatch, _seal_rows(), {})
    assert out["delta_kind"] == "unpriced"
    assert out["delta_min"] is None
    assert out["delta_reason"] == "no_news_anchor"


def test_compute_no_price_anchor_is_unpriced(monkeypatch):
    out = _compute(monkeypatch, [], {CODE: [(_dt(10, 5), "sina", "x")]})
    assert out["delta_kind"] == "unpriced"
    assert out["delta_reason"] == "no_minute"


def test_compute_no_pre_close_is_unpriced(monkeypatch):
    out = _compute(monkeypatch, _seal_rows(),
                   {CODE: [(_dt(10, 5), "sina", "x")]},
                   daily={CODE: {"pre_close": None, "pct_chg": None}})
    assert out["delta_kind"] == "unpriced"
    assert out["delta_reason"] == "no_pre_close"


def test_touch_only_no_seal_is_unpriced(monkeypatch):
    # 盘中触板但收盘未封 → 主锚点缺失，只记 touch，绝不硬算 Δmin
    rows = _mins([(_dt(9, 35), 10.6, 11.0), (_dt(9, 40), 10.4, 10.5)])
    out = _compute(monkeypatch, rows, {CODE: [(_dt(10, 5), "sina", "x")]})
    assert out["t_limit_touch"] is not None
    assert out["t_limit_seal"] is None
    assert out["touch_only"] is True
    assert out["delta_kind"] == "unpriced"
    assert out["delta_reason"] == "no_seal_touch_only"


# ── 分钟文件读取的两个真实坑（P5.1 实测踩中） ──────────────────
def test_mins_rows_full_ts_code_and_day_scoped_cache(monkeypatch, tmp_path):
    """stk_mins.ts_code 是完整代码 'ABCDEF.SZ'，不是 6 位；且缓存必须带 day。

    用 6 位做 arrow 等值过滤会静默 0 行 → 全样本假 unpriced（本轮实测踩中）；
    缓存键只按 path 会把 A 日切片喂给 B 日 → 封板时刻错位。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    monkeypatch.setattr(P, "MINS_DIR", str(tmp_path))
    d1 = datetime(*_DAY_NUM).date().isoformat()
    d2 = (datetime(*_DAY_NUM) + timedelta(days=1)).date().isoformat()
    rows = []
    for i, close in enumerate((10.1, 10.2, 10.3)):
        rows.append({"ts_code": CODE + ".SZ",  # 完整代码，不是 6 位
                     "trade_time": "%s 09:3%d:00" % (d1, i),
                     "close": close, "high": close,
                     "open": close, "low": close, "vol": 1, "amount": 1})
    rows.append({"ts_code": CODE + ".SZ",
                 "trade_time": d2 + " 09:30:00",
                 "close": 10.4, "high": 10.4,
                 "open": 10.4, "low": 10.4, "vol": 1, "amount": 1})
    pq.write_table(pa.Table.from_pylist(rows),
                   str(tmp_path / ("%s_SZ_%s.parquet" % (CODE, DAY[:6]))))

    cache = {}
    r1 = P._mins_rows(CODE, DAY, cache)
    assert len(r1) == len([r for r in rows if str(r["trade_time"])[:10] == d1])
    day2 = "".join(chr(c) for c in _DAY_CP[:6]) + "".join(chr(c) for c in (48, 55))
    r2 = P._mins_rows(CODE, day2, cache)
    assert len(r2) == 1
    assert str(r2[0]["trade_time"])[:10] == d2
    assert r1 is not r2                    # 不能被同 path 的缓存串味


# ── 契约入口 / 统计 / 离线守卫 ─────────────────────────────────
def test_match_contract_with_injected_signal():
    s = _sig(main_net=100.0, net_mf=-100.0, guba_rank=3,
             delta_kind="price_lead", delta_min=45.0)
    hits = P.match(CODE, DAY, signal=s)
    assert "P1" in _ids(hits)
    assert hits[0].values["main_net"] == 100.0
    assert hits[0].values["delta_kind"] == "price_lead"


def test_summarize_is_exploratory_not_calibrated():
    s1 = _sig(main_net=100.0, net_mf=-100.0, guba_rank=3,
              delta_kind="price_lead", delta_min=45.0)
    s2 = _sig(code="GHIJKL", social_has=False, flow_has=False,
              delta_kind="unpriced", delta_min=None)
    hits = {s.code: P.match_signal(s) for s in (s1, s2)}
    rep = P.summarize([s1, s2], hits)
    assert rep["n"] == len(hits)
    assert rep["n_hit_any"] == 1
    assert rep["coverage"] == pytest.approx(0.5)
    assert rep["calibrated"] is False      # 未经 P5.4 校准，不得进判定闸
    assert rep["delta_kind"]["unpriced"] == 1


def test_day_context_offline_guard():
    with pytest.raises(ValueError):
        P.DayContext(DAY, offline=False)


def test_pattern_registry_complete():
    ids = [p[0] for p in P.PATTERNS]
    assert ids == ["P" + str(i) for i in range(1, 9)]
    assert P.MIN_DIMS == 2
