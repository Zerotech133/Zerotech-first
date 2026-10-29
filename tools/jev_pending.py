# -*- coding: utf-8 -*-
"""Print current JEV pending count (single integer) for wrappers/scripts (2026-10-29)."""
import sqlite3
import sys

DB = r"C:\aios\news_collector\news.db"


def main() -> int:
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True, timeout=10)
        n = conn.execute("SELECT count(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0]
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print("ERR:%s" % exc)
        return 1
    print(int(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
