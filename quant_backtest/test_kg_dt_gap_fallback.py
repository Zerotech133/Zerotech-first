# -*- coding: utf-8 -*-
"""CH-07 tushare 兜底接线单测（WO-QT-20261020-067；根因 WO-021 CH-06/07）。

硬边界（工单红线，本文件自证）：
· tushare 调用全部 mock（_post/_sleep/_local/_fetch/_calendar 注入），
  不发起任何真实网络请求；
· 不入真实凭据——token 一律哑串 "dummy-token"，不读不打 .env.local；
· 开关默认关的语义测死：无显式开启时行为与既有产线完全一致。
"""
import json
import os
import sys

import pandas as pd
import pytest

# WO-067 验收修正：以 __file__ 目录入 sys.path（与 cwd/pytest --import-mode
# 解耦）——仓根直跑收集失败即审计发现③同族坑，本文件不再依赖调用方 cwd。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kg_dt_gap as G

TOKEN = "dummy-token"          # 哑串，非真实凭据
ENV_ON = {"KG_DT_GAP_TS_FALLBACK": "1"}
ENV_ON_TOKEN = dict(ENV_ON, TUSHARE_TOKEN=TOKEN)
CAL = ["20261013", "20261014", "20261015", "20261016", "20261017",
       "20261020"]             # 升序交易日历（模拟 tushare 步骤4b 清单）


def _tdx_frame(dates=("20261016",), codes=("000001.SZ",)):
    """构造 TDX ts_top_list 形态的 tl（含 _fetched_at，trade_date int）。"""
    rows = []
    for d in dates:
        for c in codes:
            rows.append({"ts_code": c, "trade_date": int(d), "name": "甲",
                         "close": 10.0, "pct_change": 5.0,
                         "net_amount": 1.0e7, "l_buy": 2.0e7,
                         "l_sell": 1.0e7, "amount": 5.0e8,
                         "reason": "日涨幅偏离", "_fetched_at": "t"})
    return pd.DataFrame(rows)


def _fb_frame(dates=("20261017",), codes=("000002.SZ",)):
    """构造 tushare top_list 形态的兜底行（tushare 多余列在）。"""
    rows = []
    for d in dates:
        for c in codes:
            rows.append({"trade_date": d, "ts_code": c, "name": "乙",
                         "close": 8.0, "pct_change": 9.9,
                         "turnover_rate": 7.0, "amount": 3.0e8,
                         "l_sell": 1.0e6, "l_buy": 9.0e6,
                         "l_amount": 1.0e7, "net_amount": 8.0e6,
                         "net_rate": -2.0, "amount_rate": 11.0,
                         "float_values": 2.0e9, "reason": "日振幅值"})
    return pd.DataFrame(rows)


# ── 开关（默认关是硬边界）──────────────────────────────────────
class TestSwitch:

    def test_default_off(self):
        assert G.ts_fallback_enabled(env={}) is False

    def test_env_off_values(self):
        for v in ("0", "false", "no", "", "off"):
            assert G.ts_fallback_enabled(env={"KG_DT_GAP_TS_FALLBACK": v}) \
                is False

    def test_env_on_values(self):
        for v in ("1", "true", "on", "TRUE"):
            assert G.ts_fallback_enabled(env={"KG_DT_GAP_TS_FALLBACK": v}) \
                is True

    def test_cfg_on(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"dt_gap": {"tushare_fallback": True}}),
                     encoding="utf-8")
        assert G.ts_fallback_enabled(env={}, cfg_path=str(p)) is True

    def test_cfg_off(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"dt_gap": {"tushare_fallback": False}}),
                     encoding="utf-8")
        assert G.ts_fallback_enabled(env={}, cfg_path=str(p)) is False

    def test_stale_n_default_and_env(self):
        assert G.ts_fallback_stale_n(env={}) == G.TS_FALLBACK_STALE_N
        assert G.ts_fallback_stale_n(
            env={"KG_DT_GAP_TS_FALLBACK_N": "3"}) == 3
        assert G.ts_fallback_stale_n(
            env={"KG_DT_GAP_TS_FALLBACK_N": "x"}) == G.TS_FALLBACK_STALE_N


# ── 锚断判定 ──────────────────────────────────────────────────
class TestMissingAnchor:

    def test_stale_beyond_n(self):
        missing, stale = G.missing_anchor_dates(
            {"20261016"}, CAL, stale_n=1)
        assert missing == ["20261017", "20261020"]
        assert stale == 2

    def test_not_triggered_within_n(self):
        missing, stale = G.missing_anchor_dates(
            {"20261016"}, CAL, stale_n=2)
        assert missing == []          # 落后 2 交易日，不 > 2 → 不触发
        assert stale == 2

    def test_empty_tdx_full_missing(self):
        missing, stale = G.missing_anchor_dates(set(), CAL, stale_n=1)
        assert missing == CAL
        assert stale == len(CAL)

    def test_fresh_anchor(self):
        missing, stale = G.missing_anchor_dates(
            {"20261016", "20261017", "20261020"}, CAL, stale_n=1)
        assert missing == []
        assert stale == 0


# ── 本地兜底源（tushare_sync 步骤4b 产物，免网络）─────────────
class TestLocalTopList:

    def test_load_hit_and_miss(self, tmp_path):
        _fb_frame().to_parquet(tmp_path / "d_20261017.parquet", index=False)
        df, got = G.load_local_top_list(["20261017", "20261020"],
                                        root=str(tmp_path))
        assert got == ["20261017"]
        assert len(df) == 1
        assert list(df.columns) == G.TS_TOP_COLS   # 多余列被收窄
        assert df.iloc[0]["ts_code"] == "000002.SZ"

    def test_load_all_miss(self, tmp_path):
        df, got = G.load_local_top_list(["20261017"], root=str(tmp_path))
        assert got == []
        assert df.empty

    def test_calendar_listing(self, tmp_path):
        _fb_frame().to_parquet(tmp_path / "d_20261017.parquet", index=False)
        _fb_frame().to_parquet(tmp_path / "d_20261020.parquet", index=False)
        (tmp_path / "d_junk.parquet").write_bytes(b"x")
        (tmp_path / "other.parquet").write_bytes(b"x")
        assert G._local_calendar(root=str(tmp_path)) == \
            ["20261017", "20261020"]

    def test_calendar_missing_dir(self, tmp_path):
        assert G._local_calendar(root=str(tmp_path / "nope")) == []


# ── 远端拉取（_post/_sleep 全 mock，零真实网络）───────────────
class TestRemoteFetch:

    @staticmethod
    def _ok_resp(d, n=2):
        fb = _fb_frame(dates=(d,))
        items = [[r[c] for c in fb.columns] for _, r in fb.iterrows()]
        return {"code": 0,
                "data": {"fields": list(fb.columns), "items": items}}

    def test_fetch_two_days_with_pacing(self):
        calls, sleeps = [], []

        def post(api, params, token, timeout):
            calls.append((api, params, token))
            r = self._ok_resp(params["trade_date"])
            import types
            return types.SimpleNamespace(json=lambda: r)

        df, got = G.fetch_remote_top_list(
            ["20261017", "20261020"], TOKEN, _post=post,
            _sleep=lambda s: sleeps.append(s))
        assert got == ["20261017", "20261020"]
        assert len(df) == 2 and set(df["trade_date"]) == {"20261017",
                                                          "20261020"}
        # 每日之间一次控速 sleep（沿 tushare_sync.SLEEP=0.35 口径）
        assert sleeps == [G.TS_FALLBACK_SLEEP]
        # api/params/token 走线正确（token 只内存传递）
        assert {c[0] for c in calls} == {"top_list"}
        assert all(c[1]["trade_date"] in ("20261017", "20261020")
                   and c[1]["offset"] == 0 for c in calls)
        assert all(c[2] == TOKEN for c in calls)

    def test_rate_limit_backoff_61s(self):
        sleeps, n = [], {"i": 0}

        def post(api, params, token, timeout):
            n["i"] += 1
            if n["i"] == 1:
                return _resp({"code": 90011, "msg": "抱歉，您每分钟最多访问该接口500次"})
            return _resp(self._ok_resp(params["trade_date"]))

        df, got = G.fetch_remote_top_list(["20261017"], TOKEN, _post=post,
                                          _sleep=sleeps.append)
        assert got == ["20261017"] and len(df) == 1
        assert 61.0 in sleeps            # 限频退避口径沿 ts_call

    def test_hard_error_raises(self):
        def post(api, params, token, timeout):
            return _resp({"code": -1, "msg": "token权限错误"})

        with pytest.raises(RuntimeError):
            G.fetch_remote_top_list(["20261017"], TOKEN, _post=post,
                                    _sleep=lambda s: None)

    def test_missing_fields_day_skipped(self):
        def post(api, params, token, timeout):
            return _resp({"code": 0, "data": {"fields": [],
                                              "items": [[1, 2]]}})

        df, got = G.fetch_remote_top_list(["20261017"], TOKEN, _post=post,
                                          _sleep=lambda s: None)
        assert got == [] and df.empty


def _resp(d):
    import types
    return types.SimpleNamespace(json=lambda: d)


# ── 兜底编排（来源标注 / 显式降级 / 上限）─────────────────────
class TestApplyFallback:

    def test_disabled_untouched(self):
        tl = _tdx_frame()
        tl2, fb, st = G.apply_tushare_fallback(
            tl, verbose=False, env={}, _calendar=lambda: CAL)
        assert st == "disabled" and fb == set() and tl2 is tl

    def test_fresh_untouched(self):
        tl = _tdx_frame(dates=("20261016", "20261017", "20261020"))
        tl2, fb, st = G.apply_tushare_fallback(
            tl, verbose=False, env=ENV_ON_TOKEN, _calendar=lambda: CAL)
        assert st == "fresh" and fb == set() and tl2 is tl

    def test_local_fill_marks_source_keys(self):
        local = (_fb_frame(dates=("20261017", "20261020")),
                 ["20261017", "20261020"])
        tl2, fb, st = G.apply_tushare_fallback(
            _tdx_frame(), verbose=False, env=ENV_ON_TOKEN,
            _local=lambda ds: local, _calendar=lambda: CAL)
        assert st == "ok"
        assert fb == {("000002.SZ", "20261017"), ("000002.SZ", "20261020")}
        assert len(tl2) == 3
        # 缺日 trade_date 统一 str，防下游 min/max 混型
        assert tl2["trade_date"].map(type).eq(str).all()

    def test_no_token_explicit_degradation(self):
        """锚断+本地不足+无凭据 → fallback_unavailable 显式降级，
        不冒充补齐（假绿禁令）。_token 注入空解析（机器上 .env.local
        可能真实存在，须显式模拟无凭据形态）。"""
        local = (_fb_frame(dates=("20261017",)), ["20261017"])

        def _must_not_fetch(ds, tk):
            raise AssertionError("无凭据不得发起远端拉取")

        tl2, fb, st = G.apply_tushare_fallback(
            _tdx_frame(), verbose=False, env=ENV_ON,
            _local=lambda ds: local, _fetch=_must_not_fetch,
            _token=lambda e: "", _calendar=lambda: CAL)
        assert st == "fallback_unavailable" and fb == set()
        assert len(tl2) == 1            # 缺日不补，断口本实保留

    def test_fetch_error_keeps_gap(self):
        local = (pd.DataFrame(columns=G.TS_TOP_COLS), [])

        def _boom(ds, tk):
            raise RuntimeError("network down")

        tl2, fb, st = G.apply_tushare_fallback(
            _tdx_frame(), verbose=False, env=ENV_ON_TOKEN,
            _local=lambda ds: local, _fetch=_boom,
            _calendar=lambda: CAL)
        assert st == "fallback_unavailable" and fb == set()

    def test_no_calendar_degrades(self):
        tl2, fb, st = G.apply_tushare_fallback(
            _tdx_frame(), verbose=False, env=ENV_ON_TOKEN,
            _calendar=lambda: [])
        assert st == "fallback_unavailable" and fb == set()

    def test_missing_days_capped_to_recent_window(self):
        seen = []
        cal = ["202611%02d" % d for d in range(1, 11)]   # 10 个缺日
        local = (pd.DataFrame(columns=G.TS_TOP_COLS), [])

        def _local(ds):
            seen.append(list(ds))
            return local

        G.apply_tushare_fallback(
            _tdx_frame(), verbose=False, env=ENV_ON_TOKEN,
            _local=_local, _fetch=lambda ds, tk: (
                pd.DataFrame(columns=G.TS_TOP_COLS), []),
            _calendar=lambda: cal)
        assert seen[0] == cal[-G.TS_FALLBACK_MAX_DAYS:]  # 只补近 7 日窗

    def test_mixed_local_then_remote(self):
        local = (_fb_frame(dates=("20261017",)), ["20261017"])
        tl2, fb, st = G.apply_tushare_fallback(
            _tdx_frame(), verbose=False, env=ENV_ON_TOKEN,
            _local=lambda ds: local,
            _fetch=lambda ds, tk: (_fb_frame(dates=tuple(ds)), list(ds)),
            _calendar=lambda: CAL)
        assert st == "ok"
        assert ("000002.SZ", "20261020") in fb
        assert len(tl2) == 3


# ── calc 接线：来源标注与 stats（tdxr/兜底全 mock）────────────
TD_COLS = ["ts_code", "trade_date", "close", "open"]


def _fake_read_range(tbl, start=None, end=None, codes=None, columns=None):
    if tbl == "ts_top_list":
        return _tdx_frame(dates=("20261016",),
                          codes=("000001.SZ", "000003.SZ"))
    if tbl == "ts_top_inst":
        # trade_date 为 str：对齐产线 merge 口径（calc:123 已把 tl 转 str）
        return pd.DataFrame({
            "ts_code": ["000001.SZ", "000003.SZ", "000001.SZ"],
            "trade_date": ["20261016", "20261016", "20261016"],
            "exalter": ["机构专用", "华鑫证券有限责任公司上海分公司", "某营业部"],
            "net_buy": [5.0e6, 3.0e6, -1.0e6]})
    if tbl == "ts_daily":
        return pd.DataFrame({
            "ts_code": ["000001.SZ", "000001.SZ", "000003.SZ",
                        "000003.SZ"],
            "trade_date": ["20261016", "20261017", "20261016",
                           "20261017"],
            "close": [10.0, 10.5, 20.0, 19.0],
            "open": [10.1, 10.4, 20.1, 19.2]})
    raise AssertionError(tbl)


class TestCalcIntegration:

    def _patch(self, monkeypatch, fb_dates, fb_status="ok"):
        fb = _fb_frame(dates=tuple(fb_dates)) if fb_dates else \
            pd.DataFrame(columns=G.TS_TOP_COLS)
        fb_keys = set(zip(fb["ts_code"], fb["trade_date"].astype(str))) \
            if len(fb) else set()

        def fake_apply(tl, verbose=True):
            tl2 = tl.copy()
            tl2["trade_date"] = tl2["trade_date"].astype(str)
            if len(fb):
                tl2 = pd.concat([tl2, fb], ignore_index=True)
            return tl2, fb_keys, fb_status

        monkeypatch.setattr(G, "apply_tushare_fallback", fake_apply)
        monkeypatch.setattr(G.tdxr, "read_range", _fake_read_range)
        return fb_keys

    def test_source_labels_true_vs_fallback(self, monkeypatch):
        fb_keys = self._patch(monkeypatch, ["20261017"])
        evs, edges, stats = G.calc(verbose=False)
        src = {e["evt_id"]: e["source"] for e in evs}
        assert src["000002.SZ:topgap:20261017"] == "tushare_fallback"
        assert src["000001.SZ:topgap:20261016"] == "ts_top_list"
        assert src["000003.SZ:topgap:20261016"] == "ts_top_list"
        esrc = {e["dst"]: e["source"] for e in edges}
        assert esrc["000002.SZ:topgap:20261017"] == "tushare_fallback"
        assert "tushare兜底" in stats and "tushare_fallback" in stats
        assert len(fb_keys) == 1

    def test_unavailable_visible_in_stats(self, monkeypatch):
        self._patch(monkeypatch, [], fb_status="fallback_unavailable")
        evs, edges, stats = G.calc(verbose=False)
        assert all(e["source"] == "ts_top_list" for e in evs)
        assert "fallback_unavailable" in stats   # 显式可见，不假绿

    def test_default_off_labels_unchanged(self, monkeypatch):
        """开关默认关：行为与既有产线完全一致（source 全 ts_top_list）。"""
        monkeypatch.delenv("KG_DT_GAP_TS_FALLBACK", raising=False)
        monkeypatch.setattr(G.tdxr, "read_range", _fake_read_range)
        evs, edges, stats = G.calc(verbose=False)
        assert all(e["source"] == "ts_top_list" for e in evs + edges)
        assert "tushare" not in stats

    def test_empty_tdx_exit_text_mentions_fallback(self, monkeypatch):
        def empty_read(tbl, start=None, end=None, codes=None,
                       columns=None):
            if tbl == "ts_top_list":
                return pd.DataFrame()
            raise AssertionError(tbl)

        monkeypatch.delenv("KG_DT_GAP_TS_FALLBACK", raising=False)
        monkeypatch.setattr(G.tdxr, "read_range", empty_read)
        with pytest.raises(SystemExit) as ei:
            G.calc(verbose=False)
        assert "KG_DT_GAP_TS_FALLBACK" in str(ei.value)
