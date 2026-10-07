# -*- coding: utf-8 -*-
"""新闻时间回归；仅使用内存或临时数据库，不启动采集与推送。"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import news_collector as collector
import news_jev_chart as chart
from news_time import CST, parse_news_time, parse_full_time


class NewsTimeRegression(unittest.TestCase):
    def test_short_dates_require_explicit_valid_reference(self):
        self.assertIsNone(parse_news_time("10-07 12:00"))
        self.assertIsNone(parse_news_time("10-07 12:00", "not-a-date"))
        self.assertIsNone(parse_news_time("02-30 12:00", "2026-03-01 13:00"))
        self.assertIsNone(parse_news_time("06-30 12:00", "2026-01-01 12:00"))

    def test_year_rollover_and_future_announcement(self):
        self.assertEqual(parse_news_time("12-31 23:59", "2026-01-01 00:01").year, 2025)
        self.assertEqual(parse_news_time("01-01 00:00", "2025-12-31 23:00", "announcement").year, 2026)
        self.assertIsNone(parse_news_time("01-01 00:00", "2025-12-31 23:00", "world"))
        self.assertEqual(parse_full_time("2026-10-07T00:00:00Z").hour, 8)
        self.assertEqual(parse_full_time("2026-10-07").hour, 0)

    def test_survey_notice_date_can_follow_availability(self):
        self.assertEqual(parse_news_time("09-29 09:00", "2026-09-28 16:45", "survey"), datetime(2026, 9, 29, 9, 0, tzinfo=CST))

    def test_rss_iso_publication_time_is_not_replaced_with_collection_time(self):
        class Response:
            content = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>test</title><updated>2025-12-31T16:00:00Z</updated></entry></feed>'
            def raise_for_status(self):
                pass
        with patch.object(collector, "WORLD_FEEDS", [("SEC", "https://example.test")]), patch.object(collector.requests, "get", return_value=Response()):
            self.assertEqual(collector.fetch_world(1)[0]["ts"], "2026-01-01 00:00")

    def test_chart_does_not_discard_records_before_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "news.db")
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE news(ts TEXT, title TEXT, jev TEXT, source TEXT, created_at TEXT)")
            db.executemany("INSERT INTO news VALUES(?,?,?,?,?)", [
                ("10-07 10:00", str(i), '{"bias":"bull"}', "world", "2026-10-07 10:01") for i in range(3001)])
            db.commit()
            db.close()
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 10, 7, 12, 0, tzinfo=tz)
            with patch.object(chart, "DB", path), patch.object(chart, "datetime", Clock):
                _, rows = chart.load(24)
            self.assertEqual(len(rows), 3001)

    def test_writer_preserves_publication_year(self):
        stamp = int(datetime(2025, 12, 31, 23, 59, tzinfo=collector.CST).timestamp())
        with patch.object(collector, "http_json", return_value={
                "data": {"items": [{"id": "1", "content_text": "test", "display_time": stamp}]}}):
            self.assertEqual(collector.fetch_wallstreetcn(1)[0]["ts"], "2025-12-31 23:59")

    def test_chart_recovers_short_timestamp_and_uses_availability(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "news.db")
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE news(ts TEXT, title TEXT, jev TEXT, source TEXT, created_at TEXT)")
                db.executemany("INSERT INTO news VALUES(?,?,?,?,?)", [
                    ("12-31 23:59", "year_end", json.dumps({"bias": "bull"}), "world", "2026-01-01 00:01"),
                    ("01-02 00:00", "future_event", json.dumps({"bias": "bull"}), "announcement", "2026-01-01 00:02"),
                    ("01-01 00:00", "no_reference", json.dumps({"bias": "bull"}), "world", ""),
                ])
            db.close()
            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return cls(2026, 1, 1, 0, 3, tzinfo=tz)
            with patch.object(chart, "DB", path), patch.object(chart, "datetime", Clock):
                _, rows = chart.load(24)
            self.assertEqual([r["title"] for r in rows], ["future_event", "year_end"])
            self.assertEqual(rows[0]["t"].strftime("%Y-%m-%d %H:%M"), "2026-01-01 00:02")
            self.assertEqual(rows[0]["event_t"].strftime("%Y-%m-%d %H:%M"), "2026-01-02 00:00")


if __name__ == "__main__":
    unittest.main()
