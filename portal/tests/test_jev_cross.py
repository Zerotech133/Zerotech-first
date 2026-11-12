# -*- coding: utf-8 -*-
"""J-03 交叉卡门禁（2026-11-13，两维卡定案后重写）。

锁的是**语义**，不是格式：
1. 票池只含涨停/触板 —— 「涨停事件交叉卡」里混进没涨停的票 = 换了分析对象（#28）。
2. 席位维三态分明 —— 当日无快照是 `pending(T+1)`（等），不是 `unavailable`（缺）；
   有快照但票不在是 `none`（未上榜），三者混成一个词，第二天就不知道该等还是该补。
3. 分钟残缺必须降级 —— 85 根从 13:35 起时，`at_open/first_move/seal_time` 一律置空，
   不许把「数据从 13:35 开始」说成「13:35 才异动」（数据缺陷冒充结论，判例 #38 同族）。
4. `divergence` 三态 —— `net_mf=0` 是「无覆盖」，不是「净额为零」。
5. 卡面不得出现结论性措辞（JEV 红线：不荐股、不预测）。
6. **两维主卡**（裁定 A）—— 验收只看 flow+seat；附表两维取不到**不得**拖累 verdict。
7. **已知率不得单独用**（裁定 C）—— verdict 三档 pass/wait/fail，`pending` 不算 pass；
   百分比只在附注里出现，且必须与四态计数同行。

日期字面量一律码点构造（用户级铁律：Write/Edit 通道亦可能串味，日期/编号必须
码点复核，不用字面量）。
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

# "20261110" —— 码点构造，两向可复核
DAY = "".join(chr(c) for c in (0x32, 0x30, 0x32, 0x36, 0x31, 0x31, 0x31, 0x30))


def _dt(hhmm: str) -> str:
    """DAY + hh:mm:00，同样不写死日期字面量。"""
    return "%s-%s-%s %s:00" % (DAY[:4], DAY[4:6], DAY[6:], hhmm)


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


_FLOW_OK = {"000001": {"main_net": 500.0, "net_mf": -300.0,
                       "main_buy": 900.0, "main_sell": 400.0}}


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
            "bars": 237, "first_bar": _dt("09:30"), "last_bar": _dt("14:59"),
            "first_move": "09:35", "first_move_pct": 4.2, "at_open": False,
            "gap_pct": 0.5, "seal_time": "10:10", "spike": None,
            "high_pct": 10.0, "close_pct": 10.0}
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
    _flow(lab, _FLOW_OK)
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


# ── 5. 完整度分列（旧口径保留：四维仍在，只是不再当验收） ──
def test_completeness_separates_none_from_unknown(lab):
    _daily(lab, [_limit_row()])
    _mins(lab, "000001.SZ", n=241, start="09:30")
    _flow(lab, _FLOW_OK)
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
    txt = C.render_markdown(card)
    assert "验收" in txt
    assert "四态明细" in txt


# ── 7. 两维主卡（裁定 A）──────────────────────────────────
def test_primary_dims_are_flow_and_seat():
    assert C.PRIMARY_DIMS == ("flow", "seat")
    assert C.AUX_DIMS == ("time", "narrative")
    assert not set(C.PRIMARY_DIMS) & set(C.AUX_DIMS)


def test_aux_dims_never_enter_acceptance(lab):
    """时间/叙事全取不到、主维齐全 → 仍然 pass。附表不拖累主维。

    这不是「放过」，而是承认附表精度天然低：叙事维的公告没有时分，
    时间维的小票分钟要采集工位解决。拿它们卡验收，等于用不可得的维
    去否掉可得的维。
    """
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)
    _seat(lab, [_seat_row()])
    card = C.build_card("000001.SZ", DAY)
    assert card["dims"]["time"]["status"] == "unavailable"
    assert card["dims"]["narrative"]["status"] == "unavailable"
    assert card["primary"]["known"] == 2
    assert card["completeness"]["n_known"] == 2, "全四维只有 2 已知，不被美化"
    assert card["acceptance"]["verdict"] == "pass"
    assert card["acceptance"]["reference_all_dims_pct"] == 50.0


def test_verdict_wait_when_seat_pending(lab):
    """当日无席位快照 → wait，不许 pass（「还没到」不等于「没问题」）。"""
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)
    card = C.build_card("000001.SZ", DAY)
    assert card["dims"]["seat"]["status"] == "pending"
    assert card["acceptance"]["verdict"] == "wait"
    assert card["acceptance"]["breakdown"]["pending"] == 1


def test_verdict_fail_when_flow_missing(lab):
    _daily(lab, [_limit_row()])
    _seat(lab, [_seat_row()])
    card = C.build_card("000001.SZ", DAY)
    assert card["dims"]["flow"]["status"] == "unavailable"
    assert card["acceptance"]["verdict"] == "fail"


def test_seat_none_counts_as_known(lab):
    """「涨停但没上龙虎榜」是确认事实，算已知；不能算成未知去刷低已知率。"""
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)
    _seat(lab, [_seat_row("000002.SZ")])      # 有快照，但本票不在
    card = C.build_card("000001.SZ", DAY)
    assert card["dims"]["seat"]["status"] == "none"
    assert card["acceptance"]["verdict"] == "pass"
    assert card["acceptance"]["breakdown"]["none"] == 1


# ── 8. 已知率不得单独用（裁定 C）──────────────────────────
def test_acceptance_declares_primary_metric_and_guard(lab):
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)
    _seat(lab, [_seat_row()])
    ac = C.build_card("000001.SZ", DAY)["acceptance"]
    assert ac["metric"] == "primary_known_rate"
    assert ac["n_total"] == 2, "主维只有两格，分母不是 4"
    assert "不得单独引用" in ac["guard"]
    assert "verdict" in ac


def test_known_rate_alone_would_self_deceive(lab):
    """同一已知率、不同含义：资金有值+席位待补 vs 资金待补+席位有值。

    两者主维已知率都是 50%，只看百分比无法区分「该等」与「该修」——
    这正是裁定 C 说「不能单独用」的原因。verdict/breakdown 能分开。
    """
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)                     # 资金有值、席位待补
    a = C.build_card("000001.SZ", DAY)

    _seat(lab, [_seat_row()])
    os.remove(os.path.join(str(lab["snap"]), "flow_%s.json" % DAY))
    b = C.build_card("000001.SZ", DAY)       # 资金取不到、席位有值

    assert a["acceptance"]["pct"] == b["acceptance"]["pct"] == 50.0
    assert a["acceptance"]["verdict"] == "wait"
    assert b["acceptance"]["verdict"] == "fail"
    assert a["acceptance"]["breakdown"]["pending"] == 1
    assert b["acceptance"]["breakdown"]["unavailable"] == 1


def test_acceptance_summary_counts_verdicts(lab):
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)
    _seat(lab, [_seat_row()])
    cards = [C.build_card("000001.SZ", DAY)]
    _daily(lab, [_limit_row("000005.SZ"), _limit_row("000006.SZ")])
    cards.append(C.build_card("000005.SZ", DAY))          # 有 flow、无 seat → wait
    s = C.acceptance_summary(cards)
    assert s["n"] == 2
    assert s["pass"] + s["wait"] + s["fail"] == s["n"]
    assert "只作参考" in s["guard"]
    assert "pct_pass" in s


def test_acceptance_summary_empty_is_safe():
    s = C.acceptance_summary([])
    assert s["n"] == 0 and s["pct_pass"] == 0.0


# ── 9. 卡面版式：主维在前，附表标注不参与验收 ─────────────
def test_render_puts_primary_before_aux(lab):
    _daily(lab, [_limit_row()])
    _flow(lab, _FLOW_OK)
    _seat(lab, [_seat_row()])
    txt = C.render_markdown(C.build_card("000001.SZ", DAY))
    assert txt.index("**主维**") < txt.index("附表（**不参与验收**")
    assert "不参与验收" in txt
    assert "资金 flow" in txt and "席位 seat" in txt


# ── 10. 盘中：收盘终值按规则未到 → wait，不是 fail ─────────
def _now_at(hhmm: str):
    """DAY 当天的某个时刻。日期全部来自 DAY（码点构造），不写死字面量。"""
    from datetime import datetime
    h, m = [int(x) for x in hhmm.split(":")]
    return datetime(int(DAY[:4]), int(DAY[4:6]), int(DAY[6:]), h, m)


def test_flow_pending_intraday_not_missing(lab):
    """当日 14:00：资金流是收盘终值，还没生成。

    判 `unavailable` 会让每张盘中卡都 fail —— 那是把「精度定义」当成
    「缺数据」，11-16 接盘中链路时会立刻踩到。
    """
    _daily(lab, [_limit_row()])
    d = C._dim_flow(DAY, "000001", now=_now_at("14:00"))
    assert d["status"] == "pending"
    assert "收盘终值" in d["note"]


def test_flow_unavailable_after_close(lab):
    """过了 15:30 还没有 → 真缺数据，必须 unavailable（不许一直 wait 下去）。"""
    _daily(lab, [_limit_row()])
    d = C._dim_flow(DAY, "000001", now=_now_at("16:00"))
    assert d["status"] == "unavailable"


def test_flow_historical_day_never_pending(lab):
    """查历史日一律不当「未到」：即使注入的时间点在同一天 14:00，只要不是当日就是缺。"""
    from datetime import timedelta
    d = C._dim_flow(DAY, "000001", now=_now_at("14:00") + timedelta(days=1))
    assert d["status"] == "unavailable"


def test_intraday_card_verdict_is_wait(lab):
    """盘中两维都按规则未到 → wait。若判 fail，盘中出卡就全是废卡。"""
    _daily(lab, [_limit_row()])
    card = C.build_card("000001.SZ", DAY, now=_now_at("14:00"))
    assert card["dims"]["flow"]["status"] == "pending"
    assert card["dims"]["seat"]["status"] == "pending"
    assert card["acceptance"]["verdict"] == "wait"
