# -*- coding: utf-8 -*-
"""P4 共振 v4 基建测试：resonance_store 入库器 + resonance_bridge 治理接线。

覆盖（工单口径）：
  1) rebuild 幂等（两次灌库行数不变，runs 留痕 +2）；
  2) backfill：可回填行算对（锚定 ≥事件日的首个交易日）、数据未到行保持 NULL；
  3) stats 正确（n_days/n_events/n_clean/mean_gap/sigma_gap/events_fwd_filled）；
  4) update_day/update_event 增量接口（NULL 不覆盖旧值）；
  5) bridge 产出 Observation（payload 带 stats；源未登记跳过 update_health 且如实说明）。

夹具：小 v3 JSON + 小 000905 parquet（手工可算的收盘价），不碰真库。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
INTEL = os.path.normpath(os.path.join(HERE, "..", "..", "intel_collector"))
for p in (os.path.join(HERE, "..", "tools"), HERE, os.path.join(HERE, ".."), INTEL):
    if p not in sys.path:
        sys.path.insert(0, p)

import resonance_store as rs

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

# ---- 小夹具数据（收盘价手工可算）----
# parquet 交易日/收盘：0101=100, 0102=110, 0105=120, 0106=90, 0107=105, 0108=105, 0109=105, 0112=105
IDX_ROWS = [
    ("20260101", 100.0), ("20260102", 110.0), ("20260105", 120.0),
    ("20260106", 90.0), ("20260107", 105.0), ("20260108", 105.0),
    ("20260109", 105.0), ("20260112", 105.0),
]
# 事件 A：event_date=20260101 → 锚 0101(i=0)：fwd1=110/100-1=+10%，fwd3=90/100-1=-10%，fwd5=105/100-1=+5%
# 事件 B：event_date=20260109 → 锚 0109(i=6)：fwd1=0%，fwd3 需 i=9 超出 → 整行 NULL pending
V3_JSON = {
    "meta": {"window": "2026-01-01 ~ 2026-01-09"},
    "gap_stats": {"all": {"n_days": 2, "mean": -0.25, "stdev": 0.75,
                          "min": -1.0, "max": 0.5, "n_noise": 1}},
    "events": [
        {"date": "2026-01-01", "prev_date": "2025-12-31", "prev_gap": 0.5,
         "prev_z": 1.0, "gap": -1.0, "z": -1.0, "pullback": 1.5,
         "noise": 0, "xq_n": 10, "jev_n": 4},
        {"date": "2026-01-09", "prev_date": "2026-01-08", "prev_gap": 0.5,
         "prev_z": 1.0, "gap": -0.5, "z": -0.33, "pullback": 1.0,
         "noise": 1, "xq_n": 2, "jev_n": 2},
    ],
    "series": [
        {"date": "2026-01-01", "is_trade_day": True, "gap": -1.0, "z_full": -1.0,
         "z_roll": None, "extreme": True, "noise": False, "xq_n": 10, "jev_n": 4,
         "xq_bull": 0.2, "jev_bull": 1.0, "pct_chg": 1.0,
         "var": {"pct_chg": 1.0, "var_p80": True, "var_1pct": True,
                 "var_1_5pct": False, "flip": False}},
        {"date": "2026-01-02", "is_trade_day": True, "gap": 0.5, "z_full": 1.0,
         "z_roll": None, "extreme": True, "noise": True, "xq_n": 3, "jev_n": 2,
         "xq_bull": 0.9, "jev_bull": 0.4, "pct_chg": -0.5,
         "var": {"pct_chg": -0.5, "var_p80": False, "var_1pct": False,
                 "var_1_5pct": False, "flip": True}},
        {"date": "2026-01-03", "is_trade_day": False, "gap": None, "z_full": None,
         "z_roll": None, "extreme": False, "noise": None, "xq_n": 0, "jev_n": 0,
         "xq_bull": None, "jev_bull": None, "pct_chg": None},
    ],
}


@pytest.fixture()
def setup(tmp_path):
    jp = tmp_path / "v3.json"
    jp.write_text(json.dumps(V3_JSON, ensure_ascii=False), encoding="utf-8")
    pp = tmp_path / "idx.parquet"
    import pandas as pd
    pd.DataFrame({
        "ts_code": ["000905.SZ"] * len(IDX_ROWS),
        "trade_date": [r[0] for r in IDX_ROWS],
        "close": [r[1] for r in IDX_ROWS],
        "pct_chg": [0.0] * len(IDX_ROWS),
    }).to_parquet(str(pp))
    db = str(tmp_path / "res.db")
    return str(jp), str(pp), db


class TestRebuild:
    def test_rebuild_idempotent(self, setup):
        jp, pp, db = setup
        r1 = rs.rebuild_from_v3(jp, db)
        assert r1["n_days"] == 3 and r1["n_events"] == 2
        r2 = rs.rebuild_from_v3(jp, db)
        assert r2["n_days"] == 3 and r2["n_events"] == 2
        con = sqlite3.connect(db)
        try:
            assert con.execute("SELECT COUNT(*) FROM gap_daily").fetchone()[0] == 3
            assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
            assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2  # 每次留痕
            # 布尔口径落 INT
            row = con.execute("SELECT is_trade_day, extreme, noise, var FROM gap_daily "
                              "WHERE date='2026-01-02'").fetchone()
            assert row == (1, 1, 1, 0)
            # fwd* 初始 NULL（统一归 backfill 管）
            assert con.execute("SELECT fwd1, fwd3, fwd5 FROM events "
                               "WHERE event_date='2026-01-01'").fetchone() == (None, None, None)
        finally:
            con.close()

    def test_rebuild_missing_json(self, setup):
        with pytest.raises(FileNotFoundError):
            rs.rebuild_from_v3("Z:/nope.json", setup[2])


class TestBackfill:
    def test_backfill_fill_and_pending(self, setup):
        jp, pp, db = setup
        rs.rebuild_from_v3(jp, db)
        r = rs.backfill_fwd(pp, db)
        filled = {f["event_date"]: f for f in r["filled"]}
        pending = {p["event_date"]: p for p in r["pending"]}
        # 事件 A 全窗可回填：手工值核对
        a = filled["2026-01-01"]
        assert a["anchor"] == "2026-01-01"
        assert a["fwd1"] == pytest.approx(10.0)      # 110/100-1
        assert a["fwd3"] == pytest.approx(-10.0)     # 90/100-1
        assert a["fwd5"] == pytest.approx(5.0)       # 105/100-1
        # 事件 B fwd3 越界 → 整行保持 NULL pending
        assert "2026-01-09" in pending
        con = sqlite3.connect(db)
        try:
            row = con.execute("SELECT fwd1, fwd3, fwd5 FROM events "
                              "WHERE event_date='2026-01-09'").fetchone()
            assert row == (None, None, None)
        finally:
            con.close()
        # 再跑一遍：A 已非 NULL 不再选，pending B 保留
        r2 = rs.backfill_fwd(pp, db)
        assert r2["filled"] == [] and len(r2["pending"]) == 1

    def test_backfill_null_kept_when_data_not_ready(self, setup):
        """parquet 追加新交易日后，pending 行下次可回填（增量自然增长语义）。"""
        import pandas as pd
        jp, pp, db = setup
        rs.rebuild_from_v3(jp, db)
        rs.backfill_fwd(pp, db)
        # parquet 延长 4 日：0109(i=6) 的 fwd3 需要 i=9
        rows = list(IDX_ROWS) + [("20260113", 99.0), ("20260114", 99.0),
                                 ("20260115", 99.0), ("20260116", 99.0)]
        pd.DataFrame({
            "ts_code": ["000905.SZ"] * len(rows),
            "trade_date": [r[0] for r in rows],
            "close": [r[1] for r in rows],
            "pct_chg": [0.0] * len(rows),
        }).to_parquet(pp)
        r = rs.backfill_fwd(pp, db)
        filled = {f["event_date"]: f for f in r["filled"]}
        b = filled["2026-01-09"]
        assert b["fwd1"] == pytest.approx(0.0)        # 105/105-1
        assert b["fwd3"] == pytest.approx((99.0 / 105.0 - 1.0) * 100)  # 0114 close
        assert b["fwd5"] == pytest.approx((99.0 / 105.0 - 1.0) * 100)


class TestStats:
    def test_stats_correct(self, setup):
        jp, pp, db = setup
        rs.rebuild_from_v3(jp, db)
        s = rs.stats(db)
        assert s["n_days"] == 3
        assert s["n_events"] == 2
        assert s["n_clean"] == 1            # noise=0 且 gap 非 NULL：仅 01-01
        assert s["mean_gap"] == pytest.approx(-0.25)   # (-1.0+0.5)/2
        assert s["sigma_gap"] == pytest.approx(1.0606601717798212, rel=1e-6)
        assert s["events_fwd_filled"] == {"any": 0, "all": 0}
        rs.backfill_fwd(pp, db)
        s2 = rs.stats(db)
        assert s2["events_fwd_filled"] == {"any": 1, "all": 1}   # 只 A 全填


class TestIncrementalApi:
    def test_update_day_event_no_clobber(self, setup):
        jp, pp, db = setup
        rs.rebuild_from_v3(jp, db)
        # update_day：None 字段不覆盖已有值
        rs.update_day("2026-01-02", gap=0.123, xq_n=None, db_path=db)
        con = sqlite3.connect(db)
        try:
            row = con.execute("SELECT gap, xq_n FROM gap_daily WHERE date='2026-01-02'").fetchone()
            assert row == (0.123, 3)      # xq_n 保留旧值
        finally:
            con.close()
        # update_event：新事件插入、已有事件不覆盖 fwd*
        rs.update_event("2026-02-01", prev_date="2026-01-31", gap=0.1,
                        pullback=0.9, noise=False, db_path=db)
        con = sqlite3.connect(db)
        try:
            assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
            row = con.execute("SELECT fwd1 FROM events WHERE event_date='2026-02-01'").fetchone()
            assert row == (None,)
        finally:
            con.close()


class TestBridge:
    def _make(self, tmp_path, setup, with_source=False):
        jp, pp, db = setup
        reg_path = tmp_path / "sources.json"
        sources = {}
        if with_source:
            sources["resonance_store"] = {
                "source_id": "resonance_store", "name": "共振事件库",
                "category": "open_source_intel", "owner": "intel(军情司·INTEL0)",
                "schema_version": "1.0",
                "rights": {"internal_retention": True, "cleaning": True,
                           "controlled_query": True},
                "rights_meta": {}, "content_types": ["meta"],
                "sla": {"freshness_minutes": 1440, "max_failures": 5},
                "enabled": True, "health": {},
            }
        reg_path.write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
        return jp, pp, db, str(reg_path)

    def test_bridge_produces_observation(self, tmp_path, setup):
        from bridges.resonance_bridge import ResonanceStoreDispatcher, SOURCE_ID
        from registry.source_registry import SourceRegistry
        from store.obs_store import ObservationStore
        from core.contracts import Status

        jp, pp, db, reg_path = self._make(tmp_path, setup, with_source=False)
        rs.rebuild_from_v3(jp, db)   # 日常流：库已有数据，bridge 只跑 backfill+stats
        reg = SourceRegistry(reg_path)
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        d = ResonanceStoreDispatcher(reg, obs_store, db_path=db, index_parquet=pp)
        res = d.collect_source(SOURCE_ID, NOW)
        assert res["ok"] is True
        assert res["n"] == 1                      # 只填事件 A
        assert "源未登记" in res["reason"]          # 如实说明跳过 update_health
        obs = obs_store.latest(SOURCE_ID, SOURCE_ID, "run:" + NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert obs is not None
        assert obs.status == Status.COMPLETE
        assert obs.payload["stats"]["n_events"] == 2
        assert obs.payload["stats"]["events_fwd_filled"]["all"] == 1
        assert len(obs.payload["backfill"]["pending"]) == 1
        assert obs.payload["registered_source"] is False
        assert obs.allowed_use_cases == ()          # 未登记 → 不伪造授权

    def test_bridge_registered_source_health(self, tmp_path, setup):
        from bridges.resonance_bridge import ResonanceStoreDispatcher, SOURCE_ID
        from registry.source_registry import SourceRegistry
        from store.obs_store import ObservationStore

        jp, pp, db, reg_path = self._make(tmp_path, setup, with_source=True)
        reg = SourceRegistry(reg_path)
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        d = ResonanceStoreDispatcher(reg, obs_store, db_path=db, index_parquet=pp)
        res = d.collect_source(SOURCE_ID, NOW)
        assert res["ok"] is True
        h = reg.get(SOURCE_ID).health
        assert h.get("consecutive_failures") == 0 and "last_success_at" in h
        obs = obs_store.latest(SOURCE_ID, SOURCE_ID, "run:" + NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert "internal_retention" in obs.allowed_use_cases

    def test_bridge_rejects_other_source(self, tmp_path, setup):
        from bridges.resonance_bridge import ResonanceStoreDispatcher
        from registry.source_registry import SourceRegistry
        from store.obs_store import ObservationStore

        jp, pp, db, reg_path = self._make(tmp_path, setup)
        d = ResonanceStoreDispatcher(SourceRegistry(reg_path),
                                     ObservationStore(str(tmp_path / "obs.db")),
                                     db_path=db, index_parquet=pp)
        res = d.collect_source("xueqiu", NOW)
        assert res["ok"] is False and "只接 resonance_store" in res["reason"]

    def test_bridge_failure_visible(self, tmp_path, setup):
        from bridges.resonance_bridge import ResonanceStoreDispatcher, SOURCE_ID
        from registry.source_registry import SourceRegistry
        from store.obs_store import ObservationStore
        from core.contracts import Status

        jp, pp, db, reg_path = self._make(tmp_path, setup, with_source=True)
        reg = SourceRegistry(reg_path)
        obs_store = ObservationStore(str(tmp_path / "obs.db"))
        d = ResonanceStoreDispatcher(reg, obs_store, db_path=db,
                                     index_parquet=str(tmp_path / "nope.parquet"))
        res = d.collect_source(SOURCE_ID, NOW)
        assert res["ok"] is False
        assert reg.get(SOURCE_ID).health.get("consecutive_failures") == 1
        obs = obs_store.latest(SOURCE_ID, SOURCE_ID, "run:" + NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert obs.status == Status.INCOMPLETE
        assert obs.payload["error"]
