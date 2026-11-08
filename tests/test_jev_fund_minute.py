# -*- coding: utf-8 -*-
"""Step 5 第三刀（xtcloud 接入）回归 —— 分钟兜底 / 资金流 / 披露。

覆盖 2026-11-08 实测踩到的四个具体缺陷：
  ① `_minute_read` 必须把 local / remote / fail 三档分开报，不能只给一个总数
     （只报总数会掩盖「远端兜底失效」——11-04 实测 45 只只命中 6 只）
  ② `_focus_flow.divergence` = 主力净买但全档净流出（大单接、小单跑）
     不能写成「主力为负数就是虚」——主力正/全档负才是最该警示的组合
  ③ `XtCloud.mins` 的时间列名，ts_stk_mins 叫 trade_time、rt_min 叫 time，
     写死一个就换表即崩；且日期过滤在服务端失效，必须本地按前缀筛
  ④ 列序必须对齐本地 parquet（close 在 open 前），错位会让 OHLC 全错
"""
from __future__ import annotations

import os
import sys

import pytest

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(STATION, "portal"), os.path.join(STATION, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import jev_graph as G          # noqa: E402
import jev_flow as F           # noqa: E402


# ── ① 分钟台账 ──────────────────────────────────────────────
def test_minute_read_splits_three_sources():
    profiles = {
        "a": {"ok": True, "source": "local"},
        "b": {"ok": True, "source": "remote"},
        "c": {"ok": True, "source": "remote"},
        "d": {"ok": False, "reason": "no_minute", "source": None},
    }
    r = G._minute_read(profiles, 4)
    assert r["asked"] == 4
    assert r["ok"] == 3
    assert r["local"] == 1 and r["remote"] == 2 and r["fail"] == 1


def test_minute_read_empty_is_zero_not_crash():
    r = G._minute_read({}, 0)
    assert r == {"asked": 0, "ok": 0, "local": 0, "remote": 0, "partial": 0,
                 "fail": 0,
                 "fallback": os.environ.get("JEV_MINS_FALLBACK", "on")}


def test_minute_read_marks_rt_min_as_partial():
    """rt_min 只有 237 根（缺 15:00 收盘那根）：算进 remote，但必须单列
    partial，不能混在 remote 里冒充「完整命中」。"""
    profiles = {
        "a": {"ok": True, "source": "local"},
        "b": {"ok": True, "source": "remote", "tbl": "ts_stk_mins"},
        "c": {"ok": True, "source": "remote", "tbl": "ts_rt_min"},
    }
    r = G._minute_read(profiles, 3)
    assert r["remote"] == 2 and r["partial"] == 1


# ── ② 资金面背离 ────────────────────────────────────────────
def _row(code, board, pct, main_net, net_mf):
    return {"code": code, "c6": code[:6], "name": "N" + code[:3], "board": board,
            "pct": pct, "main_net": main_net, "net_mf": net_mf}


def test_focus_flow_divergence_is_main_in_but_all_out():
    members = [
        _row("600001.SH", "电力", 10.0, 5000.0, -3000.0),   # 背离：大单买/全档卖
        _row("600002.SH", "电力", 9.9, 4000.0, 2000.0),     # 正常流入
        _row("600003.SH", "电力", -2.0, -1000.0, -800.0),   # 净流出
        _row("600004.SH", "电力", 1.0, 100.0, 50.0),
        _row("600005.SH", "电力", 0.0, None, None),         # 无资金流数据
    ]
    flow = {m["c6"]: {"main_net": m["main_net"], "net_mf": m["net_mf"]}
            for m in members if m["main_net"] is not None}
    r = G._focus_flow("电力", members, flow, {"电力": {"main_net": 8100.0}})
    assert r["has"] is True
    assert r["n_with_flow"] == 4          # 无数据那只不算
    assert r["n_inflow"] == 3             # main_net > 0
    assert r["divergence"] == 1           # 只有第一只满足 主力>0 且 全档<0
    assert r["divergence_names"] == ["N600"]
    assert r["top_in"][0]["main_net"] == 5000.0
    assert r["board_main_net"] == 8100.0


def test_focus_flow_without_flow_says_so():
    r = G._focus_flow("电力", [], {}, {})
    assert r["has"] is False
    assert "资金流" in r["note"]


def test_focus_flow_top_out_only_negative():
    members = [_row("600001.SH", "电力", 1.0, 100.0, 50.0),
               _row("600002.SH", "电力", 1.0, -500.0, -400.0)]
    flow = {m["c6"]: {"main_net": m["main_net"], "net_mf": m["net_mf"]}
            for m in members}
    r = G._focus_flow("电力", members, flow, {})
    assert [x["name"] for x in r["top_out"]] == ["N600"]  # 正数不该进流出榜


# ── ③ 披露不含盘中时刻 ──────────────────────────────────────
def test_focus_disc_has_no_clock_time():
    disc = {"600001": {"reports": [{"title": "三季报点评", "rating": "买入",
                                    "org": "西南证券", "eps": "0.28",
                                    "pe": "16.0", "industry": "电力",
                                    "date": "2026-11-06", "url": "u"}],
                       "notices": [{"title": "关于董事辞职的公告", "type": "高管变动",
                                    "date": "2026-11-06", "url": "u"}]}}
    members = [{"code": "600001.SH", "c6": "600001", "name": "甲"}]
    r = G._focus_disc(members, disc, top=4)
    assert r["n_reports"] == 1 and r["n_notices"] == 1
    assert r["items"][0]["kind"] == "research"     # 研报排前面
    for it in r["items"]:
        assert "hm" not in it and "lag_min" not in it   # 结构上就进不了 Δmin


def test_focus_disc_empty_is_safe():
    r = G._focus_disc([], {}, top=4)
    assert r["has"] is False and r["items"] == []


# ── ④ xtcloud 客户端：列序 / 时间列 / 日期筛 ────────────────
class _FakeClient:
    """替掉 XtCloud.table，验证解析层而不碰网络。

    必须按 code 过滤——真实服务是按 code 查的，若 fake 对任何 code 都回同一批
    行，就测不出「这只票当日没有数据」这一档（坑 4）。
    """

    def __init__(self, cols, rows):
        self._cols = cols
        self._rows = rows

    def table(self, tbl, **kw):
        code = kw.get("code")
        rs = self._rows
        if code:
            rs = [r for r in self._rows if str(r[0])[:6] == str(code)[:6]]
        return {"columns": self._cols, "rows": rs, "total": len(rs)}


def _client(cols, rows):
    import xtcloud_client as X
    c = X.XtCloud()
    fake = _FakeClient(cols, rows)
    c.table = fake.table          # type: ignore[assignment]
    return c


def test_mins_column_order_matches_local_parquet():
    """xtcloud 返回 close 在 open 之后；本地 parquet 是 close 在前。"""
    import xtcloud_client as X
    cols = ["ts_code", "trade_time", "freq", "open", "high", "low", "close",
            "vol", "amount", "_fetched_at"]
    rows = [["600001.SH", "2026-11-06 09:31:00", "1min", 10.0, 10.5, 9.9, 10.4,
             1000.0, 10400.0, "x"]]
    c = _client(cols, rows)
    out = c.mins("600001", "20261106")
    assert len(out) == 1
    rec = out[0]
    assert rec[0] == "600001.SH" and rec[1] == "2026-11-06 09:31:00"
    assert rec[2] == 10.4      # close
    assert rec[3] == 10.0      # open
    assert rec[4] == 10.5 and rec[5] == 9.9
    assert len(rec) == len(X.MINS_COLS) == 8


def test_mins_time_column_alias_and_date_filter():
    """rt_min 的时间列叫 time；且服务端日期过滤失效，必须本地筛。"""
    cols = ["ts_code", "time", "open", "close", "high", "low", "vol", "amount"]
    rows = [
        ["600001.SH", "2026-11-06 14:59:00", 10.0, 10.1, 10.2, 9.9, 100.0, 1010.0],
        ["600001.SH", "2026-11-05 15:00:00", 9.8, 9.9, 10.0, 9.7, 200.0, 1980.0],
        ["600001.SH", "2026-11-06 09:30:00", 9.9, 10.0, 10.1, 9.8, 300.0, 3000.0],
    ]
    c = _client(cols, rows)
    out = c.mins("600001", "20261106", tbl="ts_rt_min")
    assert [r[1][11:16] for r in out] == ["09:30", "14:59"]   # 只留当日 + 升序


def test_mins_rejects_unknown_schema():
    import xtcloud_client as X
    c = _client(["foo", "bar"], [["a", "b"]])
    with pytest.raises(X.XtCloudError):
        c.mins("600001", "20261106")


def test_mins_many_drops_uncovered_codes():
    """坑 4：有的票当日 0 行，必须当「没有」而不是抛异常。"""
    cols = ["ts_code", "trade_time", "freq", "open", "high", "low", "close",
            "vol", "amount"]
    rows = [["600001.SH", "2026-11-06 09:30:00", "1min", 1.0, 1.1, 0.9, 1.05,
             10.0, 10.5]]
    c = _client(cols, rows)
    got = c.mins_many(["600001", "600002"], "20261106", workers=2)
    assert list(got) == ["600001"]


# ── ⑤ 首次异动：一字板不能再判 None ─────────────────────────
def test_first_move_at_open_when_gap_exceeds_threshold(monkeypatch):
    """跳空高开/一字板：open 相对昨收已 ≥0.8% → 首次异动记开盘时刻。

    旧实现拿 open 当基准，(c/open-1) 恒为 0 → first_move 永远 None
    （实测 11-06 南侨食品/永臻股份/浙文影业三只被漏）。
    """
    import pandas as pd
    d = pd.DataFrame({
        "ts_code": ["600001.SH"] * 3,
        "trade_time": ["2026-11-06 09:30:00", "2026-11-06 09:31:00",
                       "2026-11-06 09:32:00"],
        "close": [11.0, 11.0, 11.0], "open": [11.0, 11.0, 11.0],
        "high": [11.0] * 3, "low": [11.0] * 3,
        "vol": [100.0, 0.0, 0.0], "amount": [1.0, 0.0, 0.0],
    })
    d.attrs["source"] = "remote"
    monkeypatch.setattr(G, "_mins", lambda code, day: d)
    p = G.minute_profile("600001.SH", "20261106", limit_price=11.0,
                         pre_close=10.0)
    assert p["ok"] is True
    assert p["at_open"] is True
    assert p["first_move"] == "2026-11-06 09:30:00"
    assert p["gap_pct"] == 10.0
    assert p["seal_time"] == "2026-11-06 09:30:00"
    assert p["source"] == "remote"


def test_first_move_relative_to_open_when_flat_gap(monkeypatch):
    import pandas as pd
    d = pd.DataFrame({
        "ts_code": ["600001.SH"] * 3,
        "trade_time": ["2026-11-06 09:30:00", "2026-11-06 09:31:00",
                       "2026-11-06 09:32:00"],
        "close": [10.0, 10.0, 10.2], "open": [10.0, 10.0, 10.0],
        "high": [10.0, 10.0, 10.2], "low": [10.0] * 3,
        "vol": [100.0, 50.0, 200.0], "amount": [1.0, 0.5, 2.0],
    })
    d.attrs["source"] = "local"
    monkeypatch.setattr(G, "_mins", lambda code, day: d)
    p = G.minute_profile("600001.SH", "20261106", pre_close=10.0)
    assert p["at_open"] is False
    assert p["first_move"] == "2026-11-06 09:32:00"


# ── ⑥ 资金流取数（不联网，替掉客户端） ──────────────────────
def test_load_flow_computes_main_net(monkeypatch):
    cols = ["ts_code", "trade_date", "buy_lg_amount", "sell_lg_amount",
            "buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
    rows = [["600001.SH", "20261106", 100.0, 40.0, 60.0, 20.0, 50.0]]
    c = _client(cols, rows)
    monkeypatch.setattr(F, "_xt", lambda: c)
    # 必须关磁盘快照：load_flow 现在读盘优先，盘上有当日真实快照时
    # 会绕开 _xt 替身，直接拿到 5579 只真实数据（用例就查不到 600001）。
    monkeypatch.setenv("JEV_FLOW_SNAP", "0")
    F._CACHE.clear()
    flow = F.load_flow("20261106")
    assert flow["600001"]["main_net"] == 100.0     # (100+60)-(40+20)
    assert flow["600001"]["net_mf"] == 50.0
    assert F.flow_of(flow, "600001.SH")["has"] is True
    assert F.flow_of(flow, "999999")["has"] is False



# ── ⑦ 2026-11-08 代码审查修掉的缺陷（每条都对应一个真实后果） ────────
def test_writeback_refuses_partial_rt_min(monkeypatch):
    """rt_min 缺 15:00 那根，落盘会把「237/241 根」固化成「已补齐」。

    merge_write 的幂等挡板是「目标日已有 >=200 根就跳过」，237 根过线，
    于是之后再也不会去补收盘那根——尾盘才封板的票会被判成没封板。
    """
    import jev_mins_fetch as JF
    calls = []
    monkeypatch.setattr(JF, "merge_write",
                        lambda *a, **k: calls.append(a) or ("merged", 1))
    monkeypatch.delenv("JEV_MINS_WRITE", raising=False)
    rows = [["600001.SH", "2026-11-06 09:30:00", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]]
    assert G._writeback("600001.SH", "20261106", rows, tbl="ts_rt_min") is False
    assert calls == []
    assert G._writeback("600001.SH", "20261106", rows,
                        tbl="ts_stk_mins") is True
    assert len(calls) == 1


def test_minute_negative_cache_expires(monkeypatch):
    """远端抖一次就永久记「没分钟」= 页面一直骗人到进程重启。"""
    import time

    import pandas as pd
    key = ("600001.SH", "20261106")
    G._MINS_MEM.clear()
    G._MINS_FAIL.clear()
    d = pd.DataFrame({"trade_time": ["2026-11-06 09:30:00"], "close": [1.0],
                      "open": [1.0], "high": [1.0], "low": [1.0],
                      "vol": [1.0], "amount": [1.0]})

    def _boom(*a, **k):
        raise AssertionError("负缓存没过期就重试了")

    G._MINS_MEM[key] = None
    G._MINS_FAIL[key] = time.time()
    monkeypatch.setattr(G, "_mins_local", _boom)
    monkeypatch.setattr(G, "_mins_fetch", _boom)
    assert G._mins(*key) is None

    G._MINS_FAIL[key] = time.time() - G.NEG_TTL_S - 1
    monkeypatch.setattr(G, "_mins_local", lambda c, day: d)
    got = G._mins(*key)
    assert got is d and got.attrs["source"] == "local"


def test_mins_accepts_hyphen_day():
    """旧实现直接切 day[:4]/[4:6]/[6:]，传 2026-11-06 会拼出
    "2026--1-06" 然后静默返回空列表。"""
    cols = ["ts_code", "trade_time", "freq", "open", "high", "low", "close",
            "vol", "amount"]
    rows = [["600001.SH", "2026-11-06 09:30:00", "1min", 1.0, 1.1, 0.9, 1.05,
             10.0, 10.5]]
    c = _client(cols, rows)
    assert len(c.mins("600001", "2026-11-06")) == 1
    assert len(c.mins("600001", "2026/11/06")) == 1


def test_mins_bad_day_raises_instead_of_silent_empty():
    import xtcloud_client as X
    c = _client(["ts_code", "trade_time", "open", "high", "low", "close"], [])
    with pytest.raises(X.XtCloudError):
        c.mins("600001", "2026-11")


def test_mins_requires_ohlc_but_tolerates_missing_vol():
    import xtcloud_client as X
    # 缺 high -> 抛（拿 0 当高价会静默毁掉「最大单分钟涨幅」）
    c = _client(["ts_code", "trade_time", "open", "low", "close"], [])
    with pytest.raises(X.XtCloudError):
        c.mins("600001", "20261106")
    # 缺量额 -> 降级 0，不抛
    cols = ["ts_code", "trade_time", "open", "high", "low", "close"]
    rows = [["600001.SH", "2026-11-06 09:30:00", 1.0, 1.1, 0.9, 1.05]]
    out = _client(cols, rows).mins("600001", "20261106")
    assert out[0][2] == 1.05 and out[0][6] == 0.0 and out[0][7] == 0.0


def test_moneyflow_actually_filters_by_day():
    """旧实现收了 day 却没用：传哪天都返回最前面一屏，日期错了不报错。"""
    import xtcloud_client as X
    seen = {}
    c = X.XtCloud()

    def _rows(tbl, **kw):
        seen.update(dict(kw, tbl=tbl))
        return []

    c.rows = _rows          # type: ignore[assignment]
    c.moneyflow("2026-11-06")
    assert seen["tbl"] == "ts_moneyflow"
    assert seen["start_date"] == "20261106" == seen["end_date"]


def test_focus_disc_newest_first():
    """同 kind 内按日期升序 = 一屏全是几个月前的旧报告。"""
    def _rep(title, date):
        return {"title": title, "rating": "买入", "org": "o", "eps": "",
                "pe": "", "industry": "", "date": date, "url": ""}

    disc = {"600001": {"reports": [_rep("老", "2026-08-01"),
                                   _rep("新", "2026-11-06")],
                       "notices": []}}
    members = [{"code": "600001.SH", "c6": "600001", "name": "甲"}]
    r = G._focus_disc(members, disc, top=4)
    assert [x["title"] for x in r["items"]] == ["新", "老"]


def test_timeline_fills_quota_when_prices_are_few():
    """价格动作只有 2 条时，剩下 5 个位置不该空着（配额还给消息）。"""
    members = [{"code": "600001.SH", "c6": "600001", "name": "甲", "pct": 10.0}]
    profiles = {"600001.SH": {"ok": True, "source": "local",
                              "first_move": "2026-11-06 09:30:00",
                              "first_move_pct": 10.0, "at_open": True,
                              "seal_time": "2026-11-06 09:30:00"}}
    ev = {"600001": [{"ts": "2026-11-06 10:%02d:00" % i, "hm": "10:%02d" % i,
                      "ts_kind": "intraday", "source": "财联社",
                      "title": "t%d" % i, "bias": "pos",
                      "origin_kind": "news"} for i in range(20)]}
    tl = G._timeline(members, ev, profiles, limit=14)
    assert len(tl) == 14
    assert sum(1 for x in tl if x["kind"] == "news") == 12


def test_fetch_auto_falls_back_when_xtcloud_returns_empty(monkeypatch):
    """--source auto 的语义是「拿不到就退 tushare」，空列表也算拿不到。"""
    import jev_mins_fetch as JF
    called = {"ts": 0, "merge": 0}
    monkeypatch.setattr(JF, "log", lambda *a, **k: None)
    monkeypatch.setattr(G, "resolve_day", lambda d=None: "20261106")
    monkeypatch.setattr(JF, "pick_codes",
                        lambda day, limit: ([("600001.SH", "甲", 10.0, "涨停")],
                                            "电力"))
    monkeypatch.setattr(JF, "token", lambda: "tok")
    monkeypatch.setattr(JF, "xt_call", lambda c, day: [])
    monkeypatch.setattr(JF, "SLEEP", 0)

    def _ts(tok, code, day):
        called["ts"] += 1
        return [["600001.SH", "2026-11-06 09:30:00",
                 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]]

    def _mw(code, day, items, force=False):
        called["merge"] += 1
        return "merged", len(items)

    monkeypatch.setattr(JF, "ts_call", _ts)
    monkeypatch.setattr(JF, "merge_write", _mw)
    monkeypatch.setattr(sys, "argv", ["jev_mins_fetch.py", "--day", "20261106"])
    assert JF.main() == 0
    assert called["ts"] == 1, "xtcloud 返回空时没回落 tushare"
    assert called["merge"] == 1


def test_minute_mem_is_capped(monkeypatch):
    """8091 是常驻进程：不设上限，一天 800 只 × 20 个交易日 = 1.6 万个
    DataFrame（每个 ~20KB）会一路涨到几百 MB，只能靠重启回收。"""
    monkeypatch.setenv("JEV_MINS_MEM_MAX", "3")
    G._MINS_MEM.clear()
    G._MINS_FAIL.clear()
    for i in range(5):
        G._mem_put(("60000%d.SH" % i, "20261106"), {"i": i})
    assert len(G._MINS_MEM) == 3
    # 淘汰的是最早插入的三个，留下最后三个
    assert [k[0] for k in G._MINS_MEM] == ["600002.SH", "600003.SH", "600004.SH"]


def test_minute_mem_evict_clears_fail_stamp(monkeypatch):
    """淘汰时失败戳要一起清，否则 _MINS_FAIL 自己会单独涨成一张大表。"""
    monkeypatch.setenv("JEV_MINS_MEM_MAX", "1")
    G._MINS_MEM.clear()
    G._MINS_FAIL.clear()
    G._mark_none("600001.SH", "20261106")
    assert ("600001.SH", "20261106") in G._MINS_FAIL
    G._mem_put(("600002.SH", "20261106"), {"i": 2})
    assert ("600001.SH", "20261106") not in G._MINS_FAIL


# ── ⑧ 资金流磁盘快照（2026-11-08 加）────────────────────────
def test_flow_snapshot_roundtrip(tmp_path, monkeypatch):
    """日终数据落盘一次，之后读盘 ~0.02s（冷启动是 60s+）。"""
    monkeypatch.setattr(F, "SNAP_DIR", str(tmp_path))
    monkeypatch.delenv("JEV_FLOW_SNAP", raising=False)
    assert F._snap_read("flow", "20261106") is None
    F._snap_write("flow", "20261106", {"600001": {"main_net": 1.0}})
    assert F._snap_read("flow", "20261106") == {"600001": {"main_net": 1.0}}


def test_flow_snapshot_broken_file_degrades(tmp_path, monkeypatch):
    """快照坏了必须当「没有」，不能抛 —— 否则一个半截 json 能把整页锁死。"""
    monkeypatch.setattr(F, "SNAP_DIR", str(tmp_path))
    monkeypatch.delenv("JEV_FLOW_SNAP", raising=False)
    (tmp_path / "flow_20261106.json").write_text("{not json", encoding="utf-8")
    assert F._snap_read("flow", "20261106") is None


def test_flow_snapshot_can_be_disabled(tmp_path, monkeypatch):
    """JEV_FLOW_SNAP=0 时不读也不写（测试与排障用）。"""
    monkeypatch.setattr(F, "SNAP_DIR", str(tmp_path))
    monkeypatch.setenv("JEV_FLOW_SNAP", "0")
    F._snap_write("flow", "20261106", {"600001": {"main_net": 1.0}})
    assert not (tmp_path / "flow_20261106.json").exists()
    assert F._snap_read("flow", "20261106") is None


# ── ⑧ 资金流磁盘快照（2026-11-08 加）────────────────────────
def test_flow_snapshot_roundtrip(tmp_path, monkeypatch):
    """日终数据落盘一次，之后读盘 ~0.02s（冷启动是 60s+）。"""
    monkeypatch.setattr(F, "SNAP_DIR", str(tmp_path))
    monkeypatch.delenv("JEV_FLOW_SNAP", raising=False)
    assert F._snap_read("flow", "20261106") is None
    F._snap_write("flow", "20261106", {"600001": {"main_net": 1.0}})
    assert F._snap_read("flow", "20261106") == {"600001": {"main_net": 1.0}}


def test_flow_snapshot_broken_file_degrades(tmp_path, monkeypatch):
    """快照坏了必须当「没有」，不能抛 —— 否则一个半截 json 能把整页锁死。"""
    monkeypatch.setattr(F, "SNAP_DIR", str(tmp_path))
    monkeypatch.delenv("JEV_FLOW_SNAP", raising=False)
    (tmp_path / "flow_20261106.json").write_text("{not json", encoding="utf-8")
    assert F._snap_read("flow", "20261106") is None


def test_flow_snapshot_can_be_disabled(tmp_path, monkeypatch):
    """JEV_FLOW_SNAP=0 时不读也不写（测试与排障用）。"""
    monkeypatch.setattr(F, "SNAP_DIR", str(tmp_path))
    monkeypatch.setenv("JEV_FLOW_SNAP", "0")
    F._snap_write("flow", "20261106", {"600001": {"main_net": 1.0}})
    assert not (tmp_path / "flow_20261106.json").exists()
    assert F._snap_read("flow", "20261106") is None
