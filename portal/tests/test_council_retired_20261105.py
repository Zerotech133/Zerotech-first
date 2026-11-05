# -*- coding: utf-8 -*-
"""2026-11-05 OPS-COUNCIL-RETIRE-20261105 regression tests.

The council shadow source has had no producer since 2026-10-15, so it is now
explicitly retired instead of being reported as a daily P2 stale alert.  The
historical /council page and _council_stats stay available; this test only
freezes the alerting contract.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

# Import api_common first: it re-exports the alert functions and resolves the
# intentional circular import (api_common_alert -> api_common -> alert names).
import api_common  # noqa: E402,F401

alert = importlib.import_module("api_common_alert")
zhuge = importlib.import_module("api_zhuge_brief")

BEIJING = timezone(timedelta(hours=8))


def test_council_retired_in_source_health_and_alerts(monkeypatch, tmp_path):
    qbt = tmp_path / "quant_backtest"
    reports = qbt / "reports"
    data = qbt / "data"
    for path in (reports, data, tmp_path / "reports"):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(alert, "QBT_DIR", str(qbt))
    monkeypatch.setattr(alert, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(alert, "STATION", str(tmp_path))
    monkeypatch.setattr(alert, "ALERT_FRESH_WINDOW", timedelta(hours=18))

    def _must_not_run():
        raise AssertionError("retired council must not read _council_stats")

    monkeypatch.setattr(alert, "_council_stats", _must_not_run)

    result = alert.build_alerts()
    council = result["source_health"]["sources"]["council"]
    assert council["status"] == "retired"
    assert council["reason"] == "producer_retired"
    assert council["as_of"] is None
    assert all(row["name"] != "🏛 投委会" for row in result["alerts"])


def test_zhuge_skips_retired_and_accepts_empty_as_healthy():
    clock = datetime(2026, 11, 5, 9, 0, tzinfo=BEIJING)
    raw = {
        "alerts": {
            "ok": True,
            "source_health": {
                "sources": {
                    "council": {
                        "status": "retired",
                        "reason": "producer_retired",
                        "as_of": None,
                    },
                    "nine_dim": {
                        "status": "empty",
                        "as_of": "2026-11-05T06:43:53+08:00",
                    },
                    "weak": {
                        "status": "stale",
                        "as_of": "2026-10-28T15:35:00+08:00",
                    },
                }
            },
        }
    }
    out = zhuge.build_brief(raw, clock)
    ids = {row["id"] for row in out["suggestions"]}
    assert "alert:council" not in ids
    assert "alert:nine_dim" not in ids
    assert "alert:weak" in ids
    # Only a genuinely abnormal source may use the "回原账本核对" wording.
    # empty is a legitimate zero-alert state and must not be turned into a
    # stale-source/manual-review suggestion.
    weak = next(row for row in out["suggestions"] if row["id"] == "alert:weak")
    assert "需回原账本核对" in weak["reason"]
    assert all("需回原账本核对" not in row["reason"]
               for row in out["suggestions"]
               if row["id"] in ("alert:nine_dim", "alert:council"))
