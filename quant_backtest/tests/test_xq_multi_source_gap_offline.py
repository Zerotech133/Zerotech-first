"""Synthetic offline contracts for the read-only multi-source gap view.

Every fixture is a hand-built ``export_evidence()``-shaped dict plus
hand-built ``Supplement`` rows; there is no database, no network, and no
production wiring. These tests are intentionally not executed while the
repository's dynamic-test safety approval is outstanding.
"""

from __future__ import annotations

import unittest

from quant_backtest import xq_multi_source_gap
from quant_backtest.xq_multi_source_gap import Supplement


class MultiSourceGapOfflineTests(unittest.TestCase):
    """Merge labelled supplements onto a bundle without touching Xueqiu gaps."""

    #: Exact degradation note contract; supplementary rows never upgrade
    #: Xueqiu post coverage.
    NOTE = "supplementary data does not upgrade Xueqiu post coverage"

    @staticmethod
    def bundle(
        coverage: dict | None = None,
        gaps: dict | None = None,
        symbol: str = "SH600000",
        lane: str = "history",
    ) -> dict:
        """Build a complete synthetic evidence bundle for one stock and lane."""
        if coverage is None:
            coverage = {"status": "blocked", "reason": "member roster missing"}
        if gaps is None:
            gaps = {"type": "unknown"}
        return {
            "symbol": symbol,
            "lane": lane,
            "watermark": {
                "version": 2,
                "confirmed_page_key": "h1",
                "next_cursor": None,
                "first_status_id": "p1",
                "last_status_id": "p1",
                "coverage_started_at": 10000,
                "coverage_updated_at": 20000,
                "state": "confirmed",
                "batch_id": "b1",
                "fetched_at": 20000,
            },
            "coverage": coverage,
            "post_samples": [],
            "source_chain": {"downstream_units": "not_linked", "post_links": {}},
            "failures": [],
            "pending": {"queue": "not_initialized"},
            "gaps": gaps,
        }

    @staticmethod
    def supplement(
        source: str = "tdx",
        symbol: str = "SH600000",
        data_type: str = "quote",
        payload: dict | None = None,
        as_of: int | str | None = 1000,
    ) -> Supplement:
        """Build one synthetic supplementary market-data row."""
        if payload is None:
            payload = {"last": 10.5}
        return Supplement(
            source=source,
            symbol=symbol,
            data_type=data_type,
            payload=payload,
            as_of=as_of,
        )

    def test_blocked_coverage_with_two_supplements_keeps_gaps_verbatim(self) -> None:
        """Blocked coverage stays verbatim while supplements group by source."""
        bundle = self.bundle()
        view = xq_multi_source_gap.merge_supplement(bundle, [
            self.supplement(source="tdx", data_type="quote",
                            payload={"last": 10.5}, as_of=2000),
            self.supplement(source="tushare", data_type="timeseries",
                            payload={"close": [1.0, 2.0]}, as_of="2026-10-20"),
        ])
        # The merged coverage section is byte-for-byte the input section.
        self.assertEqual(view["coverage"], bundle["coverage"])
        self.assertEqual(view["coverage"],
                         {"status": "blocked", "reason": "member roster missing"})
        self.assertEqual(view["gaps"], bundle["gaps"])
        self.assertEqual(view["gaps"], {"type": "unknown"})
        # Every other bundle section is preserved verbatim too.
        self.assertEqual(view["symbol"], "SH600000")
        self.assertEqual(view["lane"], "history")
        self.assertEqual(view["watermark"], bundle["watermark"])
        self.assertEqual(view["post_samples"], [])
        self.assertEqual(view["failures"], [])
        self.assertEqual(view["pending"], {"queue": "not_initialized"})
        self.assertEqual(view["source_chain"], bundle["source_chain"])
        self.assertEqual(view["source_chain"]["downstream_units"], "not_linked")
        # Supplements group by source and stay distinguishable per row.
        supplements = view["supplements"]
        self.assertEqual(sorted(supplements), ["tdx", "tushare"])
        self.assertEqual(supplements["tdx"], [{
            "source": "tdx", "data_type": "quote",
            "as_of": 2000, "payload": {"last": 10.5},
        }])
        self.assertEqual(supplements["tushare"], [{
            "source": "tushare", "data_type": "timeseries",
            "as_of": "2026-10-20", "payload": {"close": [1.0, 2.0]},
        }])
        # Degradation states plainly that supplements do not upgrade coverage.
        degradation = view["degradation"]
        self.assertTrue(degradation["degraded"])
        self.assertEqual(degradation["xueqiu_posts"], "blocked")
        self.assertEqual(degradation["gaps_type"], "unknown")
        self.assertEqual(degradation["note"], self.NOTE)
        # The input bundle itself is never rewritten by the merge.
        self.assertEqual(bundle["coverage"],
                         {"status": "blocked", "reason": "member roster missing"})
        self.assertEqual(bundle["gaps"], {"type": "unknown"})

    def test_empty_coverage_with_supplement_stays_empty(self) -> None:
        """An empty lane stays empty even when supplementary rows exist."""
        bundle = self.bundle(coverage={"status": "empty"})
        view = xq_multi_source_gap.merge_supplement(bundle, [
            self.supplement(source="tdx", data_type="quote", as_of=1000),
        ])
        # Supplementary data never upgrades coverage.status from empty.
        self.assertEqual(view["coverage"], {"status": "empty"})
        self.assertEqual(view["gaps"], {"type": "unknown"})
        self.assertEqual(len(view["supplements"]["tdx"]), 1)
        degradation = view["degradation"]
        self.assertTrue(degradation["degraded"])
        self.assertEqual(degradation["xueqiu_posts"], "empty")
        self.assertEqual(degradation["gaps_type"], "unknown")
        self.assertEqual(degradation["note"], self.NOTE)

    def test_ok_coverage_without_supplements_reports_no_gap(self) -> None:
        """A healthy bundle with no supplements claims no degradation gap."""
        bundle = self.bundle(coverage={
            "status": "ok",
            "earliest_created_at_ms": 100,
            "earliest_post_id": "p1",
            "latest_created_at_ms": 300,
            "latest_post_id": "p3",
            "evidenced_post_count": 3,
            "coverage_started_at": 10000,
            "coverage_updated_at": 20000,
        })
        view = xq_multi_source_gap.merge_supplement(bundle, [])
        self.assertEqual(view["supplements"], {})
        self.assertEqual(view["coverage"], bundle["coverage"])
        self.assertEqual(view["gaps"], {"type": "unknown"})
        degradation = view["degradation"]
        self.assertFalse(degradation["degraded"])
        self.assertEqual(degradation["xueqiu_posts"], "ok")
        self.assertEqual(degradation["gaps_type"], "unknown")
        self.assertEqual(degradation["note"], self.NOTE)

    def test_invalid_supplements_are_closed_on_failure(self) -> None:
        """Symbol mismatch, bad source and empty as_of all raise ValueError."""
        bundle = self.bundle()
        # A supplement for another stock is closed on failure.
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(bundle, [
                self.supplement(symbol="SZ000001", as_of=1000)])
        # Xueqiu itself is never a valid supplement source.
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(bundle, [
                self.supplement(source="xueqiu", as_of=1000)])
        # Unknown provider labels are closed on failure too.
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(bundle, [
                self.supplement(source="wind", as_of=1000)])
        # An empty as_of label is closed on failure.
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(bundle, [
                self.supplement(as_of="")])
        # A missing as_of is closed on failure.
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(bundle, [
                self.supplement(as_of=None)])

    def test_bundle_missing_required_fields_is_closed_on_failure(self) -> None:
        """A bundle without coverage or gaps never yields a partial view."""
        base = self.bundle()
        without_coverage = {
            key: value for key, value in base.items() if key != "coverage"}
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(without_coverage, [])
        without_gaps = {
            key: value for key, value in base.items() if key != "gaps"}
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(without_gaps, [])
        without_symbol = {
            key: value for key, value in base.items() if key != "symbol"}
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(without_symbol, [])
        without_lane = {
            key: value for key, value in base.items() if key != "lane"}
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(without_lane, [])

    def test_same_source_data_type_rows_sort_by_as_of_without_merge(self) -> None:
        """Repeated source+data_type rows stay separate and as_of-ascending."""
        bundle = self.bundle(coverage={"status": "empty"})
        first = self.supplement(data_type="quote", payload={"last": 1.0}, as_of=3000)
        duplicate = self.supplement(data_type="quote", payload={"last": 2.0}, as_of=2000)
        duplicate_again = self.supplement(
            data_type="quote", payload={"last": 2.0}, as_of=2000)
        last = self.supplement(data_type="quote", payload={"last": 3.0}, as_of=1000)
        view = xq_multi_source_gap.merge_supplement(
            bundle, [first, duplicate, duplicate_again, last])
        rows = view["supplements"]["tdx"]
        # Four rows in, four rows out: nothing is deduplicated or merged.
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["as_of"] for row in rows],
                         [1000, 2000, 2000, 3000])
        self.assertEqual([row["payload"]["last"] for row in rows],
                         [3.0, 2.0, 2.0, 1.0])
        self.assertTrue(all(row["source"] == "tdx" for row in rows))
        self.assertTrue(all(row["data_type"] == "quote" for row in rows))
        # Coverage and gaps are still untouched by the extra rows.
        self.assertEqual(view["coverage"], {"status": "empty"})
        self.assertEqual(view["gaps"], {"type": "unknown"})
        self.assertTrue(view["degradation"]["degraded"])
        self.assertEqual(view["degradation"]["note"], self.NOTE)

    def test_as_of_rejects_negative_and_accepts_zero_boundary(self) -> None:
        """Negative millisecond as_of is illegal; zero is the legal bound."""
        bundle = self.bundle()
        # A negative as_of is closed on failure, matching family timestamps.
        with self.assertRaises(ValueError):
            xq_multi_source_gap.merge_supplement(bundle, [
                self.supplement(as_of=-1)])
        # Zero is the legal lower boundary and merges normally.
        view = xq_multi_source_gap.merge_supplement(bundle, [
            self.supplement(as_of=0)])
        self.assertEqual(view["supplements"]["tdx"], [{
            "source": "tdx", "data_type": "quote",
            "as_of": 0, "payload": {"last": 10.5},
        }])
        self.assertEqual(view["degradation"]["note"], self.NOTE)

    def test_non_dict_payload_is_closed_on_failure_at_construction(self) -> None:
        """None or list payloads raise ValueError when the row is built."""
        with self.assertRaises(ValueError):
            Supplement(source="tdx", symbol="SH600000", data_type="quote",
                       payload=None, as_of=1000)
        with self.assertRaises(ValueError):
            Supplement(source="tdx", symbol="SH600000", data_type="quote",
                       payload=[1, 2], as_of=1000)


if __name__ == "__main__":
    unittest.main()
