"""Synthetic end-to-end offline chain for the Xueqiu primary source.

One queue-driven scenario joins every offline module of the reverse-crawl
family: enqueue -> gated fake send -> ledger commit/attempt -> content
coverage -> acceptance evidence -> split units -> multi-source supplement ->
advisory history ranking. Every fixture is synthetic and built in memory or
in a temporary directory; no network, no business database, and no dynamic
execution are required to review these tests. Failure semantics are
uniformly "closed on failure".

These tests are intentionally not executed while the repository's
dynamic-test safety approval is outstanding.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xq_acceptance_evidence  # noqa: E402
import xq_content_coverage  # noqa: E402
import xq_history_priority  # noqa: E402
import xq_multi_source_gap  # noqa: E402
import xq_offline_queue as queue  # noqa: E402
import xq_reverse_ledger as ledger  # noqa: E402
import xq_reverse_store as store  # noqa: E402
import xq_split_units  # noqa: E402
from xq_request_gate import RequestGate  # noqa: E402


#: The scanned stock that owns verified target-symbol content evidence.
TARGET_SYMBOL: str = "SH600000"

#: The second stock whose lane only ever sees a transit page in this chain.
TRANSIT_SYMBOL: str = "SZ000001"

#: Fetch-confirmation times (Unix ms), kept apart from post content times.
FETCH_TARGET_MS: int = 1_700_000_100_000
FETCH_TRANSIT_MS: int = 1_700_000_200_000
FETCH_FAILURE_MS: int = 1_700_000_300_000

#: Content times (Unix ms) of the three target-lane originals, descending.
TARGET_TIME_P1_MS: int = 1_700_000_003_000
TARGET_TIME_P2_MS: int = 1_700_000_002_000
TARGET_TIME_P3_MS: int = 1_700_000_001_000

#: Synthetic failure reason echoed by the fake 404 sender.
FAILURE_REASON: str = "synthetic missing page"

#: Admission-evidence section keys every exported bundle must carry.
BUNDLE_SECTION_KEYS: tuple[str, ...] = (
    "symbol", "lane", "watermark", "coverage", "post_samples",
    "source_chain", "failures", "pending", "gaps",
)

#: Synthetic failure reason echoed by the fake 401 sender.
TOKEN_INVALID_REASON: str = "synthetic revoked token"

#: Fetch-confirmation times (Unix ms) for the counterexample scenarios.
FETCH_TOKEN_INVALID_MS: int = 1_700_000_401_000
FETCH_HTTP_403_MS: int = 1_700_000_402_000
FETCH_EMPTY_PAGE_MS: int = 1_700_000_403_000
FETCH_PARTIAL_PAGE_MS: int = 1_700_000_404_000
FETCH_DUPLICATE_MS: int = 1_700_000_405_000
FETCH_STOPPED_MS: int = 1_700_000_406_000
FETCH_PAGE_LIMIT_MS: int = 1_700_000_407_000
FETCH_INTERVAL_MS: int = 1_700_000_408_000
FETCH_AWAITING_MS: int = 1_700_000_409_000
FETCH_BOUNDARY_MS: int = 1_700_000_410_000
FETCH_RACE_MS: int = 1_700_000_411_000
FETCH_BATCH_A_MS: int = 1_700_000_412_000
FETCH_BATCH_B_MS: int = 1_700_000_413_000

#: Content times (Unix ms) of the cross-page boundary pages, deliberately
#: ascending so the second page violates the global descending order.
BOUNDARY_TIME_P1_MS: int = 1_700_000_200_000
BOUNDARY_TIME_P2_MS: int = 1_700_000_201_000

#: The shared original observed in two different batches of one stock.
CROSS_BATCH_POST_ID: str = "cross-batch-post"
CROSS_BATCH_TIME_MS: int = 1_700_000_300_000


@dataclass(frozen=True)
class ChainFixture:
    """Material shared by every assertion after the synthetic chain runs."""

    target_posts: tuple[store.RawPostInput, ...]
    target_relations: tuple[store.PostSymbolInput, ...]
    transit_posts: tuple[store.RawPostInput, ...]
    results: tuple[queue.StepResult, ...]
    sent: tuple[queue.QueueTask, ...]


@dataclass(frozen=True)
class FrozenClock:
    """Fixed timestamp source so a gate pacing denial is deterministic."""

    at: float

    def now(self) -> float:
        """Return the same Unix timestamp for every gate query."""
        return self.at


class OfflineChainE2ETests(unittest.TestCase):
    """Queue-driven chain from enqueue to advisory ranking, all offline."""

    def setUp(self) -> None:
        """Create an isolated queue database and one shared fake-send gate."""
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.conn = sqlite3.connect(root / "offline.sqlite3", isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        queue.init_schema(self.conn)
        self.gate = RequestGate(
            root / "gate.sqlite3", max_requests=100,
            window_seconds=3600, min_interval_seconds=0)

    def tearDown(self) -> None:
        """Dispose of the private fixture connection and temporary files."""
        self.conn.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Synthetic fixture builders
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

    def _run_chain(self) -> ChainFixture:
        """Enqueue three tasks and drive them with three preset fake sends.

        Claim order follows the queue sequence: the target page for
        ``TARGET_SYMBOL`` confirms first, the transit page for
        ``TRANSIT_SYMBOL`` confirms second, and the failing page for the
        remaining history lane records its attempt third. The sender keys
        every preset response by the claimed ``QueueTask`` identity, so the
        mapping does not depend on scheduling accidents.
        """
        target_posts = (
            self.post("sh-p1", TARGET_TIME_P1_MS,
                      "放量上攻，继续看好 SH600000 的基本面"),
            self.post("sh-p2", TARGET_TIME_P2_MS,
                      "大盘震荡整理，板块轮动加快", author=None),
            self.post("sh-p3", TARGET_TIME_P3_MS,
                      "长期跟踪 SH600000，估值处于低位"),
        )
        # Only sh-p1 and sh-p3 carry verified target mentions; sh-p2 is an
        # unrelated page member and must never enter the coverage boundary.
        target_relations = (
            self.relation("sh-p1", TARGET_SYMBOL),
            self.relation("sh-p3", TARGET_SYMBOL),
        )
        transit_posts = (
            self.post("sz-p1", 1_700_000_002_500,
                      "过路页内容之一，与目标股无关", author=None),
            self.post("sz-p2", 1_700_000_001_500,
                      "过路页内容之二，未提及任何目标", author=None),
        )
        target_page = queue.CompletePage(
            TARGET_SYMBOL, "n1", True, True, True,
            tuple(post.post_id for post in target_posts),
            target_posts, target_relations, "n2",
        )
        transit_page = queue.CompletePage(
            TRANSIT_SYMBOL, "z1", True, True, True,
            tuple(post.post_id for post in transit_posts),
            transit_posts, (), "z2",
        )
        responses: dict[tuple[str, str, str], queue.OfflineResponse] = {
            (TARGET_SYMBOL, "new", "n1"): queue.OfflineResponse(200, target_page),
            (TRANSIT_SYMBOL, "history", "z1"): queue.OfflineResponse(200, transit_page),
            (TARGET_SYMBOL, "history", "h1"): queue.OfflineResponse(
                404, None, FAILURE_REASON),
        }
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "new-sh", "n1")
        queue.enqueue(self.conn, TRANSIT_SYMBOL, "history", "hist-sz", "z1")
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "hist-sh", "h1")
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            """Return the preset response for this exact claimed task."""
            sent.append(item)
            return responses[(item.symbol, item.lane, item.page_key)]

        results = (
            queue.run_one(self.conn, self.gate, send_once, FETCH_TARGET_MS),
            queue.run_one(self.conn, self.gate, send_once, FETCH_TRANSIT_MS),
            queue.run_one(self.conn, self.gate, send_once, FETCH_FAILURE_MS),
        )
        return ChainFixture(
            target_posts=target_posts,
            target_relations=target_relations,
            transit_posts=transit_posts,
            results=results,
            sent=tuple(sent),
        )

    def _bundle(self, symbol: str, lane: str) -> dict:
        """Export one evidence bundle through the production read model."""
        return xq_acceptance_evidence.export_evidence(self.conn, symbol, lane)

    # ------------------------------------------------------------------
    # 1+2. Queue-driven sends: confirm target, confirm transit, pause fail
    # ------------------------------------------------------------------

    def test_chain_confirms_target_and_transit_then_pauses_on_failure(self) -> None:
        """Three fake sends confirm two pages and quarantine the failing one."""
        chain = self._run_chain()
        self.assertEqual(
            [result.outcome for result in chain.results],
            ["confirmed", "confirmed", "http_404"])
        self.assertEqual(
            [(task.symbol, task.lane, task.page_key) for task in chain.sent],
            [(TARGET_SYMBOL, "new", "n1"),
             (TRANSIT_SYMBOL, "history", "z1"),
             (TARGET_SYMBOL, "history", "h1")])
        # Exactly one physical send per run_one call, in claim order.
        self.assertEqual(
            [(result.task.symbol, result.task.lane) for result in chain.results],
            [(TARGET_SYMBOL, "new"),
             (TRANSIT_SYMBOL, "history"),
             (TARGET_SYMBOL, "history")])
        states = {(task.symbol, task.lane): (task.state, task.page_key)
                  for task in queue.tasks(self.conn)}
        self.assertEqual(states[(TARGET_SYMBOL, "new")], ("pending", "n2"))
        self.assertEqual(states[(TRANSIT_SYMBOL, "history")], ("pending", "z2"))
        self.assertEqual(states[(TARGET_SYMBOL, "history")], ("paused", "h1"))
        # Successes advanced exactly their own lane watermarks.
        target_new = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(target_new["version"], 1)
        self.assertEqual(target_new["confirmed_page_key"], "n1")
        self.assertEqual(target_new["next_cursor"], "n2")
        self.assertEqual(target_new["state"], "confirmed")
        transit = ledger.get_checkpoint(self.conn, TRANSIT_SYMBOL, "history")
        self.assertEqual(transit["version"], 1)
        self.assertEqual(transit["next_cursor"], "z2")
        # The failing lane records its attempt and never advances its watermark.
        attempts = self.conn.execute(
            "SELECT status, detail FROM xq_reverse_page_attempt "
            "WHERE symbol = ? AND lane = ?",
            (TARGET_SYMBOL, "history")).fetchall()
        self.assertEqual([row[0] for row in attempts], ["http_404"])
        self.assertIn("404", attempts[0][1])
        self.assertIn(FAILURE_REASON, attempts[0][1])
        self.assertIn("404", chain.results[2].detail)
        failed = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "history")
        self.assertEqual(failed["version"], 0)
        self.assertIsNone(failed["next_cursor"])
        self.assertEqual(failed["state"], "pending")
        # Only the five confirmed originals exist; the failure wrote no post.
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 5)

    # ------------------------------------------------------------------
    # 3. Coverage attribution
    # ------------------------------------------------------------------

    def test_coverage_attributes_target_bounds_and_leaves_transit_empty(self) -> None:
        """Target content bounds stay separate from fetch times and transit."""
        self._run_chain()
        target = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new")
        # Content-time boundary covers exactly the two evidenced posts.
        self.assertEqual(target.earliest_created_at_ms, TARGET_TIME_P3_MS)
        self.assertEqual(target.earliest_post_id, "sh-p3")
        self.assertEqual(target.latest_created_at_ms, TARGET_TIME_P1_MS)
        self.assertEqual(target.latest_post_id, "sh-p1")
        self.assertEqual(target.evidenced_post_count, 2)
        # The unrelated middle page member never enters the boundary.
        self.assertNotIn(TARGET_TIME_P2_MS,
                         (target.earliest_created_at_ms, target.latest_created_at_ms))
        # Fetch-confirmation times occupy their own columns, not content times.
        self.assertEqual(target.coverage_started_at, FETCH_TARGET_MS)
        self.assertEqual(target.coverage_updated_at, FETCH_TARGET_MS)
        self.assertNotIn(target.coverage_started_at,
                         (target.earliest_created_at_ms, target.latest_created_at_ms))
        # The transit stock's scan advanced without any content coverage.
        transit = xq_content_coverage.read_content_coverage(
            self.conn, TRANSIT_SYMBOL, "history")
        self.assertEqual(transit.evidenced_post_count, 0)
        for value in (transit.earliest_created_at_ms, transit.earliest_post_id,
                      transit.latest_created_at_ms, transit.latest_post_id,
                      transit.coverage_started_at, transit.coverage_updated_at):
            self.assertIsNone(value)
        checkpoint = ledger.get_checkpoint(self.conn, TRANSIT_SYMBOL, "history")
        self.assertEqual(checkpoint["version"], 1)
        self.assertEqual(checkpoint["next_cursor"], "z2")
        self.assertIsNone(checkpoint["coverage_started_at"])
        self.assertIsNone(checkpoint["first_status_id"])
        # The failed lane cannot fabricate a boundary either.
        failed = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "history")
        self.assertEqual(failed.evidenced_post_count, 0)
        self.assertIsNone(failed.earliest_created_at_ms)
        self.assertIsNone(failed.coverage_started_at)

    # ------------------------------------------------------------------
    # 4. Acceptance-evidence export (PRD section 5 sample structure)
    # ------------------------------------------------------------------

    def test_evidence_bundles_carry_offline_admission_fields(self) -> None:
        """Every bundle carries IDs, times, coverage, watermark and failures."""
        self._run_chain()
        bundle_new = self._bundle(TARGET_SYMBOL, "new")
        bundle_hist = self._bundle(TARGET_SYMBOL, "history")
        bundle_transit = self._bundle(TRANSIT_SYMBOL, "history")
        for bundle in (bundle_new, bundle_hist, bundle_transit):
            for key in BUNDLE_SECTION_KEYS:
                self.assertIn(key, bundle)

        # Single-stock sample: one bundle with every admission field complete.
        watermark = bundle_new["watermark"]
        self.assertEqual(watermark["version"], 1)
        self.assertEqual(watermark["confirmed_page_key"], "n1")
        self.assertEqual(watermark["next_cursor"], "n2")
        self.assertEqual(watermark["state"], "confirmed")
        self.assertEqual(watermark["batch_id"], "new-sh")
        self.assertEqual(watermark["fetched_at"], FETCH_TARGET_MS)
        self.assertEqual(watermark["first_status_id"], "sh-p1")
        self.assertEqual(watermark["last_status_id"], "sh-p3")
        coverage = bundle_new["coverage"]
        self.assertEqual(coverage["status"], "ok")
        self.assertEqual(coverage["earliest_created_at_ms"], TARGET_TIME_P3_MS)
        self.assertEqual(coverage["earliest_post_id"], "sh-p3")
        self.assertEqual(coverage["latest_created_at_ms"], TARGET_TIME_P1_MS)
        self.assertEqual(coverage["latest_post_id"], "sh-p1")
        self.assertEqual(coverage["evidenced_post_count"], 2)
        self.assertEqual(coverage["coverage_started_at"], FETCH_TARGET_MS)
        self.assertEqual(coverage["coverage_updated_at"], FETCH_TARGET_MS)
        samples = bundle_new["post_samples"]
        self.assertEqual([sample["post_id"] for sample in samples],
                         ["sh-p1", "sh-p3"])
        for sample in samples:
            self.assertIn(sample["created_at_ms"],
                          (TARGET_TIME_P1_MS, TARGET_TIME_P3_MS))
            self.assertEqual(sample["first_fetched_at"], FETCH_TARGET_MS)
            self.assertEqual(sample["last_fetched_at"], FETCH_TARGET_MS)
            self.assertTrue(sample["original_url"].startswith("https://"))
            self.assertEqual(sample["batch_id"], "new-sh")
            self.assertEqual(sample["batch_ids"], ["new-sh"])
            # Original source is the stored xueqiu label, never inferred.
            row = self.conn.execute(
                "SELECT source FROM xq_reverse_raw_post WHERE post_id = ?",
                (sample["post_id"],)).fetchone()
            self.assertEqual(row, ("xueqiu",))
        self.assertEqual(bundle_new["failures"], [])
        chain = bundle_new["source_chain"]
        self.assertEqual(chain["downstream_units"], "not_linked")
        self.assertEqual(sorted(chain["post_links"]), ["sh-p1", "sh-p3"])
        for links in chain["post_links"].values():
            self.assertEqual(links, [{
                "symbol": TARGET_SYMBOL,
                "evidence_type": "post_content",
                "batch_id": "new-sh",
            }])
        self.assertEqual(bundle_new["pending"], {
            "queue": "initialized", "state": "pending",
            "page_key": "n2", "claim_id": None})
        self.assertEqual(bundle_new["gaps"], {"type": "unknown"})

        # Failure reasons stay visible on the paused lane.
        self.assertEqual(bundle_hist["watermark"]["version"], 0)
        self.assertIsNone(bundle_hist["watermark"]["next_cursor"])
        self.assertEqual(bundle_hist["coverage"], {"status": "empty"})
        # post_samples and source_chain are stock-scoped provenance sections
        # (pinned by test_xq_acceptance_evidence_offline.py): the same
        # evidenced originals appear on every lane bundle of this stock.
        # Only coverage, watermark, failures, pending and gaps are lane-scoped.
        self.assertEqual(bundle_hist["post_samples"], bundle_new["post_samples"])
        failures = bundle_hist["failures"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["status"], "http_404")
        self.assertEqual(failures[0]["page_key"], "h1")
        self.assertEqual(failures[0]["batch_id"], "hist-sh")
        self.assertIn(FAILURE_REASON, failures[0]["detail"])
        self.assertEqual(bundle_hist["pending"]["state"], "paused")
        self.assertEqual(bundle_hist["pending"]["page_key"], "h1")

        # Two-stock comparison: same structure, different evidence content.
        self.assertEqual(bundle_transit["symbol"], TRANSIT_SYMBOL)
        self.assertEqual(bundle_transit["coverage"], {"status": "empty"})
        self.assertEqual(bundle_transit["post_samples"], [])
        self.assertEqual(bundle_transit["failures"], [])
        self.assertEqual(bundle_transit["watermark"]["version"], 1)
        self.assertEqual(bundle_transit["watermark"]["next_cursor"], "z2")
        self.assertEqual(bundle_transit["gaps"],
                         {"type": "history_unfinished", "next_cursor": "z2"})
        self.assertEqual(bundle_new["coverage"]["status"], "ok")
        self.assertEqual(len(bundle_new["post_samples"]), 2)
        self.assertNotEqual(bundle_new["coverage"], bundle_transit["coverage"])

    # ------------------------------------------------------------------
    # 5. Split-unit lineage over the exported sample posts
    # ------------------------------------------------------------------

    def test_split_units_trace_back_to_evidence_post_ids_and_times(self) -> None:
        """Split units round-trip to the evidence bundle's IDs and times."""
        chain = self._run_chain()
        bundle_new = self._bundle(TARGET_SYMBOL, "new")
        sample_times = {sample["post_id"]: sample["created_at_ms"]
                        for sample in bundle_new["post_samples"]}
        self.assertEqual(set(sample_times), {"sh-p1", "sh-p3"})
        post_by_id = {post.post_id: post for post in chain.target_posts}
        relation_by_post = {relation.post_id: relation
                            for relation in chain.target_relations}

        units_p1 = xq_split_units.split_post(
            post_by_id["sh-p1"], (relation_by_post["sh-p1"],))
        self.assertEqual([unit.unit_type for unit in units_p1],
                         ["post", "author", "time", "mention", "content"])
        for unit in units_p1:
            self.assertEqual(unit.raw_post_id, "sh-p1")
            self.assertEqual(unit.source, "xueqiu")
        # The post and time units carry the evidence package's content time.
        self.assertEqual(
            xq_split_units.origin(units_p1, units_p1[0].unit_id)["created_at_ms"],
            sample_times["sh-p1"])
        time_unit = next(unit for unit in units_p1
                         if unit.unit_type == xq_split_units.UNIT_TYPE_TIME)
        self.assertEqual(time_unit.payload["created_at_ms"], sample_times["sh-p1"])
        # The mention unit records the same stock as the stored relation.
        mention_unit = next(unit for unit in units_p1
                            if unit.unit_type == xq_split_units.UNIT_TYPE_MENTION)
        self.assertEqual(mention_unit.payload["symbol"], TARGET_SYMBOL)
        self.assertEqual(mention_unit.payload["evidence_type"], "post_content")
        mention_origin = xq_split_units.origin(units_p1, mention_unit.unit_id)
        self.assertEqual(mention_origin["raw_post_id"], "sh-p1")
        self.assertEqual(mention_origin["unit_type"], "mention")
        self.assertEqual(mention_origin["symbol"], TARGET_SYMBOL)

        units_p3 = xq_split_units.split_post(
            post_by_id["sh-p3"], (relation_by_post["sh-p3"],))
        self.assertEqual(units_p3[0].raw_post_id, "sh-p3")
        self.assertEqual(units_p3[0].payload["created_at_ms"], sample_times["sh-p3"])
        all_units = list(units_p1) + list(units_p3)
        chain_view = xq_split_units.lineage(all_units)
        self.assertEqual(sorted(chain_view), ["sh-p1", "sh-p3"])
        self.assertEqual(chain_view["sh-p1"]["unit_ids"],
                         [unit.unit_id for unit in units_p1])
        self.assertEqual(chain_view["sh-p1"]["symbols"], [TARGET_SYMBOL])
        self.assertEqual(chain_view["sh-p1"]["source"], "xueqiu")
        self.assertEqual(chain_view["sh-p1"]["downstream"],
                         xq_split_units.DOWNSTREAM_NOT_LINKED)
        self.assertEqual(chain_view["sh-p3"]["symbols"], [TARGET_SYMBOL])
        # Splitting the same original again reproduces the same unit IDs.
        self.assertEqual(
            xq_split_units.split_post(post_by_id["sh-p1"],
                                      (relation_by_post["sh-p1"],)),
            list(units_p1))

    # ------------------------------------------------------------------
    # 6. Multi-source supplement merge
    # ------------------------------------------------------------------

    def test_tdx_supplement_never_rewrites_coverage_gaps(self) -> None:
        """A TDX supplement attaches as evidence and degrades honestly."""
        self._run_chain()
        bundle_new = self._bundle(TARGET_SYMBOL, "new")
        supplement = xq_multi_source_gap.Supplement(
            source="tdx", symbol=TARGET_SYMBOL, data_type="quote",
            payload={"price": 10.5, "volume": 12345}, as_of=FETCH_TARGET_MS)
        merged = xq_multi_source_gap.merge_supplement(bundle_new, [supplement])
        # coverage and gaps are copied verbatim, never upgraded by market data.
        self.assertEqual(merged["coverage"]["status"], "ok")
        self.assertEqual(merged["coverage"]["earliest_post_id"], "sh-p3")
        self.assertEqual(merged["coverage"]["latest_post_id"], "sh-p1")
        self.assertEqual(merged["coverage"], bundle_new["coverage"])
        self.assertEqual(merged["gaps"], {"type": "unknown"})
        self.assertEqual(merged["gaps"], bundle_new["gaps"])
        degradation = merged["degradation"]
        self.assertEqual(degradation["note"], xq_multi_source_gap.UPGRADE_NOTE)
        self.assertFalse(degradation["degraded"])
        self.assertEqual(degradation["xueqiu_posts"], "ok")
        self.assertEqual(degradation["gaps_type"], "unknown")
        rows = merged["supplements"]["tdx"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "tdx")
        self.assertEqual(rows[0]["data_type"], "quote")
        self.assertEqual(rows[0]["as_of"], FETCH_TARGET_MS)
        self.assertEqual(rows[0]["payload"], {"price": 10.5, "volume": 12345})
        # The read-only merge never mutates the input bundle.
        self.assertNotIn("degradation", bundle_new)
        self.assertNotIn("supplements", bundle_new)

        # An unfinished-history stock stays degraded even with TDX rows.
        bundle_transit = self._bundle(TRANSIT_SYMBOL, "history")
        merged_transit = xq_multi_source_gap.merge_supplement(
            bundle_transit,
            [xq_multi_source_gap.Supplement(
                source="tdx", symbol=TRANSIT_SYMBOL, data_type="timeseries",
                payload={"bars": 3}, as_of="tdx-2026-10-20")])
        self.assertTrue(merged_transit["degradation"]["degraded"])
        self.assertEqual(merged_transit["degradation"]["gaps_type"],
                         "history_unfinished")
        self.assertEqual(merged_transit["degradation"]["note"],
                         xq_multi_source_gap.UPGRADE_NOTE)
        self.assertEqual(merged_transit["coverage"], {"status": "empty"})
        self.assertEqual(merged_transit["gaps"],
                         {"type": "history_unfinished", "next_cursor": "z2"})

    # ------------------------------------------------------------------
    # 7. Advisory history-priority ranking
    # ------------------------------------------------------------------

    def test_history_ranking_is_advisory_and_side_effect_free(self) -> None:
        """Ranking explains its order and never touches queue or watermarks."""
        self._run_chain()
        bundles = [
            self._bundle(TARGET_SYMBOL, "new"),
            self._bundle(TARGET_SYMBOL, "history"),
            self._bundle(TRANSIT_SYMBOL, "history"),
        ]
        lanes = ((TARGET_SYMBOL, "new"), (TARGET_SYMBOL, "history"),
                 (TRANSIT_SYMBOL, "history"), (TRANSIT_SYMBOL, "new"))
        before_tasks = queue.tasks(self.conn)
        before_checkpoints = {
            key: ledger.get_checkpoint(self.conn, key[0], key[1]) for key in lanes}
        ranked = xq_history_priority.rank_history_targets(bundles, budget_units=2)
        after_tasks = queue.tasks(self.conn)
        after_checkpoints = {
            key: ledger.get_checkpoint(self.conn, key[0], key[1]) for key in lanes}

        # Output is advisory, explainable and budget-bounded.
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].symbol, TRANSIT_SYMBOL)
        self.assertEqual(ranked[0].lane, "history")
        self.assertEqual(ranked[1].symbol, TARGET_SYMBOL)
        self.assertEqual(ranked[1].lane, "new")
        self.assertGreater(ranked[0].priority_score, ranked[1].priority_score)
        self.assertAlmostEqual(
            ranked[0].priority_score,
            xq_history_priority.GAP_UNFINISHED_WITH_CURSOR_WEIGHT / 2)
        for result in ranked:
            self.assertTrue(result.advisory)
            self.assertGreaterEqual(len(result.reasons), 5)
            for reason in result.reasons:
                self.assertTrue(reason.strip())
            self.assertEqual(result.trend_inputs["trend_aggregation"],
                             xq_history_priority.TREND_AGGREGATION)
        self.assertIn("gap_factor=", ranked[0].reasons[0])
        self.assertIn("history_unfinished", ranked[0].reasons[0])

        # No scheduling side effects: queue rows and watermarks are verbatim.
        self.assertEqual(after_tasks, before_tasks)
        self.assertEqual(after_checkpoints, before_checkpoints)

    # ------------------------------------------------------------------
    # 8. Remaining run_one terminals (WO-035 group C counterexamples)
    # ------------------------------------------------------------------

    @staticmethod
    def _page(symbol: str, page_key: str, posts: tuple[store.RawPostInput, ...],
              relations: tuple[store.PostSymbolInput, ...],
              next_cursor: str | None, *,
              complete: bool = True) -> queue.CompletePage:
        """Attest one synthetic page with its full ordered member roster."""
        return queue.CompletePage(
            symbol, page_key, complete, True, True,
            tuple(post.post_id for post in posts), posts, relations, next_cursor)

    def _assert_quarantine(self, symbol: str, lane: str, status: str,
                           detail_part: str = "") -> None:
        """Assert one unconfirmed terminal parked its task with one receipt."""
        task = {(row.symbol, row.lane): row
                for row in queue.tasks(self.conn)}[(symbol, lane)]
        self.assertEqual(task.state, "paused")
        self.assertIsNone(task.claim_id)
        attempts = self.conn.execute(
            "SELECT status, detail FROM xq_reverse_page_attempt "
            "WHERE symbol = ? AND lane = ? ORDER BY attempt_id",
            (symbol, lane)).fetchall()
        self.assertEqual([row[0] for row in attempts], [status])
        if detail_part:
            self.assertIn(detail_part, attempts[0][1])
        bundle = self._bundle(symbol, lane)
        self.assertEqual([failure["status"] for failure in bundle["failures"]],
                         [status])
        self.assertEqual(bundle["pending"]["state"], "paused")

    def test_run_one_token_invalid_pauses_lane_without_advancing_ledger(self) -> None:
        """An HTTP 401 parks its lane with a token_invalid receipt and no posts."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "tok-batch", "t1")
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(401, None, TOKEN_INVALID_REASON)

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_TOKEN_INVALID_MS)
        self.assertEqual(result.outcome, "token_invalid")
        self.assertIn("401", result.detail)
        self.assertIn(TOKEN_INVALID_REASON, result.detail)
        self.assertEqual([(task.symbol, task.lane) for task in sent],
                         [(TARGET_SYMBOL, "new")])
        self._assert_quarantine(TARGET_SYMBOL, "new", "token_invalid",
                                detail_part=TOKEN_INVALID_REASON)
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(checkpoint["version"], 0)
        self.assertIsNone(checkpoint["next_cursor"])
        self.assertEqual(checkpoint["state"], "pending")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 0)
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(coverage.evidenced_post_count, 0)
        self.assertIsNone(coverage.coverage_started_at)
        bundle = self._bundle(TARGET_SYMBOL, "new")
        self.assertEqual(bundle["coverage"], {"status": "empty"})
        self.assertIn(TOKEN_INVALID_REASON, bundle["failures"][0]["detail"])
        # A token failure counts against the round but never stops it.
        self.assertFalse(self.gate.snapshot().stopped)
        self.assertEqual(self.gate.snapshot().failure_total, 1)

    def test_run_one_http_403_stops_round_and_freezes_peer_tasks(self) -> None:
        """An HTTP 403 stops the shared gate and quarantines only its own task."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "stop-batch", "s1")
        queue.enqueue(self.conn, TRANSIT_SYMBOL, "history", "stop-sz", "z1")
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(403)

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_HTTP_403_MS)
        self.assertEqual(result.outcome, "403")
        self.assertIn("HTTP 403", result.detail)
        self._assert_quarantine(TARGET_SYMBOL, "new", "403",
                                detail_part="request gate stopped on HTTP 403")
        self.assertTrue(self.gate.snapshot().stopped)
        self.assertEqual(self.gate.snapshot().stop_reason, "403")
        # The sending lane never advanced its ledger or coverage.
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TARGET_SYMBOL, "new")["version"], 0)
        self.assertEqual(xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new").evidenced_post_count, 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 0)
        # The peer task keeps its pending row and is never claimed or sent.
        follow_up = queue.run_one(self.conn, self.gate, send_once,
                                  FETCH_HTTP_403_MS + 1)
        self.assertEqual(follow_up.outcome, "stopped")
        self.assertIsNone(follow_up.task)
        self.assertIn("manual reset", follow_up.detail)
        self.assertEqual(len(sent), 1)
        peer = {(task.symbol, task.lane): task for task in queue.tasks(self.conn)}
        self.assertEqual(peer[(TARGET_SYMBOL, "new")].state, "paused")
        self.assertEqual(peer[(TRANSIT_SYMBOL, "history")].state, "pending")
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TRANSIT_SYMBOL, "history")["version"], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_page_attempt "
            "WHERE symbol = ?", (TRANSIT_SYMBOL,)).fetchone()[0], 0)

    def test_run_one_empty_page_records_empty_attempt_only(self) -> None:
        """An attested empty page records one empty attempt and writes no post."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "empty-batch", "e1")
        empty_page = self._page(TARGET_SYMBOL, "e1", (), (), None)
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(200, empty_page)

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_EMPTY_PAGE_MS)
        self.assertEqual(result.outcome, "empty")
        self.assertIn("cannot advance a checkpoint", result.detail)
        self.assertEqual(len(sent), 1)
        self._assert_quarantine(TARGET_SYMBOL, "history", "empty",
                                detail_part="cannot advance a checkpoint")
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "history")
        self.assertEqual(checkpoint["version"], 0)
        self.assertIsNone(checkpoint["next_cursor"])
        self.assertEqual(checkpoint["state"], "pending")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 0)
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "history")
        self.assertEqual(coverage.evidenced_post_count, 0)
        self.assertIsNone(coverage.earliest_created_at_ms)
        bundle = self._bundle(TARGET_SYMBOL, "history")
        self.assertEqual(bundle["coverage"], {"status": "empty"})
        self.assertEqual(bundle["post_samples"], [])
        self.assertEqual(bundle["watermark"]["version"], 0)

    def test_run_one_unattested_page_records_partial_page_failed(self) -> None:
        """A page lacking completeness attestation quarantines as partial."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "partial-batch", "p1")
        post = self.post("par-p1", TARGET_TIME_P1_MS, "unattested page member")
        unattested = self._page(TARGET_SYMBOL, "p1", (post,), (), None,
                               complete=False)
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(200, unattested)

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_PARTIAL_PAGE_MS)
        self.assertEqual(result.outcome, "partial_page_failed")
        self.assertIn("unverified", result.detail)
        self.assertEqual(len(sent), 1)
        self._assert_quarantine(
            TARGET_SYMBOL, "new", "partial_page_failed",
            detail_part="page completeness, ordering, query binding or key unverified")
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(checkpoint["version"], 0)
        self.assertEqual(checkpoint["state"], "pending")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 0)
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(coverage.evidenced_post_count, 0)
        self.assertIsNone(coverage.coverage_started_at)
        bundle = self._bundle(TARGET_SYMBOL, "new")
        self.assertEqual(bundle["coverage"], {"status": "empty"})
        self.assertEqual(bundle["post_samples"], [])

    def test_run_one_duplicate_replay_never_advances_checkpoint(self) -> None:
        """Duplicate replays of a confirmed page record attempts without a new version."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "dup-batch", "d1")
        post = self.post("dup-p1", TARGET_TIME_P1_MS, "replayed original SH600000")
        relation = self.relation("dup-p1", TARGET_SYMBOL)
        page = self._page(TARGET_SYMBOL, "d1", (post,), (relation,), "d2")
        ledger.commit_page(self.conn, TARGET_SYMBOL, "new", "dup-batch", "d1",
                           page.posts, page.relations, "d2", 0,
                           FETCH_DUPLICATE_MS - 1)
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(200, page)

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_DUPLICATE_MS)
        self.assertEqual(result.outcome, "duplicate")
        self.assertIn("already confirmed page", result.detail)
        self._assert_quarantine(TARGET_SYMBOL, "new", "duplicate",
                                detail_part="already confirmed page")
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(checkpoint["version"], 1)
        self.assertEqual(checkpoint["state"], "confirmed")
        self.assertEqual(checkpoint["confirmed_page_key"], "d1")
        self.assertEqual(checkpoint["next_cursor"], "d2")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone()[0], 1)
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(coverage.evidenced_post_count, 1)
        # A divergent payload under the same page key is the same terminal.
        queue.resume_task(self.conn, TARGET_SYMBOL, "new", "operator",
                          "synthetic retry of a confirmed key",
                          acted_at=FETCH_DUPLICATE_MS + 1)
        divergent = self._page(
            TARGET_SYMBOL, "d1",
            (self.post("dup-p2", TARGET_TIME_P2_MS, "divergent payload SH600000"),),
            (), "d2")
        conflict = queue.run_one(
            self.conn, self.gate,
            lambda item: queue.OfflineResponse(200, divergent),
            FETCH_DUPLICATE_MS + 2)
        self.assertEqual(conflict.outcome, "duplicate")
        self.assertIn("another payload", conflict.detail)
        attempts = self.conn.execute(
            "SELECT status FROM xq_reverse_page_attempt WHERE symbol = ? AND lane = ?",
            (TARGET_SYMBOL, "new")).fetchall()
        self.assertEqual([row[0] for row in attempts],
                         ["duplicate", "duplicate"])
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TARGET_SYMBOL, "new")["version"], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 1)

    def test_run_one_stopped_gate_skips_claim_and_send(self) -> None:
        """A tripped round returns stopped without claiming or sending anything."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "held-batch", "r1")
        self.gate.trip("synthetic manual stop")
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(200, self._page(
                TARGET_SYMBOL, "r1",
                (self.post("held-p1", TARGET_TIME_P1_MS, "never fetched"),),
                (), None))

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_STOPPED_MS)
        self.assertEqual(result.outcome, "stopped")
        self.assertIsNone(result.task)
        self.assertIn("manual reset", result.detail)
        self.assertEqual(sent, [])
        task = queue.tasks(self.conn)[0]
        self.assertEqual(task.state, "pending")
        self.assertIsNone(task.claim_id)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_page_attempt").fetchone()[0], 0)
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(checkpoint["version"], 0)
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(coverage.evidenced_post_count, 0)
        bundle = self._bundle(TARGET_SYMBOL, "new")
        self.assertEqual(bundle["failures"], [])
        self.assertEqual(bundle["pending"]["state"], "pending")
        self.assertEqual(self.gate.snapshot().stop_reason, "synthetic manual stop")

    def test_run_one_gate_denied_maps_budget_to_page_limit_and_interval_to_failed(self) -> None:
        """Gate denials surface as page_limit on budget and failed on pacing."""
        root = Path(self._tmp.name)
        budget_gate = RequestGate(root / "gate_budget_map.sqlite3",
                                  max_requests=1, window_seconds=3600,
                                  min_interval_seconds=0)
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "map-a", "m1")
        queue.enqueue(self.conn, TRANSIT_SYMBOL, "history", "map-b", "z1")
        sent: list[queue.QueueTask] = []
        page = self._page(TARGET_SYMBOL, "m1",
                          (self.post("map-p1", TARGET_TIME_P1_MS,
                                     "budget page SH600000"),), (), None)

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(200, page)

        first = queue.run_one(self.conn, budget_gate, send_once, FETCH_PAGE_LIMIT_MS)
        self.assertEqual(first.outcome, "confirmed")
        second = queue.run_one(self.conn, budget_gate, send_once,
                               FETCH_PAGE_LIMIT_MS + 1)
        self.assertEqual(second.outcome, "page_limit")
        self.assertIn("budget", second.detail)
        self._assert_quarantine(TRANSIT_SYMBOL, "history", "page_limit",
                                detail_part="request budget exhausted")
        self.assertEqual(len(sent), 1)
        self.assertEqual(budget_gate.snapshot().issued_total, 1)
        self.assertFalse(budget_gate.snapshot().stopped)
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TRANSIT_SYMBOL, "history")["version"], 0)
        self.assertEqual(xq_content_coverage.read_content_coverage(
            self.conn, TRANSIT_SYMBOL, "history").evidenced_post_count, 0)

        paced_gate = RequestGate(root / "gate_paced_map.sqlite3",
                                 max_requests=10, window_seconds=3600,
                                 min_interval_seconds=60,
                                 clock=FrozenClock(FETCH_INTERVAL_MS / 1000))
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "map-c", "h1")
        queue.enqueue(self.conn, TRANSIT_SYMBOL, "new", "map-d", "n1")

        def paced_send(item: queue.QueueTask) -> queue.OfflineResponse:
            return queue.OfflineResponse(200, self._page(
                item.symbol, item.page_key,
                (self.post("map-p2", TARGET_TIME_P2_MS,
                           "paced page " + item.symbol),), (), None))

        third = queue.run_one(self.conn, paced_gate, paced_send, FETCH_INTERVAL_MS)
        self.assertEqual(third.outcome, "confirmed")
        fourth = queue.run_one(self.conn, paced_gate, paced_send, FETCH_INTERVAL_MS)
        self.assertEqual(fourth.outcome, "failed")
        self.assertIn("minimum request interval", fourth.detail)
        self._assert_quarantine(
            TRANSIT_SYMBOL, "new", "failed",
            detail_part="minimum request interval not elapsed")
        self.assertFalse(paced_gate.snapshot().stopped)
        self.assertEqual(paced_gate.snapshot().issued_total, 1)
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TRANSIT_SYMBOL, "new")["version"], 0)

    # ------------------------------------------------------------------
    # 9. awaiting_cursor, cross-page boundary and claim-conflict closure
    # ------------------------------------------------------------------

    def test_confirmed_history_page_without_cursor_lands_awaiting_cursor(self) -> None:
        """A confirmed history page without a cursor parks awaiting_cursor with an honest gap."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "cursor-batch", "c1")
        post = self.post("cur-p1", TARGET_TIME_P1_MS,
                         "history page SH600000 without cursor")
        relation = self.relation("cur-p1", TARGET_SYMBOL)
        page = self._page(TARGET_SYMBOL, "c1", (post,), (relation,), None)
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return queue.OfflineResponse(200, page)

        result = queue.run_one(self.conn, self.gate, send_once, FETCH_AWAITING_MS)
        self.assertEqual(result.outcome, "confirmed")
        self.assertEqual(len(sent), 1)
        task = queue.tasks(self.conn)[0]
        self.assertEqual(task.state, "awaiting_cursor")
        self.assertEqual(task.page_key, "c1")
        self.assertIsNone(task.claim_id)
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "history")
        self.assertEqual(checkpoint["version"], 1)
        self.assertEqual(checkpoint["confirmed_page_key"], "c1")
        self.assertIsNone(checkpoint["next_cursor"])
        self.assertEqual(checkpoint["state"], "confirmed")
        bundle = self._bundle(TARGET_SYMBOL, "history")
        self.assertEqual(bundle["pending"], {
            "queue": "initialized", "state": "awaiting_cursor",
            "page_key": "c1", "claim_id": None})
        self.assertEqual(bundle["gaps"],
                         {"type": "history_unfinished", "next_cursor": None})
        self.assertIsNone(bundle["watermark"]["next_cursor"])
        self.assertEqual(bundle["coverage"]["status"], "ok")
        self.assertEqual(bundle["coverage"]["evidenced_post_count"], 1)
        self.assertEqual(bundle["coverage"]["latest_post_id"], "cur-p1")
        self.assertEqual([sample["post_id"] for sample in bundle["post_samples"]],
                         ["cur-p1"])
        self.assertEqual(bundle["failures"], [])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_page_attempt").fetchone()[0], 0)

    def test_cross_page_time_ascent_fails_closed_on_descending_boundary(self) -> None:
        """A second page newer than the first fails the cross-page boundary closed."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "bound-batch", "b1")
        first_post = self.post("bnd-p1", BOUNDARY_TIME_P1_MS,
                               "first page SH600000 content")
        first_page = self._page(
            TARGET_SYMBOL, "b1", (first_post,),
            (self.relation("bnd-p1", TARGET_SYMBOL),), "b2")
        second_post = self.post("bnd-p2", BOUNDARY_TIME_P2_MS,
                                "second page SH600000 content")
        second_page = self._page(
            TARGET_SYMBOL, "b2", (second_post,),
            (self.relation("bnd-p2", TARGET_SYMBOL),), None)
        responses = {
            "b1": queue.OfflineResponse(200, first_page),
            "b2": queue.OfflineResponse(200, second_page),
        }
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return responses[item.page_key]

        first = queue.run_one(self.conn, self.gate, send_once, FETCH_BOUNDARY_MS)
        self.assertEqual(first.outcome, "confirmed")
        second = queue.run_one(self.conn, self.gate, send_once,
                               FETCH_BOUNDARY_MS + 1)
        self.assertEqual(second.outcome, "partial_page_failed")
        self.assertIn("cross-page descending time boundary", second.detail)
        self.assertEqual([task.page_key for task in sent], ["b1", "b2"])
        self._assert_quarantine(
            TARGET_SYMBOL, "new", "partial_page_failed",
            detail_part="cross-page descending time boundary cannot be verified")
        # The first page keeps its watermark and the newer page wrote nothing.
        checkpoint = ledger.get_checkpoint(self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(checkpoint["version"], 1)
        self.assertEqual(checkpoint["confirmed_page_key"], "b1")
        self.assertEqual(checkpoint["next_cursor"], "b2")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT post_id FROM xq_reverse_raw_post").fetchone(), ("bnd-p1",))
        coverage = xq_content_coverage.read_content_coverage(
            self.conn, TARGET_SYMBOL, "new")
        self.assertEqual(coverage.evidenced_post_count, 1)
        self.assertEqual(coverage.latest_post_id, "bnd-p1")
        bundle = self._bundle(TARGET_SYMBOL, "new")
        self.assertEqual([sample["post_id"] for sample in bundle["post_samples"]],
                         ["bnd-p1"])
        self.assertEqual([failure["page_key"] for failure in bundle["failures"]],
                         ["b2"])

    def test_reconcile_claim_conflict_with_concurrent_change_fails_closed(self) -> None:
        """A claim changed mid-reconcile fails closed and keeps a single owner."""
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "race-batch", "r1")
        self.assertIsNotNone(queue._claim(self.conn))
        claim_id = self.conn.execute(
            "SELECT claim_id FROM xq_offline_queue WHERE symbol = ? AND lane = ?",
            (TARGET_SYMBOL, "history")).fetchone()[0]
        self.assertIsNotNone(claim_id)
        # A synthetic trigger releases the claim mid-reconcile, standing in for
        # a concurrent worker so _finish sees a changed claim (changed != 1).
        self.conn.execute(
            "CREATE TRIGGER simulate_concurrent_claim "
            "BEFORE UPDATE ON xq_offline_sequence BEGIN "
            "UPDATE xq_offline_queue SET state='paused', claim_id=NULL "
            f"WHERE symbol='{TARGET_SYMBOL}' AND lane='history'; END")
        with self.assertRaises(RuntimeError) as raised:
            queue.reconcile_claim(self.conn, TARGET_SYMBOL, "history",
                                  "operator", "synthetic race", FETCH_RACE_MS)
        self.assertIn("queue claim changed", str(raised.exception))
        self.conn.execute("DROP TRIGGER simulate_concurrent_claim")
        # Closed on failure: the rolled-back race leaves one owner and no audit.
        tasks = queue.tasks(self.conn)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].state, "claimed")
        self.assertEqual(tasks[0].claim_id, claim_id)
        self.assertEqual(queue.action_log(self.conn, TARGET_SYMBOL, "history"), [])
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TARGET_SYMBOL, "history")["version"], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 0)
        # The surviving claim resolves exactly once after the race is gone.
        result = queue.reconcile_claim(self.conn, TARGET_SYMBOL, "history",
                                       "operator", "second review",
                                       FETCH_RACE_MS + 1)
        self.assertEqual(result.outcome, "quarantined")
        tasks = queue.tasks(self.conn)
        self.assertEqual(tasks[0].state, "paused")
        self.assertIsNone(tasks[0].claim_id)
        rows = queue.action_log(self.conn, TARGET_SYMBOL, "history")
        self.assertEqual([row["action"] for row in rows], ["reconcile"])
        self.assertEqual([row["prior_state"] for row in rows], ["claimed"])
        self.assertEqual([row["resulting_state"] for row in rows], ["paused"])

    # ------------------------------------------------------------------
    # 10. Scoring snapshots, cross-batch samples, full export and budget
    # ------------------------------------------------------------------

    def test_ranking_leaves_reverse_store_tables_untouched(self) -> None:
        """Advisory ranking never mutates original, relation or member rows."""
        self._run_chain()
        tables = ("xq_reverse_raw_post", "xq_reverse_post_symbol",
                  "xq_reverse_page_member")
        before = {name: self.conn.execute(
            f"SELECT * FROM {name} ORDER BY rowid").fetchall() for name in tables}
        self.assertEqual(len(before["xq_reverse_raw_post"]), 5)
        self.assertEqual(len(before["xq_reverse_post_symbol"]), 2)
        self.assertEqual(len(before["xq_reverse_page_member"]), 5)
        bundles = [
            self._bundle(TARGET_SYMBOL, "new"),
            self._bundle(TARGET_SYMBOL, "history"),
            self._bundle(TRANSIT_SYMBOL, "history"),
        ]
        ranked = xq_history_priority.rank_history_targets(bundles, budget_units=2)
        self.assertEqual(len(ranked), 2)
        after = {name: self.conn.execute(
            f"SELECT * FROM {name} ORDER BY rowid").fetchall() for name in tables}
        self.assertEqual(after, before)

    def test_same_post_across_batches_merges_into_one_sample(self) -> None:
        """One original seen in two batches yields one stock sample with both batch IDs."""
        post = self.post(CROSS_BATCH_POST_ID, CROSS_BATCH_TIME_MS,
                         "cross-batch original mentioning SH600000")
        relation = self.relation(CROSS_BATCH_POST_ID, TARGET_SYMBOL)
        new_page = self._page(TARGET_SYMBOL, "n1", (post,), (relation,), None)
        history_page = self._page(TARGET_SYMBOL, "h1", (post,), (relation,), None)
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "batch-a", "n1")
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "batch-b", "h1")
        responses = {
            (TARGET_SYMBOL, "new", "n1"): queue.OfflineResponse(200, new_page),
            (TARGET_SYMBOL, "history", "h1"): queue.OfflineResponse(200, history_page),
        }
        sent: list[queue.QueueTask] = []

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return responses[(item.symbol, item.lane, item.page_key)]

        self.assertEqual(
            queue.run_one(self.conn, self.gate, send_once, FETCH_BATCH_A_MS).outcome,
            "confirmed")
        self.assertEqual(
            queue.run_one(self.conn, self.gate, send_once, FETCH_BATCH_B_MS).outcome,
            "confirmed")
        self.assertEqual([(task.symbol, task.lane) for task in sent],
                         [(TARGET_SYMBOL, "new"), (TARGET_SYMBOL, "history")])
        # One durable original row, one version row per observing batch.
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post_version").fetchone()[0], 2)
        bundle_new = self._bundle(TARGET_SYMBOL, "new")
        bundle_hist = self._bundle(TARGET_SYMBOL, "history")
        # post_samples is stock-scoped: both lane bundles share the merged sample.
        self.assertEqual(bundle_new["post_samples"], bundle_hist["post_samples"])
        samples = bundle_new["post_samples"]
        self.assertEqual([sample["post_id"] for sample in samples],
                         [CROSS_BATCH_POST_ID])
        sample = samples[0]
        self.assertEqual(sample["batch_id"], "batch-a")
        self.assertEqual(sample["batch_ids"], ["batch-a", "batch-b"])
        self.assertEqual(sample["created_at_ms"], CROSS_BATCH_TIME_MS)
        self.assertEqual(sample["first_fetched_at"], FETCH_BATCH_A_MS)
        self.assertEqual(sample["last_fetched_at"], FETCH_BATCH_B_MS)
        # source_chain lists the original once per observed batch.
        chain = bundle_new["source_chain"]
        self.assertEqual(sorted(chain["post_links"]), [CROSS_BATCH_POST_ID])
        self.assertEqual(chain["post_links"][CROSS_BATCH_POST_ID], [
            {"symbol": TARGET_SYMBOL, "evidence_type": "post_content",
             "batch_id": "batch-a"},
            {"symbol": TARGET_SYMBOL, "evidence_type": "post_content",
             "batch_id": "batch-b"},
        ])
        # coverage stays lane-scoped and counts the merged original once each.
        self.assertEqual(bundle_new["coverage"]["evidenced_post_count"], 1)
        self.assertEqual(bundle_hist["coverage"]["evidenced_post_count"], 1)
        self.assertEqual(bundle_new["coverage"]["latest_post_id"],
                         CROSS_BATCH_POST_ID)
        self.assertEqual(bundle_hist["coverage"]["latest_post_id"],
                         CROSS_BATCH_POST_ID)
        self.assertEqual(bundle_new["coverage"]["coverage_started_at"],
                         FETCH_BATCH_A_MS)
        self.assertEqual(bundle_hist["coverage"]["coverage_started_at"],
                         FETCH_BATCH_B_MS)

    def test_export_all_aggregates_every_stock_bundle(self) -> None:
        """Full-lane export carries one complete bundle per scanned stock."""
        self._run_chain()
        # The chain leaves TARGET's history lane quarantined with no checkpoint
        # row, so seed one confirmed history page through the production ledger
        # (same out-of-band pattern as the duplicate-replay test) to make the
        # stock exportable in the history lane.
        recovery = self._page(
            TARGET_SYMBOL, "h1",
            (self.post("hist-r1", TARGET_TIME_P3_MS,
                       "history recovery page SH600000"),), (), "h2")
        ledger.commit_page(self.conn, TARGET_SYMBOL, "history", "hist-sh",
                           "h1", recovery.posts, recovery.relations, "h2", 0,
                           FETCH_AWAITING_MS)
        exported_new = xq_acceptance_evidence.export_all(self.conn, "new")
        self.assertEqual([bundle["symbol"] for bundle in exported_new],
                         [TARGET_SYMBOL])
        self.assertEqual(exported_new, [self._bundle(TARGET_SYMBOL, "new")])
        exported_history = xq_acceptance_evidence.export_all(self.conn, "history")
        self.assertEqual([bundle["symbol"] for bundle in exported_history],
                         [TARGET_SYMBOL, TRANSIT_SYMBOL])
        self.assertEqual(exported_history, [
            self._bundle(TARGET_SYMBOL, "history"),
            self._bundle(TRANSIT_SYMBOL, "history"),
        ])
        # A stock without a checkpoint row in the lane is never synthesized.
        self.assertNotIn(TRANSIT_SYMBOL,
                         [bundle["symbol"] for bundle in exported_new])
        for bundle in exported_new + exported_history:
            for key in BUNDLE_SECTION_KEYS:
                self.assertIn(key, bundle)
        self.assertEqual([bundle["lane"] for bundle in exported_new], ["new"])
        self.assertEqual([bundle["lane"] for bundle in exported_history],
                         ["history", "history"])
        # The aggregated bundles carry the chain's real evidence content.
        self.assertEqual(exported_new[0]["watermark"]["confirmed_page_key"], "n1")
        self.assertEqual(len(exported_new[0]["post_samples"]), 2)
        self.assertEqual(exported_history[0]["watermark"]["confirmed_page_key"],
                         "h1")
        self.assertEqual(exported_history[0]["failures"][0]["status"], "http_404")
        self.assertEqual(exported_history[1]["watermark"]["confirmed_page_key"],
                         "z1")
        self.assertEqual(exported_history[1]["post_samples"], [])

    def test_budget_exhaustion_stops_at_page_limit_without_over_send(self) -> None:
        """An exhausted request budget records page_limit and never sends again."""
        root = Path(self._tmp.name)
        gate = RequestGate(root / "gate_budget.sqlite3", max_requests=2,
                           window_seconds=3600, min_interval_seconds=0)
        queue.enqueue(self.conn, TARGET_SYMBOL, "new", "lim-a", "n1")
        queue.enqueue(self.conn, TARGET_SYMBOL, "history", "lim-b", "h1")
        queue.enqueue(self.conn, TRANSIT_SYMBOL, "new", "lim-c", "z1")
        sent: list[queue.QueueTask] = []
        pages = {
            (TARGET_SYMBOL, "new", "n1"): queue.OfflineResponse(200, self._page(
                TARGET_SYMBOL, "n1",
                (self.post("lim-p1", TARGET_TIME_P1_MS,
                           "budget page SH600000"),),
                (self.relation("lim-p1", TARGET_SYMBOL),), None)),
            (TARGET_SYMBOL, "history", "h1"): queue.OfflineResponse(200, self._page(
                TARGET_SYMBOL, "h1",
                (self.post("lim-p2", TARGET_TIME_P2_MS,
                           "history budget page SH600000"),), (), None)),
        }

        def send_once(item: queue.QueueTask) -> queue.OfflineResponse:
            sent.append(item)
            return pages[(item.symbol, item.lane, item.page_key)]

        outcomes = []
        for index in range(4):
            result = queue.run_one(self.conn, gate, send_once,
                                   FETCH_PAGE_LIMIT_MS + index)
            outcomes.append(result.outcome)
        self.assertEqual(outcomes,
                         ["confirmed", "confirmed", "page_limit", "idle"])
        # Exactly two physical sends for a two-request budget.
        self.assertEqual([(task.symbol, task.lane) for task in sent],
                         [(TARGET_SYMBOL, "new"), (TARGET_SYMBOL, "history")])
        self.assertEqual(gate.snapshot().issued_total, 2)
        self.assertEqual(gate.snapshot().dispatched_total, 2)
        self.assertFalse(gate.snapshot().stopped)
        # The denied lane closes on failure with no ledger or coverage change.
        self._assert_quarantine(TRANSIT_SYMBOL, "new", "page_limit",
                                detail_part="request budget exhausted")
        checkpoint = ledger.get_checkpoint(self.conn, TRANSIT_SYMBOL, "new")
        self.assertEqual(checkpoint["version"], 0)
        self.assertEqual(checkpoint["state"], "pending")
        self.assertEqual(xq_content_coverage.read_content_coverage(
            self.conn, TRANSIT_SYMBOL, "new").evidenced_post_count, 0)
        # The two confirmed lanes advanced only their own watermarks.
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TARGET_SYMBOL, "new")["version"], 1)
        self.assertEqual(ledger.get_checkpoint(
            self.conn, TARGET_SYMBOL, "history")["version"], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
