# -*- coding: utf-8 -*-
"""lineage_freshness（P1-A 血缘新鲜度维度）测试。"""

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

_TEST_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _TEST_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

spec = importlib.util.spec_from_file_location(
    "lineage_freshness_under_test",
    _AGENT_PLATFORM / "adapters" / "lineage_freshness.py",
)
LF = importlib.util.module_from_spec(spec)
sys.modules["lineage_freshness_under_test"] = LF
spec.loader.exec_module(LF)


def _row(urn="parquet:tdx/ts_daily", status="ok", started="2026-10-16 15:30:00", **kw):
    row = {
        "output_table": urn,
        "job": "x",
        "step": "y",
        "status": status,
        "started_at": started,
        "rows_written": 100,
    }
    row.update(kw)
    return row


NOW = date(2026, 10, 19)  # 周一（各测试类共用）


class TradeDaysTests(unittest.TestCase):
    def test_weekend_excluded(self):
        # 2026-10-16 周五 → 10-19 周一：跨周末，交易日数=1（周一）
        n = LF.trade_days_between(date(2026, 10, 16), date(2026, 10, 19))
        self.assertEqual(n, 1)

    def test_holiday_excluded(self):
        n = LF.trade_days_between(
            date(2026, 10, 1),
            date(2026, 10, 8),
            holidays=["2026-10-02", "2026-10-05", "2026-10-06"],
        )
        # 10/1 周四。区间 10/2~10/8：周末 3,4；节假日 2,5,6
        # → 交易日 = 10/7(三) + 10/8(四) = 2
        self.assertEqual(n, 2)

    def test_future_or_same_day_is_zero(self):
        self.assertEqual(
            LF.trade_days_between(date(2026, 10, 19), date(2026, 10, 16)), 0
        )
        self.assertEqual(
            LF.trade_days_between(date(2026, 10, 16), date(2026, 10, 16)), 0
        )


class AssessTests(unittest.TestCase):
    def test_fresh_within_cadence(self):
        out = LF.assess([_row(started="2026-10-16 15:30:00")], now=NOW)
        self.assertEqual(out[0]["freshness"], "fresh")

    def test_stale_beyond_cadence_and_grace(self):
        # 10-14(周三) 产出，至 10-19(周一) 跨 3 个交易日（15,16,19）> 1+1
        out = LF.assess([_row(started="2026-10-14 15:30:00")], now=NOW)
        self.assertEqual(out[0]["freshness"], "stale")
        self.assertEqual(out[0]["age_trade_days"], 3)

    def test_watch_in_grace_band(self):
        # 10-15(周四) 产出，至 10-19 跨 2 个交易日（16,19）= cadence+grace
        out = LF.assess([_row(started="2026-10-15 15:30:00")], now=NOW)
        self.assertEqual(out[0]["freshness"], "watch")

    def test_broken_rows_left_to_break_domain(self):
        out = LF.assess([_row(status="fail")], now=NOW)
        self.assertEqual(out[0]["freshness"], "broken")

    def test_unknown_when_date_unparseable(self):
        out = LF.assess([_row(started="")], now=NOW)
        self.assertEqual(out[0]["freshness"], "unknown")

    def test_datetime_object_from_pg(self):
        # psycopg2 直查行 started_at 是 datetime（19:46 实证判例）：
        # datetime 是 date 子类，须取 .date() 再比较
        from datetime import datetime as dt

        out = LF.assess([_row(started=dt(2026, 10, 14, 15, 30, 0))], now=NOW)
        self.assertEqual(out[0]["freshness"], "stale")

    def test_input_rows_not_mutated(self):
        row = _row(started="2026-10-14 15:30:00")
        LF.assess([row], now=NOW)
        self.assertNotIn("freshness", row)  # ro 判例：不改入参

    def test_per_table_cadence_overrides_default(self):
        cfg = {
            "tables": {
                "parquet:tdx/weekly": {"cadence_trade_days": 5, "grace_trade_days": 1}
            },
            "holidays": [],
        }
        # 跨 3 交易日：默认规则=stale，周频表=fresh
        out = LF.assess(
            [
                _row(urn="parquet:tdx/ts_daily", started="2026-10-14 15:30:00"),
                _row(urn="parquet:tdx/weekly", started="2026-10-14 15:30:00"),
            ],
            cfg=cfg,
            now=NOW,
        )
        self.assertEqual(out[0]["freshness"], "stale")
        self.assertEqual(out[1]["freshness"], "fresh")


class LoadCadenceTests(unittest.TestCase):
    def test_missing_file_fail_open(self):
        cfg = LF.load_cadence(Path("Z:/no/such/file.json"))
        self.assertEqual(cfg, {"tables": {}, "holidays": []})

    def test_real_config_loads(self):
        cfg = LF.load_cadence()
        self.assertIsInstance(cfg["tables"], dict)
        self.assertIsInstance(cfg["holidays"], list)

    def test_summary_shape(self):
        assessed = LF.assess(
            [
                _row(urn="a", started="2026-10-14 15:30:00"),
                _row(urn="b", started="2026-10-16 15:30:00"),
            ],
            now=NOW,
        )
        s = LF.summarize(assessed)
        self.assertEqual([x["table"] for x in s["stale"]], ["a"])
        self.assertEqual(s["fresh_count"], 1)
        self.assertEqual(s["total"], 2)


class BreaksPayloadTests(unittest.TestCase):
    def test_assess_breaks_payload(self):
        payload = {
            "broken": [_row(status="fail")],
            "all_latest": [
                _row(urn="a", started="2026-10-14 15:30:00"),
                _row(urn="b", started="2026-10-16 15:30:00"),
            ],
        }
        out = LF.assess_breaks_payload(payload, now=NOW)
        self.assertEqual(out["summary"]["stale"][0]["table"], "a")

    def test_empty_payload(self):
        out = LF.assess_breaks_payload({}, now=NOW)
        self.assertEqual(out["summary"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
