"""Offline integration contract for original posts, lane checkpoints and gate."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xq_request_gate as gate_module  # noqa: E402
import xq_reverse_ledger as ledger  # noqa: E402
import xq_reverse_store as store  # noqa: E402


class FixedClock:
    """Keep every contender within the same artificial quota window."""

    def now(self) -> float:
        return 1000.0


def _gate(path: str | Path, limit: int) -> gate_module.RequestGate:
    return gate_module.RequestGate(
        path, max_requests=limit, window_seconds=60,
        min_interval_seconds=0, clock=FixedClock(),
    )


def _post(post_id: str, text: str) -> store.RawPostInput:
    return store.RawPostInput(
        post_id=post_id,
        original_url=f"https://xueqiu.com/example/{post_id}",
        created_at_ms=1700000000000,
        raw_json={"id": post_id, "text": text, "extra": {"original": True}},
    )


def _relation(post_id: str, symbol: str, text: str) -> store.PostSymbolInput:
    return store.PostSymbolInput(
        post_id=post_id, symbol=symbol, evidence_type="post_content",
        evidence_json={"source_field": "text", "excerpt": text},
    )


def _commit(
    conn: sqlite3.Connection, symbol: str, lane: str, batch: str,
    page: str, post: store.RawPostInput,
    relations: list[store.PostSymbolInput], version: int = 0,
) -> dict:
    return ledger.commit_page(
        conn, symbol=symbol, lane=lane, batch_id=batch, page_key=page,
        validated_posts=[post], relations=relations, next_cursor="next-page",
        expected_version=version, fetched_at=1700000001000,
    )


@pytest.fixture
def reverse_db(tmp_path: Path):
    """Only temporary SQLite storage; ledger DDL precedes store DDL."""
    conn = sqlite3.connect(tmp_path / "reverse.sqlite")
    ledger.init_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    store.init_schema(conn)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


class TestPostAndCheckpointAtomicity:
    def test_two_evidenced_symbols_one_original_and_only_target_lane_moves(
        self, reverse_db: sqlite3.Connection,
    ) -> None:
        text = "原帖同时提到SH600000和SZ000001，皆有证据。"
        post = _post("original-1", text)
        relations = [
            _relation(post.post_id, symbol, text)
            for symbol in ("SH600000", "SZ000001")
        ]

        result = _commit(
            reverse_db, "SH600000", "history", "batch-a", "page-a",
            post, relations,
        )

        assert result["version"] == 1
        assert result["state"] == "confirmed"
        assert result["next_cursor"] == "next-page"
        assert result["first_status_id"] == post.post_id
        assert ledger.get_checkpoint(reverse_db, "SH600000", "new")["version"] == 0
        assert ledger.get_checkpoint(reverse_db, "SZ000001", "history")["version"] == 0
        raw = reverse_db.execute(
            "SELECT raw_json FROM xq_reverse_raw_post WHERE post_id=?",
            (post.post_id,),
        ).fetchone()
        assert json.loads(raw[0]) == post.raw_json
        rows = reverse_db.execute(
            "SELECT symbol, evidence_json FROM xq_reverse_post_symbol "
            "WHERE post_id=? ORDER BY symbol", (post.post_id,),
        ).fetchall()
        assert [row[0] for row in rows] == ["SH600000", "SZ000001"]
        assert all(json.loads(row[1])["excerpt"] == text for row in rows)
        assert reverse_db.execute(
            "SELECT COUNT(*) FROM xq_reverse_page_receipt"
        ).fetchone()[0] == 1

    def test_failed_receipt_insert_rolls_back_post_relation_and_checkpoint(
        self, reverse_db: sqlite3.Connection,
    ) -> None:
        reverse_db.execute(
            "CREATE TRIGGER reject_page BEFORE INSERT ON xq_reverse_page_receipt "
            "BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END"
        )
        reverse_db.commit()
        post = _post("do-not-save", "SH600000原帖")

        with pytest.raises(sqlite3.IntegrityError, match="injected receipt failure"):
            _commit(
                reverse_db, "SH600000", "new", "failure-batch", "failure-page",
                post, [_relation(post.post_id, "SH600000", "SH600000原帖")],
            )

        for table in (
            "xq_reverse_raw_post", "xq_reverse_raw_post_version",
            "xq_reverse_post_symbol", "xq_reverse_page_receipt",
            "xq_reverse_page_member", "xq_reverse_checkpoint",
        ):
            assert reverse_db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert ledger.get_checkpoint(reverse_db, "SH600000", "new")["version"] == 0

    def test_symbols_and_new_history_lanes_have_independent_waterlines(
        self, reverse_db: sqlite3.Connection,
    ) -> None:
        for symbol, lane, post_id in (
            ("SH600000", "new", "first-new"),
            ("SH600000", "history", "first-history"),
            ("SZ000001", "new", "other-new"),
        ):
            text = f"正文确实提到{symbol}"
            _commit(
                reverse_db, symbol, lane, f"batch-{post_id}",
                f"page-{post_id}", _post(post_id, text),
                [_relation(post_id, symbol, text)],
            )

        for symbol, lane, page in (
            ("SH600000", "new", "page-first-new"),
            ("SH600000", "history", "page-first-history"),
            ("SZ000001", "new", "page-other-new"),
        ):
            checkpoint = ledger.get_checkpoint(reverse_db, symbol, lane)
            assert checkpoint["version"] == 1
            assert checkpoint["confirmed_page_key"] == page
        assert ledger.get_checkpoint(reverse_db, "SZ000001", "history")["version"] == 0
        assert reverse_db.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post"
        ).fetchone()[0] == 3

    def test_cross_batch_original_and_evidence_remain_joined_by_batch(
        self, reverse_db: sqlite3.Connection,
    ) -> None:
        """A later original must not rewrite an earlier batch's source evidence."""
        old_text = "旧批原帖提到SH600000"
        new_text = "新批原帖再次提到SH600000"
        old_post = _post("shared-original", old_text)
        new_post = _post("shared-original", new_text)

        first = _commit(
            reverse_db, "SH600000", "new", "batch-a", "first-page",
            old_post, [_relation(old_post.post_id, "SH600000", old_text)],
        )
        second = _commit(
            reverse_db, "SH600000", "new", "batch-b", "fresh-page",
            new_post, [_relation(new_post.post_id, "SH600000", new_text)],
            version=first["version"],
        )
        replay = _commit(
            reverse_db, "SH600000", "new", "batch-a", "first-page",
            old_post, [_relation(old_post.post_id, "SH600000", old_text)],
        )

        assert second["version"] == 2
        assert replay["replayed"] is True
        assert ledger.get_checkpoint(reverse_db, "SH600000", "new")["version"] == 2
        rows = reverse_db.execute(
            "SELECT v.batch_id, v.raw_json, s.evidence_json "
            "FROM xq_reverse_raw_post_version AS v "
            "JOIN xq_reverse_post_symbol AS s "
            "ON s.post_id = v.post_id AND s.batch_id = v.batch_id "
            "WHERE v.post_id = ? ORDER BY v.batch_id",
            (old_post.post_id,),
        ).fetchall()
        assert [
            (batch, json.loads(raw)["text"], json.loads(evidence)["excerpt"])
            for batch, raw, evidence in rows
        ] == [
            ("batch-a", old_text, old_text),
            ("batch-b", new_text, new_text),
        ]
        assert reverse_db.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post WHERE post_id = ?",
            (old_post.post_id,),
        ).fetchone()[0] == 1

    def test_transit_target_transit_scan_preserves_target_evidence_waterline(
        self, reverse_db: sqlite3.Connection,
    ) -> None:
        """Offline originals model complete pages bound to the target query.

        The synthetic fixture does not validate a real endpoint or page identity.
        """
        target = "SH600000"
        first_transit_post = _post("transit-first", "原帖只谈SZ000001")
        first_transit = ledger.commit_page(
            reverse_db, target, "history", "batch-a", "first-page",
            [first_transit_post], [], "second-page", 0, 1700000001000,
        )
        assert first_transit["version"] == 1
        assert first_transit["confirmed_page_key"] == "first-page"
        assert first_transit["next_cursor"] == "second-page"
        assert first_transit["first_status_id"] is None
        assert first_transit["last_status_id"] is None
        assert first_transit["coverage_started_at"] is None
        assert first_transit["coverage_updated_at"] is None

        target_post = _post("target-post", "原帖提到SH600000")
        covered = ledger.commit_page(
            reverse_db, target, "history", "batch-a", "second-page",
            [target_post],
            [_relation(target_post.post_id, target, "原帖提到SH600000")],
            "third-page", first_transit["version"], 1700000002000,
        )
        assert covered["version"] == 2
        assert covered["confirmed_page_key"] == "second-page"
        assert covered["next_cursor"] == "third-page"
        assert covered["first_status_id"] == target_post.post_id
        assert covered["last_status_id"] == target_post.post_id
        assert covered["coverage_started_at"] == 1700000002000
        assert covered["coverage_updated_at"] == 1700000002000

        last_transit_post = _post("transit-last", "原帖仍只谈SZ000001")
        last_transit = ledger.commit_page(
            reverse_db, target, "history", "batch-a", "third-page",
            [last_transit_post], [], "fourth-page", covered["version"],
            1700000003000,
        )
        assert last_transit["version"] == 3
        assert last_transit["confirmed_page_key"] == "third-page"
        assert last_transit["next_cursor"] == "fourth-page"
        assert last_transit["fetched_at"] == 1700000003000
        assert last_transit["first_status_id"] == target_post.post_id
        assert last_transit["last_status_id"] == target_post.post_id
        assert last_transit["coverage_started_at"] == 1700000002000
        assert last_transit["coverage_updated_at"] == 1700000002000
        assert reverse_db.execute(
            "SELECT page_key, first_status_id, last_status_id "
            "FROM xq_reverse_page_receipt ORDER BY confirmed_version"
        ).fetchall() == [
            ("first-page", first_transit_post.post_id, first_transit_post.post_id),
            ("second-page", target_post.post_id, target_post.post_id),
            ("third-page", last_transit_post.post_id, last_transit_post.post_id),
        ]
        assert reverse_db.execute(
            "SELECT post_id, symbol FROM xq_reverse_post_symbol"
        ).fetchall() == [(target_post.post_id, target)]


class TestRequestGate:
    def test_classified_403_stops_round_and_prevents_any_later_send(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "forbidden.sqlite"
        gate = _gate(path, 3)
        sent: list[str] = []
        classified: list[int] = []

        def send_once() -> dict[str, int]:
            sent.append("physical-request")
            return {"status": 403}

        def classify(response: dict[str, int]) -> int:
            classified.append(response["status"])
            return response["status"]

        assert gate.execute_once(send_once, status_code=classify) == {"status": 403}
        assert classified == [403]
        assert gate.snapshot().stopped is True
        assert gate.snapshot().stop_reason == "403"
        reopened = _gate(path, 3)
        with pytest.raises(gate_module.GateDenied, match="stopped"):
            reopened.execute_once(send_once, status_code=classify)
        assert sent == ["physical-request"]
        assert classified == [403]
        assert reopened.snapshot().issued_total == 1
        assert reopened.snapshot().dispatched_total == 1

    def test_failed_response_classification_stops_reopened_gate_before_send(
        self, tmp_path: Path,
    ) -> None:
        """An unclassifiable response consumes a permit and denies later sends."""
        path = tmp_path / "classification.sqlite"
        gate = _gate(path, 3)
        sends: list[str] = []

        def send_once() -> dict[str, int]:
            sends.append("physical-request")
            return {"status": 200}

        def fail_classification(response: dict[str, int]) -> str:
            raise LookupError("offline response classification failed")

        with pytest.raises(LookupError, match="classification failed"):
            gate.execute_once(
                send_once, status_code=lambda response: response["status"],
                failure_reason=fail_classification,
            )

        reopened = _gate(path, 3)
        snapshot = reopened.snapshot()
        assert snapshot.stopped is True
        assert snapshot.stop_reason == "response_classification_failed"
        assert (snapshot.issued_total, snapshot.dispatched_total,
                snapshot.failure_total) == (1, 1, 1)
        with pytest.raises(gate_module.GateDenied, match="stopped"):
            reopened.execute_once(
                send_once, status_code=lambda response: response["status"],
            )
        assert sends == ["physical-request"]

    def test_failure_record_persistence_keeps_inflight_reopened_gate_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed failure-audit write must not clear a dispatched request."""
        path = tmp_path / "classification-audit-failed.sqlite"
        gate = _gate(path, 3)
        sends: list[int] = []

        def send_once() -> int:
            sends.append(1)
            return 200

        def fail_record(reason: str, permit: gate_module.Permit) -> None:
            raise sqlite3.OperationalError("injected failure audit error")

        monkeypatch.setattr(gate, "_record_failure", fail_record)
        with pytest.raises(sqlite3.OperationalError, match="failure audit error"):
            gate.execute_once(
                send_once, status_code=lambda response: response,
                failure_reason=lambda response: "token_invalid",
            )

        reopened = _gate(path, 3)
        snapshot = reopened.snapshot()
        assert (snapshot.issued_total, snapshot.dispatched_total,
                snapshot.failure_total) == (1, 1, 0)
        with pytest.raises(gate_module.GateDenied, match="unresolved"):
            reopened.execute_once(
                send_once, status_code=lambda response: response,
            )
        assert sends == [1]

    def test_thread_contenders_do_not_exceed_shared_budget(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "threads.sqlite"
        gates = [_gate(path, 4) for _ in range(3)]

        def contend(index: int) -> str:
            try:
                gates[index % len(gates)].execute_once(
                    lambda: 200, status_code=lambda response: response,
                )
                return "sent"
            except gate_module.GateDenied:
                return "denied"

        with ThreadPoolExecutor(max_workers=10) as executor:
            outcomes = list(executor.map(contend, range(15)))
        assert outcomes.count("sent") == 4
        assert outcomes.count("denied") == 11
        assert gates[0].snapshot().dispatched_total == 4

    def test_process_contenders_do_not_exceed_shared_budget(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "processes.sqlite"
        gate = _gate(path, 2)
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        result = context.Queue()
        processes = [
            context.Process(target=_process_contender, args=(str(path), start, result))
            for _ in range(5)
        ]
        try:
            for process in processes:
                process.start()
            start.set()
            outcomes = [result.get(timeout=20) for _ in processes]
        finally:
            start.set()
            for process in processes:
                process.join(timeout=20)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            result.close()
        assert all(process.exitcode == 0 for process in processes)
        assert outcomes.count("sent") == 2, outcomes
        assert outcomes.count("denied") == 3, outcomes
        assert gate.snapshot().issued_total == 2
        assert gate.snapshot().dispatched_total == 2


def _process_contender(path: str, start, result) -> None:
    """Top-level callable permits independent Windows spawned workers."""
    try:
        gate = _gate(path, 2)
        if not start.wait(timeout=15):
            result.put("start-timeout")
            return
        gate.execute_once(lambda: 200, status_code=lambda response: response)
        result.put("sent")
    except gate_module.GateDenied:
        result.put("denied")
    except BaseException as error:
        result.put(f"error:{type(error).__name__}:{error}")
