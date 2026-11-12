# -*- coding: utf-8 -*-
"""J-03 交叉卡门禁（2026-11-13）。

锁的是**语义**，不是格式：
1. 票池只含涨停/触板 —— 「涨停事件交叉卡」里混进没涨停的票 = 换了分析对象（#28）。
2. 席位维三态分明 —— 当日无快照是 `pending(T+1)`（等），不是 `unavailable`（缺）；
   有快照但票不在是 `none`（未上榜），三者混成一个词，第二天就不知道该等还是该补。
3. 分钟残缺必须降级 —— 85 根从 13:35 起时，`at_open/first_move/seal_time` 一律置空，
   不许把「数据从 13:35 开始」说成「13:35 才异动」（数据缺陷冒充结论，判例 #38 同族）。
4. `divergence` 三态 —— `net_mf=0` 是「无覆盖」，不是「净额为零」。
5. 卡面不得出现结论性措辞（JEV 红线：不荐股、不预测）。
"""
from __future__ import annotations

import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORTAL = os.path.join(ROOT, "portal")
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import jev_cross as C  # noqa: E402

pd = pytest.importorskip("pandas")

DAY = "20261110"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """把四个数据目录都指到临时目录，测试不碰真实 data/，也不联网。"""
    daily = tmp_path / "daily"
    mins = tmp_path / "stk_mins"
    snap = tmp_path / "_jev_snap"
    seat = tmp_path / "hm_detail"
    top = tmp_path / "top_list"
    for p in (daily, mins, snap, seat, top):
        p.mkdir()
    monkeypatch.setattr(C, "DAILY_DIR", str(daily))
    monkeypatch.setattr(C, "MINS_DIR", str(mins))
    monkeypatch.setattr(C, "SNAP_DIR", str(snap))
    monkeypatch.setattr(C, "SEAT_DIR", str(seat))
    monkeypatch.setattr(C, "TOP_DIR", str(top))
    # 席位/交叉卡读的是 jev_seat 的目录常量，两处要一起指
    import jev_seat
    monkeypatch.setattr(jev_seat, "SEAT_DIR", str(seat))
    jev_seat._CACHE.clear()
    monkeypatch.setenv("JEV_CROSS_MINS_REMOTE", "off")
    yield {"daily": daily, "mins": mins, "snap": snap, "seat": seat, "top": top}
    jev_seat._CACHE.clear()


def _daily(lab, rows):
    df = pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "high",
                                     "low", "close", "pre_close", "pct_chg"])
    df.to_parquet(os.path.join(str(lab["daily"]), "d_%s.parquet" % DAY),
                  index=False)


def _mins(lab, code, n, start="09:30", price=10.0, step_minute=1):
    """构造 n 根分钟。start 决定首根时刻 —— 用来测「非 09:30 起点」。"""
    h, m = [int(x) for x in start.split(":")]
    times = []
    for i in range(n):
        tot = h * 60 + m + i * step_minute
        times.append("%s-%s-%s %02d:%02d:00"
                     % (DAY[:4], DAY[4:6], DAY[6:], tot // 60 % 24, tot % 60))
    df = pd.DataFrame({
        "ts_code": [code] * n,
        "trade_time": times,
        "open": [price] * n,
        "close": [price] * n,
        "high": [price] * n,
        "low": [price] * n,
        "vol": [1000.0] * n,
        "amount": [10000.0] * n,
    })
    df.to_parquet(os.path.join(str(lab["mins"]),
                               "%s_%s.parquet" % (code.replace(".", "_"), DAY[:6])),
                  index=False)


def _flow(lab, payload):
    with io.open(os.path.join(str(lab["snap"]), "flow_%s.json" % DAY),
                 "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _disc(lab, payload):
    with io.open(os.path.join(str(lab["snap"]), "disc_%s.json" % DAY),
                 "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _seat(lab, rows):
    with io.open(os.path.join(str(lab["seat"]), "%s.jsonl" % DAY),
                 "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _top(lab, rows):
    with io.open(os.path.join(str(lab["top"]), "%s.jsonl" % DAY),
                 "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _limit_row(code="000001.SZ", pre=10.0, close=11.0):
    """主板 10% 涨停：pre 10 → 涨停价 11.0。"""
    return {"ts_code": code, "trade_date": DAY, "open": pre, "high": close,
            "low": pre, "close": close, "pre_close": pre, "pct_chg": 10.0}


def _seat_row(code="000001.SZ", hm="机构专用", buy=1000000.0, sell=0.0):
    return {"trade_date": DAY, "ts_code": code, "ts_name": "测试票",
            "buy_amount": buy, "sell_amount": sell, "net_amount": buy - sell,
            "hm_name": hm, "hm_orgs": "机构专用"}


# ── 1. 票池只含涨停/触板 ──────────────────────────────────
def test_candidate_pool_only_limit_up(lab):
    _daily(lab, [
        _limit_row("000001.SZ"),                       # 涨停
        {"ts_code": "000002.SZ", "trade_date": DAY, "open": 10, "high": 10.9,
         "low": 9.8, "close": 10.4, "pre_close": 10.0, "pct_chg": 4.0},   # 没涨停
        {"ts_code": "000003.SZ", "trade_date": DAY, "open": 10, "high": 11.0,
         "low": 9.9, "close": 10.6, "pre_close": 10.0, "pct_chg": 6.0},   # 触板未封
    ])
    codes = C.candidate_codes(DAY)
    assert "000001.SZ" in codes
    assert "000003.SZ" in codes, "触板未封也是事件，必须出卡"
    assert "000002.SZ" not in codes, "没涨停的票不得进涨停事件池"


def test_candidate_pool_any_mode_keeps_screen_pass(lab):
    """any 模式保留粗筛池、不做精判：pct 9.6 的票粗筛进、但 10% 档没涨停。"""
    _daily(lab, [_limit_row("000001.SZ"),
                 {"ts_code": "000004.SZ", "trade_date": DAY, "open": 10,
                  "high": 10.96, "low": 9.9, "close": 10.96,
                  "pre_close": 10.0, "pct_chg": 9.6}])
    assert len(C.candidate_codes(DAY, limit_up_only=False)) == 2
    assert C.candidate_codes(DAY) == ["000001.SZ"]


# ── 2. 席位维三态 ─────────────────────────────────────────
def test_seat_pending_when_no_snapshot(lab):
    d = C._dim_seat(DAY, "000001.SZ", "000001")
    assert d["status"] == "pending", "当日无快照是正常滞后，不是缺数据"
    assert "T+1" in d["note"]


def test_seat_none_when_not_on_list(lab):
    _seat(lab, [_seat_row("000002.SZ")])
    d = C._dim_seat(DAY, "000001.SZ", "000001")
    assert d["status"] == "none"


def test_seat_ok_when_present(lab):
    _seat(lab, [_seat_row("000001.SZ", hm="机构专用", buy=2000000.0)])
    d = C._dim_seat(DAY, "000001.SZ", "000001")
    assert d["status"] == "ok"
    assert d["precision"] == "T+1"
    assert d["data"]["by_kind"]["inst"] == pytest.approx(200.0, abs=0.1)


def test_seat_persona_never_leaks_name(lab):
    _seat(lab, [_seat_row("000001.SZ", hm="某游资甲", buy=1000000.0)])
    d = C._dim_seat(DAY, "000001.SZ", "000001")
    assert "某游资甲" not in json.dumps(d, ensure_ascii=False)
    assert "PSN_" in json.dumps(d, ensure_ascii=False)


# ── 3. 分钟残缺降级 ───────────────────────────────────────
def test_time_partial_when_few_bars(lab):
    _daily(lab, [_limit_row()])
    _mins(lab, "000001.SZ", n=85, start="13:35")
    d = C._dim_time("000001.SZ", DAY, 11.0, 10.0, None)
    assert d["status"] == "partial"
    for k in ("first_move", "at_open", "seal_time", "gap_pct"):
        assert d["data"][k] is None, "%s 必须置空，不能让残缺数据冒充结论" % k
    assert d["data"]["bars"] == 85


def test_time_ok_when_full_day(lab):
    _daily(lab, [_limit_row()])
    # 241 根、09:30 起
    _mins(lab, "000001.SZ", n=241, start="09:30")
    d = C._dim_time("000001.SZ", DAY, 11.0, 10.0, None)
    assert d["status"] == "ok"
    assert d["data"]["first_bar"].endswith("09:30:00")


def test_time_partial_when_source_is_rt_min(lab, monkeypatch):
    """rt_min 有 237 根、首根 09:30，看着完整，但缺 15:00 收盘那根——
    工具侧（jev_mins_fetch）因此拒写，卡面也必须降级，不能报 ok。"""
    import jev_graph
    fake = {"ok": True, "source": "xtcloud", "tbl": "ts_rt_min", "open": 10.0,
            "bars": 237, "first_bar": "2026-11-10 09:30:00",
            "last_bar": "2026-11-10 14:59:00", "first_move": "09:35",
            "first_move_pct": 4.2, "at_open": False, "gap_pct": 0.5,
            "seal_time": "10:10", "spike": None, "high_pct": 10.0,
            "close_pct": 10.0}
    monkeypatch.setattr(jev_graph, "minute_profile", lambda *a, **k: fake)
    d = C._dim_time("000001.SZ", DAY, 11.0, 10.0, None, remote=True)
    assert d["status"] == "partial"
    assert d["data"]["seal_time"] == "10:10", "数据仍可看，只是标注降级"
    assert "15:00" in d["note"]


def test_time_partial_when_source_is_rt_min(lab, monkeypatch):
    """rt_min 有 237 根、首根 09:30，看着完整，但缺 15:00 收盘那根——
    工具侧（jev_mins_fetch）因此拒写，卡面也必须降级，不能报 ok。"""
    import jev_graph
    fake = {"ok": True, "source": "xtcloud", "tbl": "ts_rt_min", "open": 10.0,
            "bars": 237, "first_bar": "2026-11-10 09:30:00",
            "last_bar": "2026-11-10 14:59:00", "first_move": "09:35",
            "first_move_pct": 4.2, "at_open": False, "gap_pct": 0.5,
            "seal_time": "10:10", "spike": None, "high_pct": 10.0,
            "close_pct": 10.0}
    monkeypatch.setattr(jev_graph, "minute_profile", lambda *a, **k: fake)
    d = C._dim_time("000001.SZ", DAY, 11.0, 10.0, None, remote=True)
    assert d["status"] == "partial"
    assert d["data"]["seal_time"] == "10:10", "数据仍可看，只是标注降级"
    assert "15:00" in d["note"]


def test_time_unavailable_when_no_file(lab):
    _daily(lab, [_limit_row()])
    d = C._dim_time("000001.SZ", DAY, 11.0, 10.0, None)
    assert d["status"] == "unavailable"
    assert "jev_mins_fetch" in d["note"]


# ── 4. divergence 三态 ────────────────────────────────────
def test_divergence_true_when_main_in_all_out(lab):
    _flow(lab, {"000001": {"main_net": 500.0, "net_mf": -300.0,
                           "main_buy": 900.0, "main_sell": 400.0}})
    d = C._dim_flow(DAY, "000001")
    assert d["data"]["divergence"] is True


def test_divergence_false_when_aligned(lab):
    _flow(lab, {"000001": {"main_net": 500.0, "net_mf": 300.0,
                           "main_buy": 900.0, "main_sell": 400.0}})
    assert C._dim_flow(DAY, "000001")["data"]["divergence"] is False


def test_divergence_none_when_no_coverage(lab):
    """net_mf=0 是「无覆盖」（北交所实测 362/5580），不是「净额为零」。"""
    _flow(lab, {"000001": {"main_net": 500.0, "net_mf": 0.0,
                           "main_buy": 900.0, "main_sell": 400.0}})
    d = C._dim_flow(DAY, "000001")
    assert d["data"]["divergence"] is None
    assert "无覆盖" in d["note"]


def test_flow_unavailable_without_snapshot(lab):
    d = C._dim_flow(DAY, "000001")
    assert d["status"] == "unavailable"


# ── 5. 完整度分列 ─────────────────────────────────────────
def test_completeness_separates_none_from_unknown(lab):
    _daily(lab, [_limit_row()])
    _mins(lab, "000001.SZ", n=241, start="09:30")
    _flow(lab, {"000001": {"main_net": 500.0, "net_mf": -300.0,
                           "main_buy": 900.0, "main_sell": 400.0}})
    _seat(lab, [_seat_row()])
    _disc(lab, {})            # 有快照但该票无公告 → none（确认无）
    card = C.build_card("000001.SZ", DAY)
    cp = card["completeness"]
    assert cp["ok"] == 3, cp
    assert cp["none"] == 1, cp
    assert cp["n_known"] == 4
    assert cp["unavailable"] == 0


def test_build_card_marks_limit_up(lab):
    _daily(lab, [_limit_row()])
    card = C.build_card("000001.SZ", DAY)
    assert card["limit"]["is_limit_up"] is True
    assert card["limit"]["ratio"] == 0.10
    assert card["limit"]["price"] == pytest.approx(11.0)


# ── 6. 卡面不得出现结论性措辞 ─────────────────────────────
def test_render_has_no_advice_wording(lab):
    _daily(lab, [_limit_row()])
    card = C.build_card("000001.SZ", DAY)
    txt = C.render_markdown(card)
    for bad in ("建议买入", "建议卖出", "看涨", "看跌", "目标价",
                "预期收益", "推荐", "必涨"):
        assert bad not in txt, "JEV 红线：卡面出现结论性措辞「%s」" % bad


def test_render_runs_on_empty_card(lab):
    _daily(lab, [_limit_row()])
    card = C.build_card("000001.SZ", DAY)
    assert "完整度" in C.render_markdown(card)
