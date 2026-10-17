# -*- coding: utf-8 -*-
import json
import sqlite3
import threading
from datetime import timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from watch_tower_snapshot import ReplayArgumentError, SnapshotAggregator, parse_replay_at
from portal_api import _replay_query_value


def test_snapshot_partial_sources_and_cache(tmp_path: Path) -> None:
    station = tmp_path / "station" / "news_collector"
    station.mkdir(parents=True)
    (station / "quotes_status.json").write_text(json.dumps({"quotes": 2}), encoding="utf-8")
    portal = tmp_path / "portal"
    portal.mkdir()
    (portal / "qd_scan_cache.json").write_text(json.dumps({"signals_now": []}), encoding="utf-8")
    aggregator = SnapshotAggregator(portal, tmp_path / "station", ttl_sec=10)
    first = aggregator.snapshot()
    second = aggregator.snapshot()
    assert first["schema"] == "watch_tower.snapshot.v1"
    assert first is second
    assert any(item["name"] == "ingest" and item["error_code"] == "not_implemented" for item in first["sources"])


def test_http_health_source_accepts_json(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, *_args):
            return
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        aggregator = SnapshotAggregator(tmp_path, tmp_path, ttl_sec=10)
        source = aggregator._http_source("test", f"http://127.0.0.1:{server.server_port}/health", True)
        assert source["state"] == "healthy"
        assert source["data"]["ok"] is True
    finally:
        server.shutdown()


def _make_history_db(tmp_path: Path) -> Path:
    news_dir = tmp_path / "station" / "news_collector"
    news_dir.mkdir(parents=True)
    db_path = news_dir / "news.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, content TEXT,
                               hit INTEGER, pushed INTEGER, digested INTEGER, created_at TEXT,
                               stocks TEXT, jev TEXT);
            CREATE TABLE runs (id INTEGER PRIMARY KEY, kind TEXT, ts TEXT, ok INTEGER,
                               new_count INTEGER, push_count INTEGER, detail TEXT);
            CREATE TABLE quotes (code TEXT, name TEXT, ts TEXT, price REAL, pct REAL,
                                 open REAL, high REAL, low REAL, vol REAL, amount REAL, created_at TEXT,
                                 PRIMARY KEY(code, ts));
            CREATE TABLE ai_reviews (date TEXT PRIMARY KEY, ts TEXT, model TEXT, ok INTEGER,
                                     markets TEXT, brief TEXT, pushed INTEGER);
        """)
        db.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("n1", "sina", "2026-10-16 09:00", "历史新闻", "内容", 1, 0, 0, "2026-10-16 09:01", None, None))
        db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?)", (1, "collect", "2026-10-16 09:05", 1, 1, 0, "{}"))
        db.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("sh600000", "浦发银行", "2026-10-16 09:30:00", 10, 1, 9, 10, 9, 1, 2, "2026-10-16 09:30:01"))
        db.execute("INSERT INTO ai_reviews VALUES (?,?,?,?,?,?,?)", ("2026-10-16", "2026-10-16 15:30", "test", 1, "[]", "复盘", 0))
        db.commit()
    return db_path


def test_replay_query_accepts_literal_and_encoded_plus() -> None:
    literal = _replay_query_value("replay_at=2026-10-16T12:00:00+08:00")
    encoded = _replay_query_value("replay_at=2026-10-16T12:00:00%2B08:00")
    assert literal == "2026-10-16T12:00:00+08:00"
    assert encoded == "2026-10-16T12:00:00+08:00"
    assert parse_replay_at(literal) == parse_replay_at(encoded)


def test_replay_at_requires_timezone_and_rejects_future() -> None:
    assert parse_replay_at("2026-10-16").tzinfo == timezone.utc
    with pytest.raises(ReplayArgumentError):
        parse_replay_at("2026-10-16T10:00:00")
    with pytest.raises(ReplayArgumentError):
        parse_replay_at("2999-01-01T00:00:00+00:00")
    with pytest.raises(ReplayArgumentError):
        parse_replay_at("not-a-date")


def test_historical_replay_uses_sqlite_not_live_files(tmp_path: Path) -> None:
    _make_history_db(tmp_path)
    portal = tmp_path / "portal"
    portal.mkdir()
    (portal / "qd_scan_cache.json").write_text(json.dumps({"signals_now": ["live"]}), encoding="utf-8")
    (tmp_path / "station" / "news_collector" / "quotes_status.json").write_text(json.dumps({"live": True}), encoding="utf-8")
    snapshot = SnapshotAggregator(portal, tmp_path / "station", ttl_sec=10).snapshot("2026-10-16T12:00:00+08:00")
    assert snapshot["replay"] is True
    assert snapshot["replay_at"].endswith("+00:00")
    assert next(item for item in snapshot["sources"] if item["name"] == "quotes")["data"]["rows"][0]["code"] == "sh600000"
    assert next(item for item in snapshot["sources"] if item["name"] == "qd_scan")["data"] is None
    assert any(event["kind"] == "news" for event in snapshot["events"])
    assert any(event["kind"] == "run" for event in snapshot["events"])
    assert any("no_historical_source" in warning for warning in snapshot["warnings"])


def test_live_snapshot_does_not_gain_replay_fields(tmp_path: Path) -> None:
    snapshot = SnapshotAggregator(tmp_path, tmp_path, ttl_sec=10).snapshot()
    assert "replay" not in snapshot
    assert "replay_at" not in snapshot
