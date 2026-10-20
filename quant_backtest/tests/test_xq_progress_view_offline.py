"""Offline tests for the read-only Xueqiu progress view data model.

Every fixture is synthetic and built in a temporary SQLite database
through the real ledger/store/queue interfaces; no network and no
dynamic execution beyond these unittest cases are required. The tests
pin the exact key contract, freshness arithmetic, closed on failure
input rejection, blocked-attribution transparency, gap/alert passthrough
and the zero-write guarantee of ``xq_progress_view``.

These tests are intentionally not executed while the repository's
dynamic-test safety approval is outstanding; only ast.parse self-checks
run.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xq_acceptance_evidence  # noqa: E402
import xq_offline_queue as queue  # noqa: E402
import xq_progress_view as progress_view  # noqa: E402
import xq_reverse_ledger as ledger  # noqa: E402
import xq_reverse_store as store  # noqa: E402


#: The scanned stock that owns verified target-symbol content evidence.
SYMBOL: str = "SH600000"

#: A second stock used only to prove board ordering determinism.
OTHER_SYMBOL: str = "SZ000001"

#: A stock that never receives any fixture data.
EMPTY_SYMBOL: str = "SH000000"

#: Fetch-confirmation time (Unix ms) of the committed fixture page.
FETCH_MS: int = 1_700_000_100_000

#: Content times (Unix ms) of the two fixture originals, descending.
POST1_TIME_MS: int = 1_700_000_003_000
POST2_TIME_MS: int = 1_700_000_002_000

#: Default evaluation instant for views that do not assert freshness.
AS_OF_MS: int = 1_700_000_500_000

#: Confirmed ``next_cursor`` that makes the history lane demonstrably
#: unfinished in the gap fixture.
HISTORY_CURSOR: str = "hist-next-2"

#: Exact top-level keys of one progress view.
VIEW_KEYS: frozenset[str] = frozenset(
    {"symbol", "lanes", "generated_as_of_ms", "notes"})

#: Exact keys of one lane's progress row.
LANE_KEYS: frozenset[str] = frozenset({
    "queue_state", "watermark", "freshness", "coverage",
    "samples", "gaps", "failures", "alerts",
})

#: The ten ledger checkpoint keys the watermark must carry verbatim.
WATERMARK_KEYS: frozenset[str] = frozenset({
    "version", "confirmed_page_key", "next_cursor", "first_status_id",
    "last_status_id", "coverage_started_at", "coverage_updated_at",
    "state", "batch_id", "fetched_at",
})


class ProgressViewOfflineTests(unittest.TestCase):
    """Key contract, freshness, closed on failure and zero-write checks."""

    def setUp(self) -> None:
        """Create an isolated offline database with the real schema."""
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.conn = sqlite3.connect(
            root / "progress.sqlite3", isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        queue.init_schema(self.conn)

    def tearDown(self) -> None:
        """Dispose of the private fixture connection and temporary files."""
        self.conn.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Synthetic fixture builders (mirrors test_xq_offline_chain_e2e.py)
    # ------------------------------------------------------------------

    @staticmethod
    def post(post_id: str, created_at_ms: int, text: str,
             author: str | None = "trader_seven") -> store.RawPostInput:
        """Build one complete synthetic original with a trusted top-level ID."""
        raw: dict = {"id": post_id, "text": text}
        if author is not None:
            raw["user"] = {"screen_name": author}
        return store.RawPostInput(
            post_id=post_id,
            original_url=f"https://xueqiu.com/test/{post_id}",
            created_at_ms=created_at_ms,
            raw_json=raw,
        )

    @staticmethod
    def relation(post_id: str, symbol: str) -> store.PostSymbolInput:
        """Build exact original-text evidence for one stock mention."""
        return store.PostSymbolInput(
            post_id=post_id,
            symbol=symbol,
            evidence_type="post_content",
            evidence_json={"source_field": "text", "excerpt": symbol},
        )

    def _two_posts(self, symbol: str) -> tuple[list[store.RawPostInput],
                                               list[store.PostSymbolInput]]:
        """Two evidenced originals whose text carries the exact symbol."""
        posts = [
            self.post("sh-p1", POST1_TIME_MS, f"放量上攻，继续看好 {symbol} 基本面"),
            self.post("sh-p2", POST2_TIME_MS, f"长期跟踪 {symbol}，估值处于低位",
                      author=None),
        ]
        relations = [
            self.relation("sh-p1", symbol),
            self.relation("sh-p2", symbol),
        ]
        return posts, relations

    def _commit_page(self, symbol: str, lane: str, batch_id: str,
                     page_key: str, posts: list[store.RawPostInput],
                     relations: list[store.PostSymbolInput],
                     next_cursor: str | None,
                     fetched_at: int = FETCH_MS) -> dict:
        """Confirm one page through the real ledger CAS entry point."""
        return ledger.commit_page(
            self.conn, symbol, lane, batch_id, page_key,
            posts, relations, next_cursor, 0, fetched_at)

    def _snapshot_counts(self) -> dict[str, int]:
        """COUNT(*) of every table sqlite_master lists, name-sorted."""
        names = [row[0] for row in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "ORDER BY name").fetchall()]
        return {
            name: self.conn.execute(
                f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in names
        }

    # ------------------------------------------------------------------
    # a. Dual-lane structure with exact keys
    # ------------------------------------------------------------------

    def test_a_view_structure_keys_are_exact(self) -> None:
        """Top-level, lane and queue_state keys match the contract exactly."""
        queue.enqueue(self.conn, SYMBOL, "new", "b-new", "n1")
        view = progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=AS_OF_MS)
        self.assertEqual(set(view), set(VIEW_KEYS))
        self.assertEqual(view["symbol"], SYMBOL)
        self.assertEqual(view["generated_as_of_ms"], AS_OF_MS)
        self.assertEqual(set(view["lanes"]), {"new", "history"})
        for lane in ("new", "history"):
            self.assertEqual(set(view["lanes"][lane]), set(LANE_KEYS))
        self.assertIn(
            "read-only progress view; does not advance watermarks",
            view["notes"])
        self.assertIn("downstream units not linked", view["notes"])
        state = view["lanes"]["new"]["queue_state"]
        self.assertEqual(set(state),
                         {"state", "page_key", "claim_id", "batch_id"})
        self.assertEqual(state["state"], "pending")
        self.assertEqual(state["page_key"], "n1")
        self.assertEqual(state["batch_id"], "b-new")
        self.assertIsNone(state["claim_id"])
        self.assertIsNone(view["lanes"]["history"]["queue_state"])
        watermark = view["lanes"]["new"]["watermark"]
        self.assertTrue(WATERMARK_KEYS.issubset(set(watermark)))
        self.assertEqual(watermark, ledger.get_checkpoint(
            self.conn, SYMBOL, "new"))

    # ------------------------------------------------------------------
    # b. Freshness arithmetic, negatives preserved
    # ------------------------------------------------------------------

    def test_b_freshness_age_and_negative_preserved(self) -> None:
        """age_since_fetch_ms is exact and never clamps below zero."""
        posts, relations = self._two_posts(SYMBOL)
        self._commit_page(SYMBOL, "new", "b-new", "n1", posts, relations, None)
        later = progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=FETCH_MS + 2500)
        fresh = later["lanes"]["new"]["freshness"]
        self.assertEqual(fresh["latest_fetched_ms"], FETCH_MS)
        self.assertEqual(fresh["age_since_fetch_ms"], 2500)
        self.assertEqual(fresh["latest_content_ms"], POST1_TIME_MS)
        past = progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=FETCH_MS - 1)
        negative = past["lanes"]["new"]["freshness"]["age_since_fetch_ms"]
        self.assertEqual(negative, -1)
        self.assertLess(negative, 0)

    # ------------------------------------------------------------------
    # c. No-task lane: explicit None, nothing fabricated
    # ------------------------------------------------------------------

    def test_c_no_task_lane_reports_none_without_fabrication(self) -> None:
        """An untouched stock-lane shows None watermarks and empty markers."""
        view = progress_view.build_progress_view(
            self.conn, EMPTY_SYMBOL, as_of_ms=AS_OF_MS)
        for lane in ("new", "history"):
            row = view["lanes"][lane]
            self.assertIsNone(row["queue_state"])
            watermark = row["watermark"]
            self.assertEqual(watermark, ledger.get_checkpoint(
                self.conn, EMPTY_SYMBOL, lane))
            self.assertEqual(watermark["version"], 0)
            self.assertEqual(watermark["state"], "pending")
            for key in ("confirmed_page_key", "next_cursor", "first_status_id",
                        "last_status_id", "coverage_started_at",
                        "coverage_updated_at", "batch_id", "fetched_at"):
                self.assertIsNone(watermark[key])
            self.assertEqual(row["freshness"], {
                "latest_content_ms": None,
                "latest_fetched_ms": None,
                "age_since_fetch_ms": None,
            })
            self.assertEqual(row["samples"], [])
            self.assertEqual(row["failures"], [])
            self.assertEqual(row["coverage"], {"status": "empty"})

    # ------------------------------------------------------------------
    # d. Blocked attribution: exact {"status", "reason"} shape
    # ------------------------------------------------------------------

    def test_d_blocked_coverage_carries_no_numeric_keys(self) -> None:
        """Deleting page members blocks attribution without invented numbers."""
        posts, relations = self._two_posts(SYMBOL)
        self._commit_page(SYMBOL, "new", "b-new", "n1", posts, relations, None)
        self.conn.execute(
            "DELETE FROM xq_reverse_page_member WHERE symbol = ? AND lane = ?",
            (SYMBOL, "new"))
        view = progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=AS_OF_MS)
        coverage = view["lanes"]["new"]["coverage"]
        self.assertEqual(set(coverage), {"status", "reason"})
        self.assertEqual(coverage["status"], "blocked")
        self.assertIsInstance(coverage["reason"], str)
        self.assertTrue(coverage["reason"])
        self.assertFalse(any(
            isinstance(value, (int, float)) for value in coverage.values()))

    # ------------------------------------------------------------------
    # e. history_unfinished gap passthrough plus non-empty alerts
    # ------------------------------------------------------------------

    def test_e_history_gap_passthrough_and_alerts(self) -> None:
        """An unfinished history lane forwards its gap and raises an alert."""
        posts, relations = self._two_posts(SYMBOL)
        self._commit_page(SYMBOL, "history", "b-hist", "h1", posts, relations,
                          HISTORY_CURSOR)
        view = progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=AS_OF_MS)
        lane = view["lanes"]["history"]
        self.assertEqual(lane["gaps"], {
            "type": "history_unfinished", "next_cursor": HISTORY_CURSOR})
        bundle = xq_acceptance_evidence.export_evidence(
            self.conn, SYMBOL, "history")
        self.assertEqual(lane["gaps"], bundle["gaps"])
        self.assertTrue(lane["alerts"])
        self.assertIn("history_unfinished",
                      [alert["alert_type"] for alert in lane["alerts"]])
        self.assertIsInstance(lane["alerts"][0], dict)

    # ------------------------------------------------------------------
    # f. Board order determinism and whole-board rejection
    # ------------------------------------------------------------------

    def test_f_board_order_and_invalid_symbols_rejected(self) -> None:
        """Input order is preserved; any bad symbol rejects the whole board."""
        posts, relations = self._two_posts(SYMBOL)
        self._commit_page(SYMBOL, "new", "b-new", "n1", posts, relations, None)
        board = progress_view.build_board(
            self.conn, [SYMBOL, OTHER_SYMBOL], as_of_ms=AS_OF_MS)
        self.assertEqual([row["symbol"] for row in board],
                         [SYMBOL, OTHER_SYMBOL])
        reversed_board = progress_view.build_board(
            self.conn, [OTHER_SYMBOL, SYMBOL], as_of_ms=AS_OF_MS)
        self.assertEqual([row["symbol"] for row in reversed_board],
                         [OTHER_SYMBOL, SYMBOL])
        for bad in ("", "   ", True, 7, None):
            with self.assertRaises(ValueError):
                progress_view.build_board(
                    self.conn, [SYMBOL, bad], as_of_ms=AS_OF_MS)
        with self.assertRaises(ValueError):
            progress_view.build_board(
                self.conn, SYMBOL, as_of_ms=AS_OF_MS)

    # ------------------------------------------------------------------
    # g. Invalid as_of_ms rejected (negative / bool / string)
    # ------------------------------------------------------------------

    def test_g_invalid_as_of_ms_rejected(self) -> None:
        """-1, True and "1" are all closed on failure as evaluation instants."""
        for bad in (-1, True, "1"):
            with self.assertRaises(ValueError):
                progress_view.build_progress_view(
                    self.conn, SYMBOL, as_of_ms=bad)
            with self.assertRaises(ValueError):
                progress_view.build_board(
                    self.conn, [SYMBOL], as_of_ms=bad)

    # ------------------------------------------------------------------
    # h. Zero-write guarantee over every sqlite_master table
    # ------------------------------------------------------------------

    def test_h_read_only_calls_leave_all_counts_unchanged(self) -> None:
        """View and board calls never change any table's row count."""
        posts, relations = self._two_posts(SYMBOL)
        self._commit_page(SYMBOL, "new", "b-new", "n1", posts, relations, None)
        queue.enqueue(self.conn, OTHER_SYMBOL, "history", "b-hist", "h1")
        before = self._snapshot_counts()
        progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=AS_OF_MS)
        progress_view.build_progress_view(
            self.conn, SYMBOL, as_of_ms=AS_OF_MS, sample_limit=1)
        progress_view.build_board(
            self.conn, [SYMBOL, OTHER_SYMBOL, EMPTY_SYMBOL], as_of_ms=AS_OF_MS)
        after = self._snapshot_counts()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
