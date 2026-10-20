"""Synthetic offline contracts for the advisory history-priority policy.

Every fixture is a hand-built ``export_evidence()``-shaped dict; there is no
database, no network, and no production wiring. These tests are
intentionally not executed while the repository's dynamic-test safety
approval is outstanding. Failure semantics are uniformly stated as
"closed on failure".
"""

from __future__ import annotations

import unittest

from quant_backtest import xq_history_priority


class HistoryPriorityOfflineTests(unittest.TestCase):
    """Score, alert and rank synthetic bundles without touching any store."""

    @staticmethod
    def bundle(
        symbol: str = "SH600000",
        lane: str = "history",
        coverage: dict | None = None,
        gaps: dict | None = None,
        failures: list | None = None,
        pending: dict | None = None,
        watermark: dict | None = None,
    ) -> dict:
        """Build a complete synthetic evidence bundle for one stock and lane."""
        if coverage is None:
            coverage = {"status": "ok", "evidenced_post_count": 3}
        if gaps is None:
            gaps = {"type": "unknown"}
        if failures is None:
            failures = []
        if pending is None:
            pending = {"queue": "initialized", "state": "pending",
                       "page_key": "h1", "claim_id": None}
        if watermark is None:
            watermark = {
                "version": 2,
                "confirmed_page_key": "h1",
                "next_cursor": "c9",
                "first_status_id": "p1",
                "last_status_id": "p3",
                "coverage_started_at": 10000,
                "coverage_updated_at": 20000,
                "state": "confirmed",
                "batch_id": "b1",
                "fetched_at": 20000,
            }
        return {
            "symbol": symbol,
            "lane": lane,
            "watermark": watermark,
            "coverage": coverage,
            "post_samples": [],
            "source_chain": {"downstream_units": "not_linked", "post_links": {}},
            "failures": failures,
            "pending": pending,
            "gaps": gaps,
        }

    @staticmethod
    def failure(status: str = "403", detail: str = "risk block") -> dict:
        """Build one synthetic recent page-attempt failure row."""
        return {
            "attempt_id": "a1",
            "batch_id": "b1",
            "page_key": "h1",
            "status": status,
            "detail": detail,
            "fetched_at": 20000,
        }

    def test_history_unfinished_with_cursor_out_ranks_unknown_gap(self) -> None:
        """A resumable unfinished backlog outranks a plain unknown gap."""
        unfinished = self.bundle(
            symbol="SH600001",
            gaps={"type": "history_unfinished", "next_cursor": "c9"},
        )
        unknown = self.bundle(symbol="SH600002", gaps={"type": "unknown"})
        ranked = xq_history_priority.rank_history_targets(
            [unknown, unfinished], budget_units=2)
        # Sorting is by priority score descending, not by input order.
        self.assertEqual([row.symbol for row in ranked], ["SH600001", "SH600002"])
        self.assertGreater(ranked[0].priority_score, ranked[1].priority_score)
        # The gap factor is spelled out in the reasons, not a black box.
        joined = " ".join(ranked[0].reasons)
        self.assertIn("gap_factor", joined)
        self.assertIn("history_unfinished", joined)
        self.assertIn("next_cursor", joined)
        self.assertIn("gap_factor", " ".join(ranked[1].reasons))
        # Every score is advisory only and carries the recommendation marker.
        for row in ranked:
            self.assertTrue(row.advisory)
            self.assertTrue(all(isinstance(item, str) for item in row.reasons))
        # The unknown-gap stock gets no fabricated gap bonus.
        self.assertAlmostEqual(ranked[1].priority_score, 0.0)

    def test_marginal_output_hint_recorded_or_marked_missing(self) -> None:
        """A supplied hint is scored; an absent one is marked, not invented."""
        bundle = self.bundle(
            gaps={"type": "history_unfinished", "next_cursor": "c9"})
        with_hint = xq_history_priority.score_history_backlog(
            bundle, marginal_output_hint=2.5, budget_units=1)
        joined = " ".join(with_hint.reasons)
        self.assertIn("marginal_factor", joined)
        self.assertIn("2.5", joined)
        # The hint contributes exactly its weight to the explainable sum.
        self.assertAlmostEqual(
            with_hint.priority_score,
            xq_history_priority.GAP_UNFINISHED_WITH_CURSOR_WEIGHT
            + xq_history_priority.MARGINAL_OUTPUT_WEIGHT * 2.5)
        without_hint = xq_history_priority.score_history_backlog(
            bundle, budget_units=1)
        joined_missing = " ".join(without_hint.reasons)
        self.assertIn("marginal_output_hint missing", joined_missing)
        self.assertIn("not fabricated", joined_missing)
        # Missing means neutral: the score equals the score with hint 0.
        zero_hint = xq_history_priority.score_history_backlog(
            bundle, marginal_output_hint=0.0, budget_units=1)
        self.assertAlmostEqual(
            without_hint.priority_score, zero_hint.priority_score)

    def test_dense_risk_failures_demote_stock_with_reasons(self) -> None:
        """Same gap, but 403-dense failures sink a stock below a clean one."""
        clean = self.bundle(
            symbol="SH600001",
            gaps={"type": "history_unfinished", "next_cursor": "c9"},
        )
        cooled = self.bundle(
            symbol="SH600002",
            gaps={"type": "history_unfinished", "next_cursor": "c8"},
            failures=[self.failure("403"), self.failure("timeout"),
                      self.failure("403")],
        )
        ranked = xq_history_priority.rank_history_targets(
            [cooled, clean], budget_units=2)
        self.assertEqual([row.symbol for row in ranked], ["SH600001", "SH600002"])
        joined = " ".join(ranked[1].reasons)
        self.assertIn("failure_factor", joined)
        self.assertIn("risk-cooling", joined)
        # The demotion is exactly the documented penalty on equal gaps,
        # normalized by the same budget divisor (2 units here).
        self.assertAlmostEqual(
            ranked[0].priority_score - ranked[1].priority_score,
            -xq_history_priority.RISK_COOLING_PENALTY / 2.0)
        # Trend inputs echo the recent risk counts for downstream aggregation.
        self.assertEqual(ranked[1].trend_inputs["recent_failure_count"], 3)
        self.assertEqual(ranked[1].trend_inputs["risk_failure_count_recent"], 3)
        self.assertEqual(
            ranked[1].trend_inputs["trend_aggregation"],
            xq_history_priority.TREND_AGGREGATION)

    def test_blocked_coverage_stays_out_of_top_and_alerts(self) -> None:
        """Blocked attribution never ranks at the top and raises its alert."""
        blocked = self.bundle(
            symbol="SH600001",
            coverage={"status": "blocked", "reason": "member roster missing"},
            gaps={"type": "history_unfinished", "next_cursor": "c9"},
        )
        plain = self.bundle(symbol="SH600002", gaps={"type": "unknown"})
        ranked = xq_history_priority.rank_history_targets(
            [blocked, plain], budget_units=2)
        # Even with the biggest gap factor, blocked attribution sinks last.
        self.assertEqual([row.symbol for row in ranked], ["SH600002", "SH600001"])
        joined = " ".join(ranked[1].reasons)
        self.assertIn("attribution blocked", joined)
        self.assertIn("blocked_factor", joined)
        alerts = xq_history_priority.gap_alerts(blocked)
        types = [alert.alert_type for alert in alerts]
        self.assertIn("attribution_blocked", types)
        for alert in alerts:
            if alert.alert_type == "attribution_blocked":
                self.assertEqual(alert.severity, "warning")
                self.assertEqual(
                    alert.evidence["coverage_status"], "blocked")
                self.assertEqual(
                    alert.evidence["coverage_reason"], "member roster missing")

    def test_gap_alerts_cover_each_type_and_healthy_stock_is_silent(self) -> None:
        """Each rule fires once when triggered; healthy bundles stay quiet."""
        unfinished_with_cursor = self.bundle(
            symbol="SH600001",
            gaps={"type": "history_unfinished", "next_cursor": "c9"},
        )
        unfinished_no_cursor = self.bundle(
            symbol="SH600002",
            gaps={"type": "history_unfinished", "next_cursor": None},
        )
        blocked = self.bundle(
            symbol="SH600003",
            coverage={"status": "blocked", "reason": "attribution tie broken"},
        )
        paused = self.bundle(
            symbol="SH600004",
            pending={"queue": "initialized", "state": "paused",
                     "page_key": "h7", "claim_id": None},
        )
        cooled = self.bundle(
            symbol="SH600005",
            failures=[self.failure("timeout")],
        )
        healthy = self.bundle(symbol="SH600006", gaps={"type": "unknown"})

        alerts_1 = xq_history_priority.gap_alerts(unfinished_with_cursor)
        self.assertEqual([a.alert_type for a in alerts_1], ["history_unfinished"])
        self.assertEqual(alerts_1[0].severity, "warning")
        self.assertIn("next_cursor present", alerts_1[0].detail)
        self.assertEqual(alerts_1[0].evidence["gaps_type"], "history_unfinished")
        self.assertEqual(alerts_1[0].evidence["next_cursor"], "c9")

        alerts_2 = xq_history_priority.gap_alerts(unfinished_no_cursor)
        self.assertEqual([a.alert_type for a in alerts_2], ["history_unfinished"])
        self.assertIn("next_cursor missing", alerts_2[0].detail)
        self.assertIsNone(alerts_2[0].evidence["next_cursor"])

        alerts_3 = xq_history_priority.gap_alerts(blocked)
        self.assertEqual([a.alert_type for a in alerts_3], ["attribution_blocked"])
        self.assertEqual(alerts_3[0].severity, "warning")

        alerts_4 = xq_history_priority.gap_alerts(paused)
        self.assertEqual([a.alert_type for a in alerts_4], ["collection_paused"])
        self.assertEqual(alerts_4[0].severity, "info")
        self.assertEqual(alerts_4[0].evidence["pending_state"], "paused")
        self.assertEqual(alerts_4[0].evidence["pending_page_key"], "h7")

        alerts_5 = xq_history_priority.gap_alerts(cooled)
        self.assertEqual([a.alert_type for a in alerts_5], ["risk_cooling"])
        self.assertEqual(alerts_5[0].severity, "warning")
        self.assertEqual(
            alerts_5[0].evidence["recent_failure_statuses"], ["timeout"])
        self.assertEqual(alerts_5[0].evidence["risk_failure_count_recent"], 1)

        # A healthy stock produces zero alerts: no noise is manufactured.
        self.assertEqual(xq_history_priority.gap_alerts(healthy), [])

    def test_invalid_inputs_are_closed_on_failure(self) -> None:
        """Zero/bool budgets and malformed bundles raise ValueError only."""
        bundle = self.bundle()
        # budget_units == 0 is closed on failure.
        with self.assertRaises(ValueError):
            xq_history_priority.score_history_backlog(bundle, budget_units=0)
        with self.assertRaises(ValueError):
            xq_history_priority.rank_history_targets([bundle], budget_units=0)
        # Booleans are never counts: closed on failure.
        with self.assertRaises(ValueError):
            xq_history_priority.score_history_backlog(bundle, budget_units=True)
        with self.assertRaises(ValueError):
            xq_history_priority.score_history_backlog(bundle, budget_units=False)
        with self.assertRaises(ValueError):
            xq_history_priority.rank_history_targets([bundle], budget_units=True)
        # Missing coverage or gaps never yields a partial score or alert list.
        base = self.bundle()
        without_coverage = {
            key: value for key, value in base.items() if key != "coverage"}
        without_gaps = {
            key: value for key, value in base.items() if key != "gaps"}
        without_symbol = {
            key: value for key, value in base.items() if key != "symbol"}
        without_lane = {
            key: value for key, value in base.items() if key != "lane"}
        for broken in (without_coverage, without_gaps,
                       without_symbol, without_lane):
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(broken)
            with self.assertRaises(ValueError):
                xq_history_priority.gap_alerts(broken)
            with self.assertRaises(ValueError):
                xq_history_priority.rank_history_targets([broken])
        # One bad bundle rejects the whole ranking: no partial result.
        with self.assertRaises(ValueError):
            xq_history_priority.rank_history_targets(
                [self.bundle(symbol="SH600001"), without_gaps])
        # A negative marginal hint is closed on failure, never clamped.
        with self.assertRaises(ValueError):
            xq_history_priority.score_history_backlog(
                bundle, marginal_output_hint=-1.0)

    def test_rank_truncates_to_budget_and_breaks_ties_by_symbol(self) -> None:
        """budget_units caps the suggested target count deterministically."""
        first = self.bundle(symbol="SH600002", gaps={"type": "unknown"})
        second = self.bundle(symbol="SH600001", gaps={"type": "unknown"})
        third = self.bundle(symbol="SH600003", gaps={"type": "unknown"})
        ranked = xq_history_priority.rank_history_targets(
            [first, second, third], budget_units=2)
        # Equal scores fall back to symbol lexicographic order, then truncate.
        self.assertEqual([row.symbol for row in ranked], ["SH600001", "SH600002"])
        # A larger budget keeps every target in the same stable order.
        full = xq_history_priority.rank_history_targets(
            [first, second, third], budget_units=3)
        self.assertEqual([row.symbol for row in full],
                         ["SH600001", "SH600002", "SH600003"])

    def test_blank_next_cursor_is_closed_on_failure(self) -> None:
        """Empty or whitespace cursors are malformed, never resumable.

        closed on failure: every entry point rejects a blank
        ``next_cursor`` with ``ValueError``; the missing-cursor branch
        (``None``) keeps the documented weight and is unaffected.
        """
        for blank in ("", "   ", "\t", "\n", " \t\n "):
            malformed = self.bundle(
                gaps={"type": "history_unfinished", "next_cursor": blank})
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.gap_alerts(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.rank_history_targets([malformed])
        # Non-string cursors are malformed too: closed on failure.
        for bogus in (0, 123, ["c9"], {"k": "v"}, True):
            malformed = self.bundle(
                gaps={"type": "history_unfinished", "next_cursor": bogus})
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.gap_alerts(malformed)
        # None is the legitimate no-cursor branch: weight stays 1.0.
        no_cursor = self.bundle(
            symbol="SH600001",
            gaps={"type": "history_unfinished", "next_cursor": None})
        scored = xq_history_priority.score_history_backlog(no_cursor)
        self.assertAlmostEqual(
            scored.priority_score,
            xq_history_priority.GAP_UNFINISHED_NO_CURSOR_WEIGHT)
        alerts = xq_history_priority.gap_alerts(no_cursor)
        self.assertEqual([a.alert_type for a in alerts], ["history_unfinished"])
        self.assertIn("next_cursor missing", alerts[0].detail)
        self.assertIsNone(alerts[0].evidence["next_cursor"])
        # A nonempty cursor keeps the documented resumable weight 3.0.
        resumable = self.bundle(
            symbol="SH600002",
            gaps={"type": "history_unfinished", "next_cursor": "c9"})
        scored_cursor = xq_history_priority.score_history_backlog(resumable)
        self.assertAlmostEqual(
            scored_cursor.priority_score,
            xq_history_priority.GAP_UNFINISHED_WITH_CURSOR_WEIGHT)
        # Validity is judged after stripping: " c9 " is a nonempty cursor.
        padded = self.bundle(
            symbol="SH600003",
            gaps={"type": "history_unfinished", "next_cursor": " c9 "})
        scored_padded = xq_history_priority.score_history_backlog(padded)
        self.assertAlmostEqual(
            scored_padded.priority_score,
            xq_history_priority.GAP_UNFINISHED_WITH_CURSOR_WEIGHT)

    def test_rank_same_symbol_lane_tie_is_input_order_invariant(self) -> None:
        """Equal-score dual-lane rows rank identically for any input order.

        Same symbol, same raw score, one ``new`` and one ``history`` lane:
        both input permutations must produce the identical output order.
        """
        new_row = self.bundle(symbol="SH600000", lane="new",
                              gaps={"type": "unknown"})
        history_row = self.bundle(symbol="SH600000", lane="history",
                                  gaps={"type": "unknown"})
        forward = xq_history_priority.rank_history_targets(
            [new_row, history_row], budget_units=2)
        backward = xq_history_priority.rank_history_targets(
            [history_row, new_row], budget_units=2)
        # The two bundles share the same raw score (unknown gap, no
        # failures, coverage ok), so only the tie-break decides the order.
        self.assertAlmostEqual(
            forward[0].priority_score, forward[1].priority_score)
        self.assertEqual(len(forward), 2)
        self.assertEqual(
            [(row.symbol, row.lane) for row in forward],
            [(row.symbol, row.lane) for row in backward])

    def test_rank_lane_tie_break_puts_new_before_history(self) -> None:
        """On equal scores the new lane is ranked before the history lane."""
        new_row = self.bundle(symbol="SH600000", lane="new",
                              gaps={"type": "unknown"})
        history_row = self.bundle(symbol="SH600000", lane="history",
                                  gaps={"type": "unknown"})
        for order in ([new_row, history_row], [history_row, new_row]):
            ranked = xq_history_priority.rank_history_targets(
                order, budget_units=2)
            self.assertEqual([row.lane for row in ranked], ["new", "history"])
            self.assertEqual([row.symbol for row in ranked],
                             ["SH600000", "SH600000"])

    def test_hint_rejects_bool_str_nan_inf(self) -> None:
        """marginal_output_hint rejects bool, str, NaN, +inf and -inf."""
        bundle = self.bundle()
        for bogus in (True, False, "2.5", float("nan"),
                      float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(
                    bundle, marginal_output_hint=bogus)

    def test_budget_units_rejects_float_and_str(self) -> None:
        """budget_units rejects float and str so only positive ints divide scores."""
        bundle = self.bundle()
        for bogus in (1.0, 2.5, "1", "2"):
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(
                    bundle, budget_units=bogus)
            with self.assertRaises(ValueError):
                xq_history_priority.rank_history_targets(
                    [bundle], budget_units=bogus)

    def test_required_bundle_rejects_malformed_optional_sections(self) -> None:
        """failures/pending/watermark with the wrong container shape are rejected."""
        for failures in ("oops", {"a": 1}, 7, [1, 2], [{"ok": 1}, "bad"]):
            malformed = self.bundle(failures=failures)
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.gap_alerts(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.rank_history_targets([malformed])
        for pending in ("paused", ["state"], 3):
            malformed = self.bundle(pending=pending)
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.gap_alerts(malformed)
        for watermark in ("w", [1], 5):
            malformed = self.bundle(watermark=watermark)
            with self.assertRaises(ValueError):
                xq_history_priority.score_history_backlog(malformed)
            with self.assertRaises(ValueError):
                xq_history_priority.gap_alerts(malformed)

    def test_score_result_rejects_illegal_direct_construction(self) -> None:
        """ScoreResult rejects advisory=False, non-number scores and bad reasons."""
        with self.assertRaises(ValueError):
            xq_history_priority.ScoreResult(
                symbol="SH600000", lane="history", priority_score=1.0,
                reasons=["ok"], advisory=False)
        with self.assertRaises(ValueError):
            xq_history_priority.ScoreResult(
                symbol="SH600000", lane="history", priority_score="1.0",
                reasons=["ok"], advisory=True)
        with self.assertRaises(ValueError):
            xq_history_priority.ScoreResult(
                symbol="SH600000", lane="history", priority_score=1.0,
                reasons=[1, 2], advisory=True)
        with self.assertRaises(ValueError):
            xq_history_priority.ScoreResult(
                symbol="SH600000", lane="history", priority_score=1.0,
                reasons="ok", advisory=True)
        with self.assertRaises(ValueError):
            xq_history_priority.ScoreResult(
                symbol="SH600000", lane="history", priority_score=1.0,
                reasons=["ok"], trend_inputs="raw", advisory=True)
        with self.assertRaises(ValueError):
            xq_history_priority.ScoreResult(
                symbol="", lane="history", priority_score=1.0,
                reasons=["ok"], advisory=True)

    def test_gap_alert_rejects_illegal_direct_construction(self) -> None:
        """GapAlert rejects out-of-enum alert_type/severity and non-dict evidence."""
        with self.assertRaises(ValueError):
            xq_history_priority.GapAlert(
                symbol="SH600000", lane="history", alert_type="weird",
                severity="warning", detail="d", evidence={})
        with self.assertRaises(ValueError):
            xq_history_priority.GapAlert(
                symbol="SH600000", lane="history", alert_type="risk_cooling",
                severity="critical", detail="d", evidence={})
        with self.assertRaises(ValueError):
            xq_history_priority.GapAlert(
                symbol="SH600000", lane="history", alert_type="risk_cooling",
                severity="warning", detail="d", evidence="raw")
        with self.assertRaises(ValueError):
            xq_history_priority.GapAlert(
                symbol="SH600000", lane="history", alert_type="risk_cooling",
                severity="warning", detail="", evidence={})

    def test_is_risk_status_tolerates_none_and_int_status(self) -> None:
        """_is_risk_status coerces via str(): None is non-risk, int 403 counts as risk."""
        self.assertFalse(xq_history_priority._is_risk_status(None))
        self.assertFalse(xq_history_priority._is_risk_status(500))
        self.assertTrue(xq_history_priority._is_risk_status(403))
        self.assertTrue(xq_history_priority._is_risk_status("403"))
        self.assertTrue(xq_history_priority._is_risk_status("timeout"))
        self.assertFalse(xq_history_priority._is_risk_status("ok"))
        # Scoring tolerates these statuses instead of rejecting them.
        tolerant = self.bundle(
            gaps={"type": "unknown"},
            failures=[self.failure(status=None), self.failure(status=None),
                      self.failure(status=500)])
        scored = xq_history_priority.score_history_backlog(tolerant)
        self.assertEqual(scored.trend_inputs["risk_failure_count_recent"], 0)
        int_risk = self.bundle(
            gaps={"type": "unknown"},
            failures=[self.failure(status=403), self.failure(status=403),
                      self.failure(status=403)])
        scored_int = xq_history_priority.score_history_backlog(int_risk)
        self.assertEqual(
            scored_int.trend_inputs["risk_failure_count_recent"], 3)

    def test_unknown_coverage_status_keeps_blocked_factor_zero(self) -> None:
        """A coverage status outside ok/blocked/empty is quoted verbatim, never penalized."""
        weird = self.bundle(
            gaps={"type": "unknown"},
            coverage={"status": "weird", "evidenced_post_count": 3})
        scored = xq_history_priority.score_history_backlog(weird)
        self.assertAlmostEqual(scored.priority_score, 0.0)
        joined = " ".join(scored.reasons)
        self.assertIn("blocked_factor=+0.0", joined)
        self.assertIn("coverage.status=weird", joined)
        self.assertNotIn("attribution blocked", joined)
        # An unknown status raises no attribution_blocked alert either.
        self.assertEqual(xq_history_priority.gap_alerts(weird), [])

    def test_score_result_rejects_bool_priority_score(self) -> None:
        """A bool priority_score is a number trap, not a score.

        closed on failure: ``True``/``False`` are ``int`` subclasses, so the
        validator checks ``bool`` first and rejects them with
        ``ValueError("priority_score must be a number")`` before any result
        exists.
        """
        for bogus in (True, False):
            with self.assertRaisesRegex(
                    ValueError, "priority_score must be a number"):
                xq_history_priority.ScoreResult(
                    symbol="SH600000", lane="history", priority_score=bogus,
                    reasons=["ok"], advisory=True)

    def test_gap_alert_rejects_blank_symbol_and_lane(self) -> None:
        """GapAlert direct construction closes on blank symbol or lane.

        ``GapAlert.__post_init__`` validates both identifier fields with
        the same nonempty-string rule as the scoring path; empty,
        whitespace-only and non-string values are all closed on failure
        with the verbatim per-field message.
        """
        base = dict(alert_type="risk_cooling", severity="warning",
                    detail="d", evidence={})
        for bogus_symbol in ("", "   ", None, 600000):
            with self.assertRaisesRegex(
                    ValueError, "symbol must be a nonempty string"):
                xq_history_priority.GapAlert(
                    symbol=bogus_symbol, lane="history", **base)
        for bogus_lane in ("", "  \t ", None, ["history"]):
            with self.assertRaisesRegex(
                    ValueError, "lane must be a nonempty string"):
                xq_history_priority.GapAlert(
                    symbol="SH600000", lane=bogus_lane, **base)

    def test_rank_entry_rejects_malformed_pending_and_watermark(self) -> None:
        """The rank entry also rejects non-dict pending/watermark bundles.

        The batch entry point validates every bundle through
        ``_required_bundle``, so one malformed optional section rejects the
        whole ranking (closed on failure, no partial result) with the same
        verbatim messages as the single-bundle entries.
        """
        for pending in ("paused", ["state"], 3):
            malformed = self.bundle(pending=pending)
            with self.assertRaisesRegex(
                    ValueError, "pending must be a dict when present"):
                xq_history_priority.rank_history_targets([malformed])
        for watermark in ("w", [1], 5):
            malformed = self.bundle(watermark=watermark)
            with self.assertRaisesRegex(
                    ValueError, "watermark must be a dict when present"):
                xq_history_priority.rank_history_targets([malformed])
        # One bad bundle among good ones rejects the whole call.
        good = self.bundle(symbol="SH600001")
        bad = self.bundle(symbol="SH600002", watermark="w")
        with self.assertRaises(ValueError):
            xq_history_priority.rank_history_targets([good, bad])

    def test_positive_int_hint_scores_positive_marginal_factor(self) -> None:
        """A positive int hint contributes its weighted value, not neutral.

        ``marginal_output_hint=2`` is a legal external observation: the
        marginal factor is ``MARGINAL_OUTPUT_WEIGHT * 2``, echoed into
        ``reasons``, while ``None`` stays exactly neutral and marked
        missing. The gap factor is identical for both calls, so the score
        difference isolates the hint contribution.
        """
        bundle = self.bundle(
            gaps={"type": "history_unfinished", "next_cursor": "c9"})
        with_hint = xq_history_priority.score_history_backlog(
            bundle, marginal_output_hint=2, budget_units=1)
        without_hint = xq_history_priority.score_history_backlog(
            bundle, budget_units=1)
        # The int hint raises the explainable sum by exactly weight * 2.
        self.assertAlmostEqual(without_hint.priority_score,
                               with_hint.priority_score
                               - xq_history_priority.MARGINAL_OUTPUT_WEIGHT * 2)
        joined = " ".join(with_hint.reasons)
        self.assertIn("marginal_factor=+2.0", joined)
        self.assertIn("marginal_output_hint=2.0 (external observation)", joined)
        # The None contrast keeps the neutral missing marker, never a number.
        joined_missing = " ".join(without_hint.reasons)
        self.assertIn(
            "marginal_factor=+0.0: marginal_output_hint missing "
            "(neutral, not fabricated)", joined_missing)

    def test_last_fetched_at_tolerates_non_dict_watermark(self) -> None:
        """Low-value fallback: a non-dict watermark slot reports None.

        Inside ``_last_fetched_at`` the watermark is only referenced, never
        trusted: any non-dict value (or absent key) falls back to ``None``
        instead of raising. Entry points still close on failure for such
        bundles (pinned elsewhere); this pins the tolerant branch itself so
        ``trend_inputs`` can never fabricate a fetch time.
        """
        self.assertIsNone(
            xq_history_priority._last_fetched_at({"watermark": "w"}))
        self.assertIsNone(
            xq_history_priority._last_fetched_at({"watermark": ["x"]}))
        self.assertIsNone(xq_history_priority._last_fetched_at({}))
        # The dict branch echoes the recorded fetch time verbatim.
        self.assertEqual(
            xq_history_priority._last_fetched_at(
                {"watermark": {"fetched_at": 20000}}),
            20000)


if __name__ == "__main__":
    unittest.main()
