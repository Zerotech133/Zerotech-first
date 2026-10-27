"""K-MDC-R03 (任峻位): evidence persistence store — four-state ledger with replay.

Synthetic-only harness: durability via a temp-directory ledger file, atomic
os.replace writes. No network, no real sources, no production coupling.

States: offered -> accepted -> persisted -> visible.
Idempotency key carries full provenance (source_ref/receipt_id) per R02FIX
PROVENANCE_CONFLICT semantics. Watermark only advances over a contiguous run
of persisted-or-visible records — an unconfirmed hole blocks advancement.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

OFFERED, ACCEPTED, PERSISTED, VISIBLE = "offered", "accepted", "persisted", "visible"
_ORDER = {OFFERED: 0, ACCEPTED: 1, PERSISTED: 2, VISIBLE: 3}
_LEDGER = "ledger.json"


class StoreRejected(RuntimeError):
    """Fail-closed store rejection."""


@dataclass(frozen=True)
class EvidenceItem:
    source_id: str
    content_type: str
    source_record_id: str
    content_hash: str
    source_ref: str
    receipt_id: str

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "content_type",
            "source_record_id",
            "content_hash",
            "source_ref",
            "receipt_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise StoreRejected(f"{name}: nonempty provenance string required")

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.source_id,
            self.content_type,
            self.source_record_id,
            self.content_hash,
            self.source_ref,
            self.receipt_id,
        )

    @property
    def key_json(self) -> str:
        return json.dumps(self.key, ensure_ascii=False, sort_keys=True)

    @property
    def content_key(self) -> tuple[str, str, str, str]:
        return (
            self.source_id,
            self.content_type,
            self.source_record_id,
            self.content_hash,
        )


class EvidenceStore:
    """File-backed four-state ledger. One writer per directory (任峻位单写者)."""

    def __init__(self, root: str | Path, *, capacity: int = 8) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise StoreRejected("capacity: positive integer required")
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._capacity = capacity
        self._inflight: dict[str, dict] = {}  # key_json -> record (in-memory tail)
        self._content_index: dict[
            tuple, str
        ] = {}  # content_key -> key_json (provenance guard)
        self._records: dict[str, dict] = {}  # key_json -> record (all epochs)
        self._next_seq = 1
        self.drop_count = 0
        self._load()

    # -- persistence ---------------------------------------------------------

    def _path(self) -> Path:
        return self._root / _LEDGER

    def _load(self) -> None:
        path = self._path()
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self._records = {k: dict(v) for k, v in data.get("records", {}).items()}
        self._next_seq = int(data.get("next_seq", 1))
        self.drop_count = int(data.get("drop_count", 0))
        for key_json, record in self._records.items():
            item = _item_of(record)
            self._content_index[item.content_key] = key_json
            if record["state"] in (OFFERED, ACCEPTED):
                self._inflight[key_json] = record

    def _flush(self) -> None:
        payload = json.dumps(
            {
                "records": self._records,
                "next_seq": self._next_seq,
                "drop_count": self.drop_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        tmp = self._path().with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path())  # atomic: readers never see a torn ledger

    # -- four-state transitions ----------------------------------------------

    def offer(self, item: EvidenceItem) -> dict:
        if not isinstance(item, EvidenceItem):
            raise StoreRejected("EvidenceItem required")
        existing = self._records.get(item.key_json)
        if existing is not None:
            return dict(existing)  # idempotent re-offer returns the same record
        drifted = self._content_index.get(item.content_key)
        if drifted is not None:
            raise StoreRejected(
                "PROVENANCE_CONFLICT: same content with changed source_ref/receipt_id "
                "requires explicit versioning decision, not silent reuse"
            )
        if len(self._inflight) >= self._capacity:
            self.drop_count += 1
            self._flush()
            raise StoreRejected("OFFER_BUFFER_FULL: inflight buffer at capacity")
        record = {
            "item": list(item.key),
            "seq": self._next_seq,
            "state": OFFERED,
            "visibility": {},
        }
        self._next_seq += 1
        self._records[item.key_json] = record
        self._content_index[item.content_key] = item.key_json
        self._inflight[item.key_json] = record
        self._flush()
        return dict(record)

    def _record_of(self, key_json: str) -> dict:
        record = self._records.get(key_json)
        if record is None:
            raise StoreRejected("EVIDENCE_UNKNOWN")
        return record

    def _advance(self, item: EvidenceItem, target: str) -> dict:
        record = self._record_of(item.key_json)
        if _ORDER[record["state"]] > _ORDER[target]:
            raise StoreRejected(
                f"STATE_INVALID: {record['state']} cannot move back to {target}"
            )
        if _ORDER[record["state"]] < _ORDER[target] - 1:
            raise StoreRejected(
                f"STATE_INVALID: cannot jump {record['state']} -> {target}"
            )
        record["state"] = target
        if target in (PERSISTED, VISIBLE):
            self._inflight.pop(item.key_json, None)
        self._flush()
        return dict(record)

    def accept(self, item: EvidenceItem) -> dict:
        return self._advance(item, ACCEPTED)

    def persist(
        self, item: EvidenceItem, *, after_write: Callable[[], None] | None = None
    ) -> dict:
        """Persist accepted item; ledger is durably written BEFORE after_write runs.

        If after_write raises, the record is already persisted — replay keeps it
        persisted (write-after-persist crash must not duplicate or roll back).
        """
        record = self._advance(item, PERSISTED)
        if after_write is not None:
            after_write()
        return record

    def mark_visible(self, item: EvidenceItem, consumer_id: str, receipt: str) -> dict:
        if not isinstance(consumer_id, str) or not consumer_id.strip():
            raise StoreRejected("consumer_id required")
        if not isinstance(receipt, str) or not receipt.strip():
            raise StoreRejected("VISIBILITY_RECEIPT_REQUIRED")
        record = self._record_of(item.key_json)
        # Reject before mutating the receipt map: _advance would otherwise
        # reject offered/accepted -> visible *after* recording a false receipt.
        if record["state"] not in (PERSISTED, VISIBLE):
            raise StoreRejected(
                f"STATE_INVALID: cannot jump {record['state']} -> {VISIBLE}"
            )
        record["visibility"][consumer_id] = receipt
        return self._advance(item, VISIBLE)

    # -- queries ----------------------------------------------------------------

    def state_of(self, item: EvidenceItem) -> str:
        return self._record_of(item.key_json)["state"]

    def counts(self) -> dict:
        out = {OFFERED: 0, ACCEPTED: 0, PERSISTED: 0, VISIBLE: 0}
        for record in self._records.values():
            out[record["state"]] += 1
        return out

    def high_watermark(self) -> int:
        """Highest contiguous seq whose record is persisted or visible; 0 if none.

        A hole (offered/accepted between persisted records) freezes the mark.
        """
        by_seq = sorted((r["seq"], r["state"]) for r in self._records.values())
        mark = 0
        for seq, state in by_seq:
            if state in (PERSISTED, VISIBLE):
                mark = seq
            else:
                break
        return mark

    def replay(self) -> "EvidenceStore":
        """Reload the ledger from disk (crash-restart simulation)."""
        return EvidenceStore(self._root, capacity=self._capacity)


def _item_of(record: dict) -> EvidenceItem:
    return EvidenceItem(*record["item"])
