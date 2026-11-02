# -*- coding: utf-8 -*-
"""qualify_probe 单测：纯本地 fixture，不读真实数据仓/不联网/不写 registry。"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

try:
    import pyarrow as pa  # noqa: E402
    import pyarrow.parquet as pq  # noqa: E402
    HAS_PYARROW = True
except Exception:  # noqa: BLE001
    HAS_PYARROW = False

import qualify_probe  # noqa: E402

TODAY = dt.date(2026, 10, 27)
CHECKED = "2026-10-27T10:30:00+08:00"


def write_parquet(path: Path, dates, col: str = "trade_date", stats: bool = True) -> None:
    table = pa.table({
        col: pa.array(dates, type=pa.string()),
        "value": pa.array(list(range(len(dates))), type=pa.int64()),
    })
    pq.write_table(table, path, write_statistics=stats)


def entry(store: Path, date_col: str | None = "trade_date", layout: str = "by_date",
          freq: str | None = None, static_no_date: bool = False,
          manifest_status: str | None = None, manifest: str | None = None) -> dict:
    e = {"store": str(store), "layout": layout, "date_col": date_col}
    if freq is not None:
        e["freq"] = freq
    if static_no_date:
        e["static_no_date"] = True
    if manifest_status is not None:
        e["manifest_status"] = manifest_status
    if manifest is not None:
        e["manifest"] = manifest
    return e


def _dated(days_ago: int):
    """构造距今 N 天的 YYYYMMDD（基于固定 TODAY，保证单测可重复）。"""
    return (TODAY - dt.timedelta(days=days_ago)).strftime("%Y%m%d")


def _make_sqlite(path: Path, table: str = "t1", dates=("20261024", "20261025", "20261026")) -> None:
    """建本地 sqlite 测试库（只用于探针单测；不写生产数据）。"""
    con = sqlite3.connect(str(path))
    try:
        con.execute('create table "%s" (a integer, trade_date text)' % table)
        con.executemany('insert into "%s" values (?, ?)' % table,
                        [(i, d) for i, d in enumerate(dates)])
        con.commit()
    finally:
        con.close()


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestDateHelpers(unittest.TestCase):
    def test_norm_date(self):
        self.assertEqual(qualify_probe.norm_date("20261026"), "20261026")
        self.assertEqual(qualify_probe.norm_date("2026-10-26"), "20261026")
        self.assertEqual(qualify_probe.norm_date(20261026), "20261026")
        self.assertEqual(qualify_probe.norm_date(dt.date(2026, 10, 26)), "20261026")
        self.assertIsNone(qualify_probe.norm_date(None))
        self.assertIsNone(qualify_probe.norm_date("not-a-date"))

    def test_date_from_name(self):
        self.assertEqual(qualify_probe.date_from_name("d_20261026.parquet"),
                         ("20261026", "20261026"))
        self.assertEqual(qualify_probe.date_from_name("p_20240930.parquet"),
                         ("20240930", "20240930"))
        key, fallback = qualify_probe.date_from_name("000906_202610.parquet")
        self.assertEqual(key, "20261099")
        self.assertEqual(fallback, "20261031")
        self.assertEqual(qualify_probe.date_from_name("000001_SZ.parquet"), (None, None))

    def test_manifest_keys(self):
        self.assertEqual(qualify_probe.manifest_keys({"a": {}, "b": {}}), {"a", "b"})
        self.assertEqual(qualify_probe.manifest_keys({"tables": {"x": {}, "y": {}}}), {"x", "y"})
        self.assertEqual(qualify_probe.manifest_keys(None), set())


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestProbeVerdicts(unittest.TestCase):
    def test_evidence_ok(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261026.parquet", ["20261026", "20261026"])
            write_parquet(store / "d_20261025.parquet", ["20261025"])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_ok")
            self.assertEqual(card["rows_sampled"], 3)
            self.assertEqual(card["latest_business_date"], "20261026")
            self.assertEqual(card["latest_source"], "footer_statistics")
            self.assertEqual(card["lag_days"], 1)
            self.assertFalse(card["latest_degraded"])
            self.assertEqual(card["reasons"], [])

    def test_manifest_missing_is_partial(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261026.parquet", ["20261026"])
            card = qualify_probe.probe_table("t1", entry(store), set(), TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertEqual(card["manifest_state"], "missing")
            self.assertIn("manifest_missing", card["reasons"])

    def test_filename_fallback_is_degraded(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261020.parquet", ["20261020"], stats=False)
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertEqual(card["latest_business_date"], "20261020")
            self.assertEqual(card["latest_source"], "filename_degraded")
            self.assertTrue(card["latest_degraded"])
            self.assertIn("filename_fallback", card["reasons"])

    def test_store_missing_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            card = qualify_probe.probe_table(
                "t1", entry(Path(d) / "nope"), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("store_missing", card["reasons"])

    def test_empty_store_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("no_parquet", card["reasons"])

    def test_zero_rows_is_bad(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261020.parquet", [])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertEqual(card["rows_sampled"], 0)
            self.assertIn("zero_rows_sampled", card["reasons"])

    def test_stale_lag_is_partial(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261019.parquet", ["20261019"])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["lag_days"], 8)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("stale_gt_7d", card["reasons"])

    def test_future_date_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261028.parquet", ["20261028"])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["lag_days"], -1)
            self.assertTrue(card["future_date"])
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("future_date", card["reasons"])

    def test_no_date_column_non_whitelist_is_partial(self):
        """task-32：无 date_col 但不在豁免白名单 → 回到 partial（no_date_column，不再静默豁免）。"""
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            pq.write_table(pa.table({"a": pa.array([1]), "b": pa.array(["x"])}),
                           str(store / "part_0000.parquet"))
            card = qualify_probe.probe_table("t1", entry(store, None), {"t1"}, TODAY, CHECKED)
            self.assertIsNone(card["latest_business_date"])
            self.assertEqual(card["latest_source"], "no_date_column")
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("no_date_column", card["reasons"])
            self.assertNotEqual(card["date_check"], qualify_probe.STATIC_DATE_EXEMPT)
            self.assertIsNone(card.get("exempt_source"))

    def test_whitelist_static_exempt_visible(self):
        """task-32：白名单表（tdx_sector_def）无 date_col → 豁免可见且 exempt_source=whitelist。"""
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            pq.write_table(pa.table({"a": pa.array([1]), "b": pa.array(["x"])}),
                           str(store / "part_0000.parquet"))
            card = qualify_probe.probe_table(
                "tdx_sector_def", entry(store, None), {"tdx_sector_def"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_ok")
            self.assertEqual(card["date_check"], qualify_probe.STATIC_DATE_EXEMPT)
            self.assertEqual(card["exempt_source"], "whitelist")
            self.assertEqual(card["exempt_reason"], "whitelist")
            self.assertNotIn("no_date_column", card["reasons"])
            self.assertIn("exempt_source=whitelist", card["unsupported_reason"])

    def test_sample_limit_and_latest(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            for day in range(1, 11):
                write_parquet(store / ("d_202610%02d.parquet" % day), ["202610%02d" % day])
            card = qualify_probe.probe_table("t1", entry(store), {"t1"}, TODAY, CHECKED)
            self.assertLessEqual(card["sample_count"], 5)
            self.assertGreater(card["rows_sampled"], 0)
            self.assertEqual(card["latest_business_date"], "20261010")

    def test_nested_files_counted_but_top_level_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            (store / "sub").mkdir(parents=True)
            write_parquet(store / "sub" / "d_20260105__prod.parquet", ["20260105"])
            card = qualify_probe.probe_table("q", entry(store, layout="part"), set(), TODAY, CHECKED)
            self.assertEqual(card["files_on_disk"], 1)
            self.assertEqual(card["files_top_level"], 0)
            self.assertEqual(card["nested_dirs"], ["sub"])
            self.assertEqual(card["rows_sampled"], 1)
            self.assertEqual(card["verdict"], "evidence_partial")

    def test_summary_length(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            write_parquet(store / "d_20261026.parquet", ["20261026"])
            card = qualify_probe.probe_table("t1", entry(store), set(), TODAY, CHECKED)
            self.assertLessEqual(len(card["evidence_summary"]), 200)
            self.assertLessEqual(len(card["unsupported_reason"]), 200)


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestFreqAwareLag(unittest.TestCase):
    """task-24：freq-aware 滞后阈值（D=7/W=14/M=40/Q=100）+ 静态表例外 fail-visible。"""

    def _card(self, freq, days_ago, static_no_date=False, has_date=True):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            if has_date:
                date = _dated(days_ago)
                write_parquet(store / ("d_%s.parquet" % date), [date])
                date_col = "trade_date"
            else:
                pq.write_table(pa.table({"a": pa.array([1])}), str(store / "part_0000.parquet"))
                date_col = None
            return qualify_probe.probe_table(
                "t1", entry(store, date_col, freq=freq, static_no_date=static_no_date),
                {"t1"}, TODAY, CHECKED)

    def test_lag_limit_for(self):
        self.assertEqual(qualify_probe.lag_limit_for({"freq": "D"}), (7, "freq:D"))
        self.assertEqual(qualify_probe.lag_limit_for({"freq": "W"}), (14, "freq:W"))
        self.assertEqual(qualify_probe.lag_limit_for({"freq": "M"}), (40, "freq:M"))
        self.assertEqual(qualify_probe.lag_limit_for({"freq": "Q"}), (100, "freq:Q"))
        self.assertEqual(qualify_probe.lag_limit_for({}), (7, "freq_default"))
        self.assertEqual(qualify_probe.lag_limit_for({"freq": "1min"}), (7, "freq_default"))

    def test_week_threshold(self):
        card = self._card("W", 12)
        self.assertEqual(card["lag_limit_days"], 14)
        self.assertEqual(card["verdict"], "evidence_ok")
        self.assertEqual(card["reasons"], [])
        over = self._card("W", 20)
        self.assertEqual(over["verdict"], "evidence_partial")
        self.assertIn("stale_gt_14d", over["reasons"])

    def test_month_threshold(self):
        card = self._card("M", 35)
        self.assertEqual(card["lag_limit_days"], 40)
        self.assertEqual(card["verdict"], "evidence_ok")
        over = self._card("M", 45)
        self.assertEqual(over["verdict"], "evidence_partial")
        self.assertIn("stale_gt_40d", over["reasons"])

    def test_quarter_threshold(self):
        card = self._card("Q", 90)
        self.assertEqual(card["lag_limit_days"], 100)
        self.assertEqual(card["verdict"], "evidence_ok")
        over = self._card("Q", 120)
        self.assertEqual(over["verdict"], "evidence_partial")
        self.assertIn("stale_gt_100d", over["reasons"])

    def test_d_still_strict(self):
        card = self._card("D", 8)
        self.assertEqual(card["verdict"], "evidence_partial")
        self.assertIn("stale_gt_7d", card["reasons"])

    def test_unknown_freq_defaults_to_d(self):
        card = self._card(None, 8)
        self.assertEqual(card["lag_limit_rule"], "freq_default")
        self.assertEqual(card["verdict"], "evidence_partial")
        self.assertIn("stale_gt_7d", card["reasons"])

    def test_static_no_date_flag_exempt_visible(self):
        """static_no_date=True（catalog 显式声明）：即使日期可读也不判 stale，豁免来源可见。"""
        card = self._card("D", 400, static_no_date=True)
        self.assertEqual(card["verdict"], "evidence_ok")
        self.assertEqual(card["date_check"], qualify_probe.STATIC_DATE_EXEMPT)
        self.assertEqual(card["exempt_reason"], "static_no_date")
        self.assertEqual(card["exempt_source"], "catalog_static_no_date")
        self.assertNotIn("stale_gt_7d", card["reasons"])
        self.assertIn("豁免", card["unsupported_reason"])


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestCollect(unittest.TestCase):
    def test_summary_counts(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = Path(d) / "s1"
            s2 = Path(d) / "s2"
            s1.mkdir()
            s2.mkdir()
            write_parquet(s1 / "d_20261026.parquet", ["20261026"])
            write_parquet(s2 / "d_20261026.parquet", ["20261026"])
            catalog = {"version": 12, "updated": "x", "tables": {
                "a": entry(s1), "b": entry(s2)}}
            payload = qualify_probe.collect_evidence(
                ["a", "b"], catalog, {"a": {}}, TODAY, CHECKED, catalog_sha="fake-sha")
            self.assertEqual(payload["summary"], {"tables": 2, "ok": 1, "partial": 1, "bad": 0})
            self.assertEqual(payload["tables"][0]["verdict"], "evidence_ok")
            self.assertEqual(payload["tables"][1]["verdict"], "evidence_partial")


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestManifestNotApplicable(unittest.TestCase):
    """task-28：manifest_status=not_applicable → 不回退 tushare、不判 manifest_missing。"""

    def _store(self, d, days_ago=1):
        store = Path(d) / "store"
        store.mkdir()
        date = _dated(days_ago)
        write_parquet(store / ("d_%s.parquet" % date), [date])
        return store

    def test_resolve_manifest_path_not_applicable(self):
        """not_applicable 优先于显式 manifest 路径与 fallback（对照：无该状态仍走 catalog）。"""
        with tempfile.TemporaryDirectory() as d:
            fb = Path(d) / "fb.json"
            fb.write_text("{}", encoding="utf-8")
            self.assertEqual(
                qualify_probe.resolve_manifest_path(
                    {"manifest_status": "not_applicable", "manifest": str(fb)}, fallback=fb),
                (None, "not_applicable"))
            path, src = qualify_probe.resolve_manifest_path({"manifest": str(fb)}, fallback=fb)
            self.assertEqual((path, src), (fb, "catalog"))

    def test_not_applicable_no_missing_reason(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            card = qualify_probe.probe_table(
                "t1", entry(store, freq="D", manifest_status="not_applicable"), set(),
                TODAY, CHECKED, manifest_source="not_applicable")
            self.assertEqual(card["manifest_state"], "not_applicable")
            self.assertEqual(card["manifest_source"], "not_applicable")
            self.assertEqual(card["verdict"], "evidence_ok")
            self.assertNotIn("manifest_missing", card["reasons"])
            self.assertNotIn("manifest_fallback_tushare", card["reasons"])
            self.assertIn("not_applicable", card["unsupported_reason"])

    def test_not_applicable_ignores_fallback(self):
        """catalog null + not_applicable 与「显式路径」语义区分：不读 fallback、不记 fallbacks。"""
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            fb = Path(d) / "fb.json"
            fb.write_text(json.dumps({"t_na": {}}), encoding="utf-8")
            catalog = {"version": 18, "updated": "x", "tables": {
                "t_na": entry(store, freq="D", manifest_status="not_applicable")}}
            payload = qualify_probe.collect_evidence(
                ["t_na"], catalog, None, TODAY, CHECKED, fallback_manifest=fb)
            card = payload["tables"][0]
            self.assertEqual(card["manifest_state"], "not_applicable")
            self.assertEqual(card["verdict"], "evidence_ok")
            self.assertNotIn("manifest_missing", card["reasons"])
            self.assertEqual(payload["manifest"]["not_applicable"], ["t_na"])
            self.assertEqual(payload["manifest"]["fallbacks"], [])
            self.assertEqual(payload["manifest"]["manifests"], {})

    def test_not_applicable_other_checks_still_apply(self):
        """manifest N/A 不豁免其它检查：stale 仍 partial；deprecated/空目录仍 bad。"""
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, days_ago=8)
            card = qualify_probe.probe_table(
                "t1", entry(store, freq="D", manifest_status="not_applicable"), set(),
                TODAY, CHECKED, manifest_source="not_applicable")
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("stale_gt_7d", card["reasons"])
            self.assertNotIn("manifest_missing", card["reasons"])
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "empty"
            store.mkdir()
            e = entry(store, freq="D", manifest_status="not_applicable")
            e["deprecated"] = True  # lifecycle 口径不掩盖数据检查
            card = qualify_probe.probe_table("t1", e, set(), TODAY, CHECKED,
                                             manifest_source="not_applicable")
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("no_parquet", card["reasons"])
            self.assertNotIn("manifest_missing", card["reasons"])


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestSqliteStoreProbe(unittest.TestCase):
    """task-32：catalog store=sqlite 的表走只读 sqlite 探针（行数/日期列 max）。"""

    def test_sqlite_rows_and_latest_ok(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "info.db"
            _make_sqlite(db, "t1", ("20261024", "20261025", "20261026"))
            e = entry(db, date_col="trade_date", layout="sqlite", freq="D")
            card = qualify_probe.probe_table("t1", e, {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_ok")
            self.assertEqual(card["rows_sampled"], 3)
            self.assertEqual(card["latest_business_date"], "20261026")
            self.assertEqual(card["latest_source"], "sqlite_max")
            self.assertEqual(card["lag_days"], 1)
            self.assertEqual(card["sqlite"]["table"], "t1")
            self.assertIn("sqlite", card["unsupported_reason"])

    def test_sqlite_stale_is_partial_not_bad(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "info.db"
            _make_sqlite(db, "t1", ("20260101", "20260102"))
            e = entry(db, date_col="trade_date", layout="sqlite", freq="D")
            card = qualify_probe.probe_table("t1", e, {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("stale_gt_7d", card["reasons"])
            self.assertNotIn("no_parquet", card["reasons"])

    def test_sqlite_table_missing_fail_visible(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "info.db"
            _make_sqlite(db, "other_table", ("20261026",))
            e = entry(db, date_col="trade_date", layout="sqlite", freq="D")
            card = qualify_probe.probe_table("t1", e, {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("sqlite_table_missing", card["reasons"])

    def test_sqlite_store_path_missing(self):
        with tempfile.TemporaryDirectory() as d:
            e = entry(Path(d) / "nope.db", date_col="trade_date", layout="sqlite")
            card = qualify_probe.probe_table("t1", e, {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["verdict"], "evidence_bad")
            self.assertIn("store_missing", card["reasons"])


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestStaticWhitelist(unittest.TestCase):
    """task-32：静态豁免显式白名单 + payload exempt_source=whitelist。"""

    def test_collect_payload_whitelist_and_exempt_sources(self):
        with tempfile.TemporaryDirectory() as d:
            s1 = Path(d) / "s1"
            s2 = Path(d) / "s2"
            s1.mkdir()
            s2.mkdir()
            pq.write_table(pa.table({"a": pa.array([1])}), str(s1 / "part_0000.parquet"))
            pq.write_table(pa.table({"a": pa.array([1])}), str(s2 / "part_0000.parquet"))
            catalog = {"version": 18, "updated": "x", "tables": {
                "tdx_sector_def": entry(s1, None),
                "ts_news": entry(s2, None)}}
            payload = qualify_probe.collect_evidence(
                ["tdx_sector_def", "ts_news"], catalog,
                {"tdx_sector_def": {}, "ts_news": {}}, TODAY, CHECKED)
            dp = payload["date_policy"]
            self.assertIn("tdx_sector_def", dp["static_exempt_whitelist"])
            self.assertNotIn("ts_news", dp["static_exempt_whitelist"])
            self.assertEqual(dp["static_exempt_tables"], ["tdx_sector_def"])
            self.assertEqual(dp["exempt_sources"], {"tdx_sector_def": "whitelist"})
            cards = {c["table"]: c for c in payload["tables"]}
            self.assertEqual(cards["tdx_sector_def"]["verdict"], "evidence_ok")
            self.assertEqual(cards["ts_news"]["verdict"], "evidence_partial")
            self.assertIn("no_date_column", cards["ts_news"]["reasons"])


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestPartSampling(unittest.TestCase):
    """task-32：part 布局抽样覆盖 + 全量 footer 扫描真实最大业务日。"""

    def test_select_samples_part_spread_includes_first_part(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            for i in range(6):
                f = store / ("part_%04d.parquet" % i)
                write_parquet(f, [_dated(1)])
                os.utime(f, (1000 + i, 1000 + i))  # part_0000 最旧 mtime
            files, _, _ = qualify_probe._iter_parquet(store)
            samples, strategy = qualify_probe.select_samples(files, limit=3, layout="part")
            self.assertEqual(strategy, "part_spread")
            rels = [f.rel for f in samples]
            self.assertIn("part_0000.parquet", rels)
            self.assertLessEqual(len(samples), 3)

    def test_part_full_footer_scan_finds_true_latest(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "store"
            store.mkdir()
            # 真实最新日藏在 part_0007（不在 part_spread 抽样索引内、mtime 也最旧）
            for i in range(10):
                f = store / ("part_%04d.parquet" % i)
                write_parquet(f, [_dated(300 if i != 7 else 1)])
                os.utime(f, (2000 - i, 2000 - i))
            card = qualify_probe.probe_table(
                "t1", entry(store, date_col="trade_date", layout="part", freq="D"),
                {"t1"}, TODAY, CHECKED)
            self.assertEqual(card["latest_business_date"], _dated(1))
            self.assertEqual(card["latest_source"], "footer_all_parts")
            self.assertIsNotNone(card["part_scan"])
            self.assertGreaterEqual(card["part_scan"]["files_scanned"], 10)
            self.assertEqual(card["verdict"], "evidence_ok")


class TestRegistry(unittest.TestCase):
    def _registry(self):
        return {
            "generated_at": "2026-10-27T10:09:39+08:00",
            "catalog_sha256": "abc",
            "physical_count": 3,
            "display_count": 1,
            "physical_tables": [
                {"asset_id": "a", "qualification_status": "unknown", "other": 1},
                {"asset_id": "b", "qualification_status": "unknown", "other": 2},
                {"asset_id": "c", "qualification_status": "unknown", "other": 3},
            ],
        }

    def _cards(self):
        return [
            {"table": "a", "verdict": "evidence_partial", "evidence_summary": "s1",
             "unsupported_reason": "u1"},
            {"table": "b", "verdict": "evidence_ok", "evidence_summary": "s2",
             "unsupported_reason": "u2"},
        ]

    def test_update_and_validate(self):
        before = self._registry()
        after = qualify_probe.update_registry_entries(before, self._cards(), CHECKED)
        self.assertEqual(after["physical_tables"][0]["qualification_status"], "evidence_partial")
        self.assertEqual(after["physical_tables"][1]["qualification_status"], "evidence_ok")
        self.assertEqual(after["physical_tables"][2]["qualification_status"], "unknown")
        self.assertEqual(after["physical_tables"][2]["other"], 3)
        self.assertEqual(after["physical_count"], 3)
        val = qualify_probe.validate_registry(before, after, ["a", "b"], "abc",
                                              expected_physical=3, expected_display=1)
        self.assertTrue(val["ok"], val)
        self.assertTrue(val["diff"]["only_allowed_fields"])
        self.assertTrue(val["diff"]["other_entries_unchanged"])

    def test_validate_rejects_other_entry_change(self):
        before = self._registry()
        after = qualify_probe.update_registry_entries(before, self._cards(), CHECKED)
        after["physical_tables"][2]["other"] = 99
        val = qualify_probe.validate_registry(before, after, ["a", "b"], "abc",
                                              expected_physical=3, expected_display=1)
        self.assertFalse(val["ok"])
        self.assertFalse(val["checks"]["other_entries_unchanged"])

    def test_validate_rejects_sha_drift(self):
        before = self._registry()
        after = qualify_probe.update_registry_entries(before, self._cards(), CHECKED)
        after["catalog_sha256"] = "drift"
        val = qualify_probe.validate_registry(before, after, ["a", "b"], "abc",
                                              expected_physical=3, expected_display=1)
        self.assertFalse(val["ok"])
        self.assertFalse(val["checks"]["catalog_sha256_unchanged"])

    def test_write_registry_backup_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "registry.json"
            path.write_text(json.dumps(self._registry(), ensure_ascii=False), encoding="utf-8")
            reg = self._registry()
            reg["physical_tables"][0]["qualification_status"] = "evidence_partial"
            info = qualify_probe.write_registry(path, reg)
            self.assertTrue(Path(info["backup"]).is_file())
            reread = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(reread["physical_tables"][0]["qualification_status"],
                             "evidence_partial")
            self.assertEqual(reread["physical_count"], 3)


class TestKgLog(unittest.TestCase):
    def test_ok(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("kg_sync OK 2026-10-27T08:08:08\n", encoding="utf-8")
            self.assertTrue(qualify_probe.check_kg_log(p)["ok"])

    def test_in_progress_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("kg_sync start 2026-10-27T10:00:00\n", encoding="utf-8")
            r = qualify_probe.check_kg_log(p)
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "last_entry_in_progress")

    def test_error_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("kg_sync exit=1 2026-10-27T10:00:00\n", encoding="utf-8")
            r = qualify_probe.check_kg_log(p)
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "last_entry_error")

    def test_missing_refused(self):
        with tempfile.TemporaryDirectory() as d:
            r = qualify_probe.check_kg_log(Path(d) / "nope.log")
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "missing")

    def test_skips_previous_qual_probe_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kg.log"
            p.write_text("qual_probe date=x tables=26 ok=0 partial=26 bad=0 exit=0\n"
                         "registry_rebuild date=x tables=245 sha=x exit=0\n",
                         encoding="utf-8")
            self.assertTrue(qualify_probe.check_kg_log(p)["ok"])


class TestDefaults(unittest.TestCase):
    def test_default_tables(self):
        self.assertEqual(len(qualify_probe.DEFAULT_TABLES), 26)
        self.assertEqual(len(set(qualify_probe.DEFAULT_TABLES)), 26)
        self.assertIn("quarantine_prod_overwrite", qualify_probe.DEFAULT_TABLES)
        self.assertIn("us_fina_indicator", qualify_probe.DEFAULT_TABLES)


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestManifestPathResolution(unittest.TestCase):
    """task-23：manifest 按 catalog 条目 manifest 字段解析，不再固定 tushare。"""

    def test_catalog_manifest_wins(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tdx" / "_manifest.json"
            p.parent.mkdir(parents=True)
            p.write_text("{}", encoding="utf-8")
            path, source = qualify_probe.resolve_manifest_path({"manifest": str(p)})
            self.assertEqual(path, p)
            self.assertEqual(source, "catalog")

    def test_relative_manifest_resolved_under_repo(self):
        path, source = qualify_probe.resolve_manifest_path(
            {"manifest": "quant_backtest/data/tdx/_manifest.json"})
        self.assertEqual(source, "catalog")
        self.assertTrue(path.is_absolute())
        self.assertTrue(str(path).replace("\\", "/").endswith(
            "quant_backtest/data/tdx/_manifest.json"))

    def test_absent_manifest_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            fb = Path(d) / "tushare_manifest.json"
            fb.write_text("{}", encoding="utf-8")
            self.assertEqual(qualify_probe.resolve_manifest_path({}, fallback=fb),
                             (fb, "tushare_fallback"))
            self.assertEqual(
                qualify_probe.resolve_manifest_path({}, fallback=Path(d) / "nope.json"),
                (None, "none"))


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestFallbackVerdicts(unittest.TestCase):
    """task-23：fallback manifest 不可读/缺失必须 fail-visible（evidence_partial）。"""

    def _store(self, d):
        store = Path(d) / "store"
        store.mkdir()
        write_parquet(store / "d_20261026.parquet", ["20261026"])
        return store

    def test_manifest_fallback_is_partial_visible(self):
        with tempfile.TemporaryDirectory() as d:
            card = qualify_probe.probe_table(
                "t1", entry(self._store(d)), {"t1"}, TODAY, CHECKED,
                manifest_path=Path(d) / "tushare" / "_manifest.json",
                manifest_source="tushare_fallback")
            self.assertEqual(card["manifest_state"], "present")
            self.assertIn("manifest_fallback_tushare", card["reasons"])
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("回退", card["unsupported_reason"])

    def test_manifest_catalog_unreadable_is_partial_visible(self):
        with tempfile.TemporaryDirectory() as d:
            card = qualify_probe.probe_table(
                "t1", entry(self._store(d)), set(), TODAY, CHECKED,
                manifest_issue="catalog_manifest_unreadable")
            self.assertIn("manifest_missing", card["reasons"])
            self.assertIn("manifest_catalog_unreadable", card["reasons"])
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertIn("不可读", card["unsupported_reason"])


@unittest.skipUnless(HAS_PYARROW, "pyarrow 不可用")
class TestCollectPerTableManifest(unittest.TestCase):
    """task-23：collect_evidence 逐表读取 catalog 指定 manifest。"""

    def _mkstore(self, d, name, date="20261026"):
        store = Path(d) / name
        store.mkdir()
        write_parquet(store / ("d_%s.parquet" % date), [date])
        return store

    def test_catalog_manifest_path_used_for_tdx_and_tushare(self):
        with tempfile.TemporaryDirectory() as d:
            dfiles = Path(d)
            tdx_m = dfiles / "tdx_manifest.json"
            tdx_m.write_text(json.dumps({"t_tdx": {}}), encoding="utf-8")
            ts_m = dfiles / "tushare_manifest.json"
            ts_m.write_text(json.dumps({"t_ts": {"rows": 1}}), encoding="utf-8")
            s1 = self._mkstore(d, "s1")
            s2 = self._mkstore(d, "s2")
            catalog = {"version": 18, "updated": "x", "tables": {
                "t_tdx": dict(entry(s1), source="xtcloud", manifest=str(tdx_m),
                              manifest_status="closed"),
                "t_ts": dict(entry(s2), source="tushare", manifest=str(ts_m),
                             manifest_status="closed"),
            }}
            payload = qualify_probe.collect_evidence(
                ["t_tdx", "t_ts"], catalog, None, TODAY, CHECKED,
                fallback_manifest=dfiles / "no_fallback.json")
            self.assertEqual(payload["summary"],
                             {"tables": 2, "ok": 2, "partial": 0, "bad": 0})
            self.assertEqual(payload["manifest"]["mode"], "per_table")
            self.assertEqual(len(payload["manifest"]["manifests"]), 2)
            cards = {c["table"]: c for c in payload["tables"]}
            self.assertEqual(cards["t_tdx"]["manifest_state"], "present")
            self.assertEqual(cards["t_tdx"]["manifest_source"], "catalog")
            self.assertTrue(cards["t_tdx"]["manifest_path"].endswith("tdx_manifest.json"))
            self.assertEqual(cards["t_ts"]["manifest_source"], "catalog")
            self.assertEqual(cards["t_ts"]["verdict"], "evidence_ok")

    def test_missing_catalog_manifest_field_falls_back_visible(self):
        with tempfile.TemporaryDirectory() as d:
            dfiles = Path(d)
            s1 = self._mkstore(d, "s1")
            fb = dfiles / "tushare_manifest.json"
            fb.write_text(json.dumps({"t_no_manifest": {}}), encoding="utf-8")
            catalog = {"version": 18, "updated": "x",
                       "tables": {"t_no_manifest": entry(s1)}}
            payload = qualify_probe.collect_evidence(
                ["t_no_manifest"], catalog, None, TODAY, CHECKED, fallback_manifest=fb)
            card = payload["tables"][0]
            self.assertEqual(card["manifest_source"], "tushare_fallback")
            self.assertIn("manifest_fallback_tushare", card["reasons"])
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertEqual(payload["manifest"]["fallbacks"], ["t_no_manifest"])

    def test_unreadable_catalog_manifest_is_partial_visible(self):
        with tempfile.TemporaryDirectory() as d:
            dfiles = Path(d)
            s1 = self._mkstore(d, "s1")
            catalog = {"version": 18, "updated": "x", "tables": {
                "t_bad": dict(entry(s1), manifest=str(dfiles / "missing_manifest.json"))}}
            payload = qualify_probe.collect_evidence(
                ["t_bad"], catalog, None, TODAY, CHECKED,
                fallback_manifest=dfiles / "no_fb.json")
            card = payload["tables"][0]
            self.assertIn("manifest_catalog_unreadable", card["reasons"])
            self.assertEqual(card["verdict"], "evidence_partial")
            self.assertEqual(payload["manifest"]["unreadable"], ["t_bad"])


if __name__ == "__main__":
    unittest.main()
