"""Synthetic offline contracts for confirmed original-post content-time boundaries.

These tests are intentionally not executed while the repository's dynamic-test
safety approval is outstanding.
"""

from __future__ import annotations

import sqlite3
import unittest

from quant_backtest import xq_content_coverage, xq_reverse_ledger, xq_reverse_store


class ContentCoverageOfflineTests(unittest.TestCase):
    """Reject ambiguous page membership and isolate content from fetch time."""

    def setUp(self) -> None:
        """Create isolated, in-memory original and ledger tables."""
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        xq_reverse_ledger.init_schema(self.conn)
        self.conn.execute("BEGIN")
        xq_reverse_store.init_schema(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        """Dispose of the private fixture connection."""
        self.conn.close()

    @staticmethod
    def post(post_id: str, created_at_ms: int, text: str) -> xq_reverse_store.RawPostInput:
        """Build a complete synthetic original with a trusted top-level ID."""
        return xq_reverse_store.RawPostInput(
            post_id=post_id,
            original_url=f"https://xueqiu.com/test/{post_id}",
            created_at_ms=created_at_ms,
            raw_json={"id": post_id, "text": text},
        )

    @staticmethod
    def relation(post_id: str, symbol: str) -> xq_reverse_store.PostSymbolInput:
        """Build exact original-text evidence for a target mention."""
        return xq_reverse_store.PostSymbolInput(
            post_id=post_id,
            symbol=symbol,
            evidence_type="post_content",
            evidence_json={"source_field": "text", "excerpt": symbol},
        )

    def commit(
        self, symbol: str, lane: str, batch_id: str, page_key: str,
        posts: list[xq_reverse_store.RawPostInput],
        relations: list[xq_reverse_store.PostSymbolInput],
        fetched_at: int, expected_version: int = 0,
        next_cursor: str | None = None,
    ) -> None:
        """Confirm a synthetic page through the production ledger interface."""
        xq_reverse_ledger.commit_page(
            self.conn, symbol, lane, batch_id, page_key,
            posts, relations, next_cursor, expected_version, fetched_at,
        )

    def test_empty_and_attempt_do_not_create_content_boundary(self) -> None:
        """Unstarted/failed/transit pages cannot fabricate post creation time."""
        initial = xq_content_coverage.read_content_coverage(self.conn, "SH600000", "new")
        self.assertIsNone(initial.earliest_created_at_ms)
        self.assertIsNone(initial.coverage_started_at)
        xq_reverse_ledger.record_page_attempt(
            self.conn, "SH600000", "new", "b1", "failed", "403", 10000,
        )
        self.commit("SH600000", "new", "b2", "p1",
                    [self.post("passing", 100, "SH600001")],
                    [self.relation("passing", "SH600001")], 20000)
        result = xq_content_coverage.read_content_coverage(self.conn, "SH600000", "new")
        self.assertEqual(result.evidenced_post_count, 0)
        self.assertIsNone(result.earliest_created_at_ms)
        self.assertIsNone(result.latest_post_id)
        self.assertIsNone(result.coverage_started_at)

    def test_single_post_receipts_provide_real_content_times_per_lane(self) -> None:
        """Content boundaries and evidence IDs stay separate from fetch times."""
        self.commit("SH600000", "history", "b1", "p1",
                    [self.post("older", 120, "SH600000")],
                    [self.relation("older", "SH600000")], 10000,
                    next_cursor="p2")
        self.commit("SH600000", "history", "b1", "p2",
                    [self.post("newer", 800, "SH600000")],
                    [self.relation("newer", "SH600000")], 12000,
                    expected_version=1)
        self.commit("SH600000", "new", "b2", "n1",
                    [self.post("fresh", 900, "SH600000")],
                    [self.relation("fresh", "SH600000")], 14000)
        historical = xq_content_coverage.read_content_coverage(
            self.conn, "SH600000", "history")
        recent = xq_content_coverage.read_content_coverage(self.conn, "SH600000", "new")
        self.assertEqual((historical.earliest_created_at_ms, historical.earliest_post_id),
                         (120, "older"))
        self.assertEqual((historical.latest_created_at_ms, historical.latest_post_id),
                         (800, "newer"))
        self.assertEqual((historical.coverage_started_at, historical.coverage_updated_at),
                         (10000, 12000))
        self.assertEqual((recent.earliest_created_at_ms, recent.latest_post_id),
                         (900, "fresh"))
        self.assertEqual(recent.coverage_started_at, 14000)

    def test_cross_batch_post_version_never_overwrites_previous_original(self) -> None:
        """A later global latest raw post may differ from the older lane version."""
        self.commit("SH600000", "history", "old", "p1",
                    [self.post("same", 100, "SH600000")],
                    [self.relation("same", "SH600000")], 1000)
        self.commit("SH600000", "new", "new", "p1",
                    [self.post("same", 500, "SH600000")],
                    [self.relation("same", "SH600000")], 2000)
        old = xq_content_coverage.read_content_coverage(self.conn, "SH600000", "history")
        new = xq_content_coverage.read_content_coverage(self.conn, "SH600000", "new")
        self.assertEqual(old.earliest_created_at_ms, 100)
        self.assertEqual(new.earliest_created_at_ms, 500)

    def test_multi_post_receipt_attributes_coverage_through_page_members(self) -> None:
        """A confirmed multi-post page is attributed via its member roster."""
        self.commit("SH600000", "history", "batch", "p0",
                    [self.post("known", 50, "SH600000")],
                    [self.relation("known", "SH600000")], 3000,
                    next_cursor="p1")
        self.commit("SH600000", "history", "batch", "p1",
                    [self.post("first", 100, "SH600001"),
                     self.post("middle", 200, "SH600000"),
                     self.post("last", 300, "SH600001")],
                    [self.relation("middle", "SH600000")], 4000,
                    expected_version=1)
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, "SH600000", "history")
        self.assertEqual((coverage.earliest_created_at_ms, coverage.earliest_post_id),
                         (50, "known"))
        self.assertEqual((coverage.latest_created_at_ms, coverage.latest_post_id),
                         (200, "middle"))
        self.assertEqual(coverage.evidenced_post_count, 2)
        # The same batch may have another lane; lane-scoped member rows must
        # not leak another lane's posts into this lane's attribution.
        self.commit("SH600000", "new", "batch", "n1",
                    [self.post("other", 400, "SH600001")],
                    [self.relation("other", "SH600001")], 5000)
        self.assertIsNone(xq_content_coverage.read_content_coverage(
            self.conn, "SH600000", "new").earliest_created_at_ms)

    def test_multi_post_receipt_without_member_rows_blocks_entirely(self) -> None:
        """Pre-roster multi-post receipts are never backfilled with guesses."""
        self.commit("SH600000", "history", "batch", "p1",
                    [self.post("first", 100, "SH600001"),
                     self.post("middle", 200, "SH600000"),
                     self.post("last", 300, "SH600001")],
                    [self.relation("middle", "SH600000")], 4000)
        # Hand-deleting the roster models a receipt written before the
        # member table existed; the reader must stay closed on failure.
        self.conn.execute(
            """DELETE FROM xq_reverse_page_member
                WHERE symbol = 'SH600000' AND lane = 'history'
                  AND batch_id = 'batch' AND page_key = 'p1'""")
        with self.assertRaises(xq_content_coverage.CoverageAttributionBlockedError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "history")

    def test_member_count_disagreement_blocks_entirely(self) -> None:
        """A roster whose row count differs from the receipt is not trusted."""
        self.commit("SH600000", "history", "batch", "p1",
                    [self.post("first", 100, "SH600001"),
                     self.post("middle", 200, "SH600000"),
                     self.post("last", 300, "SH600001")],
                    [self.relation("middle", "SH600000")], 4000)
        self.conn.execute(
            """INSERT INTO xq_reverse_page_member
                 (symbol, lane, batch_id, page_key, ordinal, post_id)
               VALUES ('SH600000', 'history', 'batch', 'p1', 4, 'phantom')""")
        with self.assertRaises(xq_content_coverage.CoverageAttributionBlockedError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "history")

    def test_member_boundary_disagreement_blocks_entirely(self) -> None:
        """Member rows whose first/last IDs differ from the receipt block."""
        self.commit("SH600000", "history", "batch", "p1",
                    [self.post("first", 100, "SH600001"),
                     self.post("middle", 200, "SH600000"),
                     self.post("last", 300, "SH600001")],
                    [self.relation("middle", "SH600000")], 4000)
        self.conn.execute(
            """UPDATE xq_reverse_page_member SET post_id = 'swapped'
                WHERE symbol = 'SH600000' AND lane = 'history'
                  AND batch_id = 'batch' AND page_key = 'p1' AND ordinal = 1""")
        with self.assertRaises(xq_content_coverage.CoverageAttributionBlockedError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "history")

    def test_member_ordinal_gap_blocks_entirely(self) -> None:
        """A roster whose ordinals leave a 1..n gap is not trusted."""
        self.commit("SH600000", "history", "batch", "p1",
                    [self.post("first", 100, "SH600001"),
                     self.post("last", 300, "SH600000")],
                    [self.relation("last", "SH600000")], 4000)
        # Hand-editing an ordinal leaves a 1..n gap in the roster; the
        # reader must stay closed on failure.
        self.conn.execute(
            """UPDATE xq_reverse_page_member SET ordinal = 4
                WHERE symbol = 'SH600000' AND lane = 'history'
                  AND batch_id = 'batch' AND page_key = 'p1' AND ordinal = 2""")
        with self.assertRaises(xq_content_coverage.CoverageAttributionBlockedError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "history")

    def test_member_cross_batch_time_conflict_blocks_entirely(self) -> None:
        """One member post may not carry two confirmed content times."""
        self.commit("SH600000", "history", "batch-a", "p0",
                    [self.post("dup", 100, "SH600000")],
                    [self.relation("dup", "SH600000")], 1000,
                    next_cursor="p1")
        self.commit("SH600000", "history", "batch-b", "p1",
                    [self.post("first", 150, "SH600001"),
                     self.post("dup", 500, "SH600000"),
                     self.post("last", 300, "SH600001")],
                    [self.relation("dup", "SH600000")], 2000,
                    expected_version=1)
        with self.assertRaises(xq_content_coverage.CoverageAttributionBlockedError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "history")

    def test_malformed_evidence_fails_closed(self) -> None:
        """Corrupt same-batch evidence is not an eligible original relation."""
        self.commit("SH600000", "new", "b1", "n1",
                    [self.post("one", 100, "SH600000")],
                    [self.relation("one", "SH600000")], 1000)
        self.conn.execute(
            """UPDATE xq_reverse_post_symbol SET evidence_json = ?
               WHERE post_id = ? AND batch_id = ?""",
            ('{"source_field":"text","excerpt":"SH600001"}', "one", "b1"),
        )
        with self.assertRaises(xq_content_coverage.CoverageAttributionBlockedError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "new")

    def test_request_validation(self) -> None:
        """Unknown lanes and blank stock identifiers are rejected."""
        with self.assertRaises(ValueError):
            xq_content_coverage.read_content_coverage(self.conn, "", "new")
        with self.assertRaises(ValueError):
            xq_content_coverage.read_content_coverage(self.conn, "SH600000", "unknown")


class SymbolTokenBoundaryTests(unittest.TestCase):
    """WO-061 A2 口径：内容覆盖归属同样要求完整 token 证据。"""

    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        xq_reverse_ledger.init_schema(self.conn)
        self.conn.execute("BEGIN")
        xq_reverse_store.init_schema(self.conn)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    @staticmethod
    def post(post_id: str, text: str) -> xq_reverse_store.RawPostInput:
        return xq_reverse_store.RawPostInput(
            post_id=post_id,
            original_url=f"https://xueqiu.com/test/{post_id}",
            created_at_ms=1_600_000_000_000,
            raw_json={"id": post_id, "text": text},
        )

    @staticmethod
    def relation(post_id: str, symbol: str, excerpt: str
                 ) -> xq_reverse_store.PostSymbolInput:
        return xq_reverse_store.PostSymbolInput(
            post_id=post_id,
            symbol=symbol,
            evidence_type="post_content",
            evidence_json={"source_field": "text", "excerpt": excerpt},
        )

    def test_partial_token_occurrence_rejected(self) -> None:
        """SH600000 不得借 SH6000000 的子串位置取得内容归属。"""
        with self.assertRaises(ValueError):
            xq_reverse_ledger.commit_page(
                self.conn, "SH600000", "new", "bA2x", "pg1",
                [self.post("pA2x", "SH6000000 走势分析长文")],
                [self.relation("pA2x", "SH600000", "SH600000")],
                None, 0, 1000)

    def test_cashtag_occurrence_accepted(self) -> None:
        """标点邻接的完整 token 保持可归属。"""
        xq_reverse_ledger.commit_page(
            self.conn, "SH600000", "new", "bA2y", "pg1",
            [self.post("pA2y", "buy $SH600000$ today")],
            [self.relation("pA2y", "SH600000", "$SH600000$")],
            None, 0, 1000)
        view = xq_content_coverage.read_content_coverage(
            self.conn, "SH600000", "new")
        self.assertIsNotNone(view.earliest_created_at_ms)
