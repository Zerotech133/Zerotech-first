from unittest import mock
import json
import sqlite3

import data_health


def test_build_gaps_reports_missing_cn_fields():
    kline = {
        "cn": {"count": 1, "exists": True, "missing_fields": ["turnover", "pe"]},
        "us": {"count": 1, "exists": True},
        "hk": {"count": 1, "exists": True},
        "crypto": {"count": 1, "exists": True},
    }
    news = {"exists": True, "has_announcement": True,
            "has_research": True, "has_report": True, "jev_pending": 0}
    gaps = data_health._build_gaps(kline, news)
    assert any("A股关键字段缺失" in gap and "turnover" in gap for gap in gaps)


def test_build_gaps_reports_stale_strategy_artifact():
    kline = {
        "cn": {"count": 1, "exists": True},
        "us": {"count": 1, "exists": True},
        "hk": {"count": 1, "exists": True},
        "crypto": {"count": 1, "exists": True},
    }
    news = {"exists": True, "has_announcement": True,
            "has_research": True, "has_report": True, "jev_pending": 0}
    artifacts = [{"name": "盘前分析", "file": "premarket_latest.json",
                  "exists": True, "age_hours": 30.0}]
    gaps = data_health._build_gaps(kline, news, artifacts)
    assert any("盘前分析" in gap and "过期" in gap for gap in gaps)


def test_scan_news_keeps_primary_stats_when_optional_schema_probe_fails():
    fake_conn = mock.MagicMock()
    fake_conn.execute.side_effect = [
        mock.MagicMock(fetchone=lambda: (10,)),
        mock.MagicMock(fetchone=lambda: ("2026-10-01", "2026-10-02")),
        mock.MagicMock(fetchall=lambda: [("sina", 10)]),
        mock.MagicMock(fetchone=lambda: (3,)),
        RuntimeError("schema probe unavailable"),
    ]
    with mock.patch.object(data_health.sqlite3, "connect", return_value=fake_conn):
        result = data_health._scan_news()
    assert result["total"] == 10
    assert result["sources"] == {"sina": 10}
    assert result["jev_pending"] == 7


def test_jev_reconciliation_exposes_pending_unqueued_ids(tmp_path):
    db_path = tmp_path / "news.db"
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, jev TEXT)")
    db.executemany("INSERT INTO news(id, jev) VALUES (?, ?)", [(1, None), (2, "利多"), (3, None)])
    db.commit()
    db.close()
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps([{"id": "1", "status": "pending"},
                                 {"id": "99", "status": "blocked"}], ensure_ascii=False), encoding="utf-8")
    result = data_health._scan_jev_reconciliation(news_db_path=db_path, queue_paths=[queue])
    assert result["status"] == "FAIL"
    assert result["news_pending"] == 2
    assert result["pending_unqueued"] == ["3"]
    assert result["queue_orphans"] == ["99"]


def test_build_gaps_reports_valuation_nulls_and_jev_reconciliation():
    kline = {"cn": {"count": 1, "exists": True,
                     "valuation_fields": {"pe": {"missing": 2}, "turnover": {"missing": 0}}},
             "us": {"count": 1, "exists": True}, "hk": {"count": 1, "exists": True},
             "crypto": {"count": 1, "exists": True}}
    news = {"exists": True, "has_announcement": True, "has_research": True,
            "has_report": True, "jev_pending": 0}
    gaps = data_health._build_gaps(kline, news,
                                   jev_reconciliation={"status": "FAIL", "pending_unqueued_count": 3})
    assert any("估值字段" in gap and "pe=2" in gap for gap in gaps)
    assert any("JEV staging/新闻库对账失败" in gap for gap in gaps)


def test_scan_warehouse_exposes_health_gaps_from_fields_and_artifacts(monkeypatch):
    data_health._WCACHE["t"], data_health._WCACHE["d"] = 0, None
    monkeypatch.setattr(data_health, "_scan_kline", lambda market: {
        "count": 1, "exists": True, "size_mb": 1})
    monkeypatch.setattr(data_health, "_sample_parquet", lambda market="cn": {
        "file": "sample.parquet", "rows": 1,
        "cols": ["ts_code", "trade_date", "close"]})
    monkeypatch.setattr(data_health, "_scan_news", lambda: {
        "exists": True, "total": 1, "sources": {}, "jev_judged": 1,
        "jev_pending": 0, "date_range": "2026-10-01 ~ 2026-10-01"})
    monkeypatch.setattr(data_health, "_scan_tdx", lambda: {
        "tables": [], "info": [], "sync_done": 0, "sync_total": 0,
        "db_size_mb": 0})
    monkeypatch.setattr(data_health, "_scan_tushare", lambda: {
        "tables": [], "sync_done": 0, "sync_total": 0})
    monkeypatch.setattr(data_health, "_scan_freshness", lambda: {"tables": []})
    monkeypatch.setattr(data_health, "_scan_integrity", lambda: {
        "metrics": [], "fail_count": 0, "warn_count": 0})
    monkeypatch.setattr(data_health, "_scan_artifacts", lambda: [{
        "name": "盘前分析", "exists": True, "age_hours": 30,
        "size_mb": 1, "mtime": "2026-10-01 00:00"}])
    monkeypatch.setattr(data_health, "_scan_business_monitoring", lambda: {})
    result = data_health.scan_warehouse()
    assert any("A股关键字段缺失" in gap for gap in result["gaps"])
    assert any("策略产物过期" in gap for gap in result["gaps"])


def test_tdx_sync_summary_separates_sqlite_and_parquet(tmp_path, monkeypatch):
    root = tmp_path / "tdx"
    root.mkdir()
    (root / "_manifest.json").write_text(json.dumps({
        "ts_daily": {"done": True, "rows": 2},
        "ts_rt_min": {"done": False, "rows": 3},
        "stock_notice": {"done": True, "rows": 4},
        "stock_news": {"done": True, "rows": 5},
        "stock_report": {"done": True, "rows": 6},
    }), encoding="utf-8")
    table = root / "ts_daily"
    table.mkdir()
    (table / "part_0000.parquet").write_bytes(b"x")
    monkeypatch.setattr(data_health, "DATA", str(tmp_path))
    result = data_health._scan_tdx()
    assert result["sync_summary"] == {
        "parquet_done": 1, "parquet_total": 2,
        "sqlite_done": 0, "sqlite_total": 3,
        "incomplete": ["ts_rt_min"],
    }


def test_tushare_sync_summary_separates_probes_and_no_coverage(tmp_path, monkeypatch):
    root = tmp_path / "tushare"
    root.mkdir()
    (root / "_manifest.json").write_text(json.dumps({
        "daily": {"done": True, "rows": 2},
        "monetary_policy": {"done": False},
        "hk_mins": {"done": True, "codes": 0},
        "stk_auction_probe": {"done": True, "rows": 0},
    }), encoding="utf-8")
    table = root / "daily"
    table.mkdir()
    (table / "part_0000.parquet").write_bytes(b"x")
    monkeypatch.setattr(data_health, "DATA", str(tmp_path))
    result = data_health._scan_tushare()
    assert result["sync_summary"] == {
        "entity_done": 1, "entity_total": 3,
        "probe_count": 1, "no_coverage": ["hk_mins"],
        "incomplete": ["hk_mins", "monetary_policy"],
    }
