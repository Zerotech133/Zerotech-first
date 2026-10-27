# -*- coding: utf-8 -*-
"""只在临时 SQLite 上验证计划、备份、并发保护与逐行回退。"""

import os
import sqlite3
import tempfile
import unittest

try:
    import news_time_migration as migration
except ImportError:
    migration = None


class MigrationRegression(unittest.TestCase):
    def test_prepare_apply_rollback_preserves_unrelated_records(self):
        self.assertIsNotNone(migration, "需要独立的新闻时间迁移工具")
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "news.db")
            backup = os.path.join(temp, "backup.db")
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, created_at TEXT)"
            )
            db.executemany(
                "INSERT INTO news VALUES(?,?,?,?)",
                [
                    ("old", "world", "12-31 23:59", "2026-01-01 00:01"),
                    ("future", "announcement", "01-02 00:00", "2026-01-01 00:01"),
                    ("bad", "world", "10-07 12:00", ""),
                ],
            )
            db.commit()
            plan = migration.prepare(path)
            self.assertEqual(len(plan["changes"]), 2)
            self.assertEqual(len(plan["unresolved"]), 1)
            self.assertEqual(
                db.execute("SELECT ts FROM news WHERE id='old'").fetchone()[0],
                "12-31 23:59",
            )
            db.execute(
                "INSERT INTO news VALUES('new','world','2026-01-01 00:02','2026-01-01 00:02')"
            )
            db.commit()
            self.assertEqual(migration.apply(plan, backup), 2)
            self.assertEqual(
                db.execute("SELECT ts FROM news WHERE id='old'").fetchone()[0],
                "2025-12-31 23:59",
            )
            self.assertEqual(migration.apply(plan, os.path.join(temp, "again.db")), 0)
            self.assertEqual(migration.rollback(plan), 2)
            self.assertEqual(
                db.execute("SELECT ts FROM news WHERE id='old'").fetchone()[0],
                "12-31 23:59",
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM news").fetchone()[0], 4)
            db.close()
            saved = sqlite3.connect(backup)
            self.assertEqual(
                saved.execute("SELECT ts FROM news WHERE id='old'").fetchone()[0],
                "12-31 23:59",
            )
            saved.close()

    def test_changed_target_aborts_all_updates(self):
        self.assertIsNotNone(migration, "需要独立的新闻时间迁移工具")
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "news.db")
            db = sqlite3.connect(path)
            db.execute(
                "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, created_at TEXT)"
            )
            db.execute(
                "INSERT INTO news VALUES('old','world','12-31 23:59','2026-01-01 00:01')"
            )
            db.commit()
            plan = migration.prepare(path)
            db.execute("UPDATE news SET ts='12-30 23:59' WHERE id='old'")
            db.commit()
            with self.assertRaises(ValueError):
                migration.apply(plan, os.path.join(temp, "backup.db"))
            self.assertEqual(
                db.execute("SELECT ts FROM news").fetchone()[0], "12-30 23:59"
            )
            db.close()


if __name__ == "__main__":
    unittest.main()
