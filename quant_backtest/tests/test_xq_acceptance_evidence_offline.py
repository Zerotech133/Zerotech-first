"""Synthetic offline contracts for the acceptance-evidence export.

These tests are intentionally not executed while the repository's dynamic-test
safety approval is outstanding.
"""

from __future__ import annotations

import sqlite3
import unittest

from quant_backtest import (
    xq_acceptance_evidence,
    xq_offline_queue,
    xq_reverse_ledger,
    xq_reverse_store,
)


class AcceptanceEvidenceOfflineTests(unittest.TestCase):
    """Export watermark, coverage, samples, chain, failures, queue and gaps."""

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

    def test_single_post_page_exports_watermark_coverage_samples_and_chain(self) -> None:
        """A confirmed page yields every evidence column without mixing times."""
        self.commit("SH600000", "new", "b1", "n1",
                    [self.post("only", 100, "SH600000 原帖内容")],
                    [self.relation("only", "SH600000")], 10000,
                    next_cursor="n2")
        # The same original re-observed in a later batch lists every batch.
        self.commit("SH600000", "history", "b2", "h1",
                    [self.post("only", 100, "SH600000 原帖内容")],
                    [self.relation("only", "SH600000")], 20000)
        result = xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new")
        self.assertEqual(result["symbol"], "SH600000")
        self.assertEqual(result["lane"], "new")
        watermark = result["watermark"]
        self.assertEqual(watermark["version"], 1)
        self.assertEqual(watermark["confirmed_page_key"], "n1")
        self.assertEqual(watermark["next_cursor"], "n2")
        self.assertEqual(watermark["first_status_id"], "only")
        self.assertEqual(watermark["last_status_id"], "only")
        self.assertEqual(watermark["state"], "confirmed")
        self.assertEqual(watermark["batch_id"], "b1")
        self.assertEqual(watermark["fetched_at"], 10000)
        coverage = result["coverage"]
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["earliest_created_at_ms"], 100)
        self.assertEqual(coverage["earliest_post_id"], "only")
        self.assertEqual(coverage["latest_created_at_ms"], 100)
        self.assertEqual(coverage["latest_post_id"], "only")
        self.assertEqual(coverage["evidenced_post_count"], 1)
        self.assertEqual(coverage["coverage_started_at"], 10000)
        self.assertEqual(coverage["coverage_updated_at"], 10000)
        # Content time is kept apart from fetch-confirmation time.
        self.assertNotEqual(coverage["earliest_created_at_ms"],
                            coverage["coverage_started_at"])
        samples = result["post_samples"]
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample["post_id"], "only")
        self.assertEqual(sample["created_at_ms"], 100)
        self.assertEqual(sample["first_fetched_at"], 10000)
        self.assertEqual(sample["last_fetched_at"], 20000)
        self.assertEqual(sample["original_url"], "https://xueqiu.com/test/only")
        self.assertEqual(sample["batch_id"], "b1")
        self.assertEqual(sample["batch_ids"], ["b1", "b2"])
        chain = result["source_chain"]
        self.assertEqual(chain["downstream_units"], "not_linked")
        self.assertEqual(chain["post_links"], {
            "only": [
                {"symbol": "SH600000", "evidence_type": "post_content",
                 "batch_id": "b1"},
                {"symbol": "SH600000", "evidence_type": "post_content",
                 "batch_id": "b2"},
            ],
        })
        bundle = xq_acceptance_evidence.export_all(self.conn, "new")
        self.assertEqual([item["symbol"] for item in bundle], ["SH600000"])

    def test_transit_page_keeps_empty_coverage_and_honest_pending_and_gaps(self) -> None:
        """A scanned page without the target stock never fabricates coverage."""
        xq_offline_queue.init_schema(self.conn)
        xq_offline_queue.enqueue(self.conn, "SH600000", "history", "b1", "p1")
        self.commit("SH600000", "history", "b1", "p1",
                    [self.post("passing", 100, "SH600001 unrelated")],
                    [self.relation("passing", "SH600001")], 5000,
                    next_cursor="p2")
        result = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "history")
        self.assertEqual(result["coverage"], {"status": "empty"})
        self.assertEqual(result["watermark"]["version"], 1)
        self.assertEqual(result["watermark"]["confirmed_page_key"], "p1")
        self.assertEqual(result["watermark"]["next_cursor"], "p2")
        self.assertEqual(result["post_samples"], [])
        self.assertEqual(result["source_chain"]["post_links"], {})
        self.assertEqual(result["pending"], {
            "queue": "initialized", "state": "pending",
            "page_key": "p1", "claim_id": None})
        self.assertEqual(result["gaps"],
                         {"type": "history_unfinished", "next_cursor": "p2"})

    def test_multi_post_membership_then_blocked_after_member_removal(self) -> None:
        """Members attribute coverage; a lost roster blocks without numbers."""
        self.commit("SH600000", "history", "batch", "p1",
                    [self.post("first", 100, "SH600001 a"),
                     self.post("middle", 200, "SH600000 b"),
                     self.post("last", 300, "SH600001 c")],
                    [self.relation("middle", "SH600000")], 4000)
        result = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "history")
        coverage = result["coverage"]
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["evidenced_post_count"], 1)
        self.assertEqual(coverage["latest_post_id"], "middle")
        self.assertEqual(coverage["latest_created_at_ms"], 200)
        self.assertEqual(len(result["post_samples"]), 1)
        # Hand-deleting the roster models a receipt the reader cannot prove.
        self.conn.execute(
            """DELETE FROM xq_reverse_page_member
                WHERE symbol = 'SH600000' AND lane = 'history'
                  AND batch_id = 'batch' AND page_key = 'p1'""")
        blocked = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "history")
        section = blocked["coverage"]
        self.assertEqual(section["status"], "blocked")
        self.assertTrue(section["reason"])
        self.assertEqual(set(section), {"status", "reason"})

    def test_corrupt_stored_batch_ids_json_is_closed_on_failure(self) -> None:
        """Unparseable or non-list batch ids make export fail, never guess."""
        self.commit("SH600000", "new", "b1", "n1",
                    [self.post("only", 100, "SH600000 原帖内容")],
                    [self.relation("only", "SH600000")], 10000)
        # Hand-corrupting the stored batch list models a damaged receipt.
        self.conn.execute(
            """UPDATE xq_reverse_raw_post
                  SET batch_ids_json = 'not-json'
                WHERE post_id = 'only'""")
        with self.assertRaises(ValueError):
            xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new")
        # Valid JSON that is not a list is closed on failure as well.
        self.conn.execute(
            """UPDATE xq_reverse_raw_post
                  SET batch_ids_json = '{"batch": "b1"}'
                WHERE post_id = 'only'""")
        with self.assertRaises(ValueError):
            xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new")

    def test_export_all_isolates_blocked_stock_from_healthy_stock(self) -> None:
        """One blocked bundle never stops the other stock's honest export."""
        self.commit("SH600000", "history", "batch-a", "p1",
                    [self.post("first", 100, "SH600001 a"),
                     self.post("middle", 200, "SH600000 b"),
                     self.post("last", 300, "SH600001 c")],
                    [self.relation("middle", "SH600000")], 4000)
        self.commit("SZ000001", "history", "batch-b", "p1",
                    [self.post("solo", 150, "SZ000001 body")],
                    [self.relation("solo", "SZ000001")], 5000)
        # Hand-deleting SH600000's roster blocks only that stock's coverage.
        self.conn.execute(
            """DELETE FROM xq_reverse_page_member
                WHERE symbol = 'SH600000' AND lane = 'history'
                  AND batch_id = 'batch-a' AND page_key = 'p1'""")
        bundle = xq_acceptance_evidence.export_all(self.conn, "history")
        self.assertEqual([item["symbol"] for item in bundle],
                         ["SH600000", "SZ000001"])
        by_symbol = {item["symbol"]: item for item in bundle}
        blocked = by_symbol["SH600000"]["coverage"]
        self.assertEqual(blocked["status"], "blocked")
        self.assertTrue(blocked["reason"])
        healthy = by_symbol["SZ000001"]["coverage"]
        self.assertEqual(healthy["status"], "ok")
        self.assertEqual(healthy["evidenced_post_count"], 1)
        self.assertEqual(healthy["latest_post_id"], "solo")

    def test_export_all_propagates_corrupt_batch_ids_fail_closed(self) -> None:
        """One corrupt stored batch list aborts the whole export, unlike blocked coverage which stays isolated per stock."""
        self.commit("SH600000", "new", "b1", "n1",
                    [self.post("healthy", 100, "SH600000 body")],
                    [self.relation("healthy", "SH600000")], 10000)
        self.commit("SZ000001", "new", "b2", "n2",
                    [self.post("solo", 150, "SZ000001 body")],
                    [self.relation("solo", "SZ000001")], 11000)
        # Hand-corrupting one stored batch list models a damaged legacy row.
        self.conn.execute(
            """UPDATE xq_reverse_raw_post
                  SET batch_ids_json = 'not-json'
                WHERE post_id = 'solo'""")
        # export_all has no per-stock isolation for this damage: the list
        # comprehension propagates the ValueError, so the caller receives no
        # partial bundle at all (fail-closed), even though SH600000's own
        # bundle would export cleanly on its own.
        with self.assertRaises(ValueError) as error:
            xq_acceptance_evidence.export_all(self.conn, "new")
        self.assertEqual(
            str(error.exception),
            "stored batch_ids_json must be a JSON list")

    def test_recent_page_failures_are_listed_with_reasons(self) -> None:
        """Unconfirmed attempts surface their status, detail and fetch time."""
        xq_reverse_ledger.record_page_attempt(
            self.conn, "SH600000", "new", "b1", "n1", "403", 10000, "forbidden")
        xq_reverse_ledger.record_page_attempt(
            self.conn, "SH600000", "new", "b1", "n1", "http_404", 11000, "missing")
        xq_reverse_ledger.record_page_attempt(
            self.conn, "SZ000001", "new", "b1", "n1", "timeout", 12000, "other stock")
        result = xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new")
        failures = result["failures"]
        self.assertEqual([item["status"] for item in failures], ["http_404", "403"])
        self.assertEqual([item["detail"] for item in failures], ["missing", "forbidden"])
        self.assertEqual([int(item["fetched_at"]) for item in failures], [11000, 10000])
        self.assertTrue(all(item["batch_id"] == "b1" and item["page_key"] == "n1"
                            for item in failures))

    def test_failure_samples_are_capped_at_ten_newest_attempts(self) -> None:
        """Only the most recent attempts survive the failure sample cap."""
        recorded = []
        for index in range(12):
            recorded.append(xq_reverse_ledger.record_page_attempt(
                self.conn, "SH600000", "new", "b1", "n1",
                "timeout", 10000 + index, f"attempt-{index}"))
        result = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "new")
        failures = result["failures"]
        # FAILURE_SAMPLE_LIMIT keeps exactly the ten newest attempts.
        self.assertEqual(len(failures), 10)
        self.assertEqual([item["attempt_id"] for item in failures],
                         list(reversed(recorded[-10:])))
        self.assertEqual([item["detail"] for item in failures],
                         [f"attempt-{index}" for index in range(11, 1, -1)])

    def test_pending_reflects_queue_presence(self) -> None:
        """An absent queue table is reported as such, never invented around."""
        bare = xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new")
        self.assertEqual(bare["pending"], {"queue": "not_initialized"})
        xq_offline_queue.init_schema(self.conn)
        xq_offline_queue.enqueue(self.conn, "SH600000", "new", "b1", "n1")
        ready = xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new")
        self.assertEqual(ready["pending"], {
            "queue": "initialized", "state": "pending",
            "page_key": "n1", "claim_id": None})
        other = xq_acceptance_evidence.export_evidence(self.conn, "SZ000001", "new")
        self.assertEqual(other["pending"], {
            "queue": "initialized", "state": None,
            "page_key": None, "claim_id": None})

    def test_gaps_only_infer_watermark_provable_boundaries(self) -> None:
        """History gaps follow the cursor; missing cursors prove no ending."""
        self.commit("SH600000", "history", "b1", "p1",
                    [self.post("one", 100, "SH600000")],
                    [self.relation("one", "SH600000")], 1000,
                    next_cursor="p2")
        unfinished = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "history")
        self.assertEqual(unfinished["gaps"],
                         {"type": "history_unfinished", "next_cursor": "p2"})
        self.commit("SH600000", "history", "b1", "p2",
                    [self.post("two", 50, "SH600000")],
                    [self.relation("two", "SH600000")], 2000,
                    expected_version=1)
        # A cursorless confirmed page never proves that history has ended.
        ended = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "history")
        self.assertEqual(ended["gaps"], {"type": "unknown"})
        # A waiting cursor request keeps the history gap explicitly open.
        xq_offline_queue.init_schema(self.conn)
        self.conn.execute(
            """INSERT INTO xq_offline_queue
                 (symbol, lane, batch_id, page_key, state, sequence, claim_id)
               VALUES ('SH600000', 'history', 'b1', 'p2',
                       'awaiting_cursor', 1, NULL)""")
        waiting = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "history")
        self.assertEqual(waiting["gaps"],
                         {"type": "history_unfinished", "next_cursor": None})
        self.assertEqual(waiting["pending"]["state"], "awaiting_cursor")
        new_lane = xq_acceptance_evidence.export_evidence(
            self.conn, "SH600000", "new")
        self.assertEqual(new_lane["gaps"], {"type": "unknown"})

    def test_request_validation_and_missing_tables(self) -> None:
        """Blank stocks, unknown lanes and bad limits are rejected early."""
        with self.assertRaises(ValueError):
            xq_acceptance_evidence.export_evidence(self.conn, "", "new")
        with self.assertRaises(ValueError):
            xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "unknown")
        with self.assertRaises(ValueError):
            xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new", 0)
        with self.assertRaises(ValueError):
            xq_acceptance_evidence.export_evidence(self.conn, "SH600000", "new", True)
        empty = sqlite3.connect(":memory:")
        try:
            with self.assertRaises(sqlite3.Error):
                xq_acceptance_evidence.export_evidence(empty, "SH600000", "new")
        finally:
            empty.close()

    @staticmethod
    def stored_post_conn(batch_ids_json: str) -> sqlite3.Connection:
        """Build an in-memory store holding one post with a crafted batch id list."""
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN")
        xq_reverse_store.init_schema(conn)
        raw_text = '{"id": "p1", "text": "body $SH600000"}'
        evidence_text = '{"excerpt": "$SH600000", "source_field": "text"}'
        conn.execute(
            "INSERT INTO xq_reverse_raw_post (source, post_id, original_url,"
            " created_at_ms, first_fetched_at, last_fetched_at, raw_json,"
            " first_raw_json, batch_ids_json)"
            " VALUES ('xueqiu', 'p1', 'https://xueqiu.com/test/p1',"
            " 1600000000000, 1600000000000, 1600000000000, ?, ?, ?)",
            (raw_text, raw_text, batch_ids_json))
        conn.execute(
            "INSERT INTO xq_reverse_raw_post_version (post_id, batch_id,"
            " original_url, created_at_ms, fetched_at, raw_json)"
            " VALUES ('p1', 'b1', 'https://xueqiu.com/test/p1',"
            " 1600000000000, 1600000000000, ?)",
            (raw_text,))
        conn.execute(
            "INSERT INTO xq_reverse_post_symbol (post_id, symbol,"
            " evidence_type, evidence_json, batch_id)"
            " VALUES ('p1', 'SH600000', 'post_content', ?, 'b1')",
            (evidence_text,))
        return conn

    def test_stored_batch_ids_json_rejects_blank_or_nonstring_elements(self) -> None:
        """A stored batch list with blank or non-string elements is rejected without fabrication."""
        for corrupt in ('[""]', '["   "]', "[1]", "[null]", "[{}]",
                        '["b1", ""]', '["b1", 2]'):
            conn = self.stored_post_conn(corrupt)
            with self.assertRaises(ValueError) as error:
                xq_acceptance_evidence._post_samples(conn, "SH600000", 10)
            self.assertEqual(
                str(error.exception),
                "stored batch_ids_json must list nonempty batch strings")
            conn.close()

    def test_stored_empty_batch_ids_list_is_rejected_as_corrupt(self) -> None:
        """An empty stored batch list is corrupt (the writer always stores at least one batch), so it is rejected closed on failure and never inferred or fabricated."""
        conn = self.stored_post_conn("[]")
        with self.assertRaises(ValueError) as error:
            xq_acceptance_evidence._post_samples(conn, "SH600000", 10)
        self.assertEqual(
            str(error.exception),
            "stored batch_ids_json must list nonempty batch strings")
        conn.close()


if __name__ == "__main__":
    unittest.main()
