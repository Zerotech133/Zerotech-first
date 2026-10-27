# -*- coding: utf-8 -*-
"""WO-FS-157: all effects isolated to system-temp synthetic tickets."""

import hashlib
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.inbox_offline import ContractError, OfflineInbox  # noqa: E402


def _evidence(
    box,
    stamp,
    *,
    ticket="lg-synthetic1",
    content="合成情报：无真实行情数据；状态待核",
    **updates,
):
    doc = {
        "ticket": ticket,
        "source_at": stamp,
        "verification": "verified",
        "authority": "intel",
        "scope": "synthetic",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    doc.update(updates)
    path = box.evidence / "intel_id.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def setup(tmp_path_factory):
    # tmp_path_factory defaults to OS temp on this runner; explicit prefix is required.
    with tempfile.TemporaryDirectory(prefix="wo_fs_157_") as dirname:
        root = Path(dirname)
        (root / "inbox").mkdir()
        (root / "evidence").mkdir()
        ticket = "lg-synthetic1"
        record = {
            "id": ticket,
            "state": "inbox",
            "source": "feishu_comm",
            "kind": "task_request",
            "payload": {"intent": "stock_consult"},
        }
        src = root / "inbox" / (ticket + ".json")
        src.write_text(json.dumps(record), encoding="utf-8")
        original = src.read_bytes()
        yield OfflineInbox(root, {"A0", "D1", "QA1"}), ticket, src, original
        assert src.read_bytes() == original


def test_explicit_temp_boundary_and_source_validation(setup):
    box, ticket, src, _ = setup
    with pytest.raises(ContractError):
        OfflineInbox(Path.cwd(), {"A0"})
    assert box.inspect(ticket)["state"] == "pending"
    with pytest.raises(ContractError):
        box.claim("lg-../escape", "A0", "k1")
    with pytest.raises(ContractError):
        box.claim(ticket, "UNKNOWN", "k1")
    assert not list(box.receipts.glob("*.json"))
    src.write_text("{bad", encoding="utf-8")
    with pytest.raises(ContractError, match="unreadable source"):
        box.inspect(ticket)
    src.write_bytes(setup[3])


def test_roundtrip_evidence_and_idempotency(setup):
    box, ticket, _, _ = setup
    claim = box.claim(ticket, "D1", "claim1")
    assert claim["state"] == "claimed" and claim["delivery"] == "not_attempted"
    assert box.claim(ticket, "D1", "claim1") == claim
    with pytest.raises(ContractError, match="already claimed"):
        box.claim(ticket, "A0", "claim2")
    stamp = datetime.now(timezone.utc).isoformat()
    _evidence(box, stamp)
    with pytest.raises(ContractError, match="independent"):
        box.answer(
            ticket,
            "D1",
            "answer1",
            text="待核",
            source_ref="intel_id.json",
            source_at=stamp,
            reviewer="D1",
            review_status="approved",
        )
    with pytest.raises(ContractError):
        box.answer(
            ticket,
            "D1",
            "answer1",
            text="待核",
            source_ref="",
            source_at=stamp,
            reviewer="QA1",
            review_status="approved",
        )
    answered = box.answer(
        ticket,
        "D1",
        "answer1",
        text="来源不足，待核",
        source_ref="intel_id.json",
        source_at=stamp,
        reviewer="QA1",
        review_status="approved",
    )
    assert answered["state"] == "answered" and answered["reviewer"] == "QA1"
    assert (
        answered["evidence_sha256"]
        == hashlib.sha256((box.evidence / "intel_id.json").read_bytes()).hexdigest()
    )
    assert (
        box.answer(
            ticket,
            "D1",
            "answer1",
            text="来源不足，待核",
            source_ref="intel_id.json",
            source_at=stamp,
            reviewer="QA1",
            review_status="approved",
        )
        == answered
    )
    with pytest.raises(ContractError, match="conflicting answer replay"):
        box.answer(
            ticket,
            "D1",
            "answer1",
            text="私自换结论",
            source_ref="intel_id.json",
            source_at=stamp,
            reviewer="QA1",
            review_status="approved",
        )
    with pytest.raises(ContractError, match="message_id"):
        box.record_delivery(ticket, "send1", accepted=True)
    failed = box.record_delivery(
        ticket, "send1", accepted=False, why="stub network down"
    )
    assert failed["state"] == "answered" and failed["delivery"] == "failed"
    accepted = box.record_delivery(
        ticket, "send2", accepted=True, message_id="om_stub", why="api accepted"
    )
    assert accepted["delivery"] == "accepted" and accepted["message_id"] == "om_stub"
    assert (
        box.record_delivery(
            ticket, "send2", accepted=True, message_id="om_stub", why="api accepted"
        )
        == accepted
    )
    with pytest.raises(ContractError, match="conflicting delivery replay"):
        box.record_delivery(ticket, "send2", accepted=False, why="contradiction")
    with pytest.raises(ContractError, match="already accepted"):
        box.record_delivery(ticket, "send3", accepted=True, message_id="om_duplicate")
    assert box.inspect(ticket)["state"] == "answered"
    assert not (box.root / "processed" / (ticket + ".json")).exists()


def test_concurrent_claim_only_one_owner(setup):
    box, ticket, _, _ = setup

    def call(actor):
        try:
            return box.claim(ticket, actor, "claim-" + actor)
        except ContractError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(call, ("A0", "D1")))
    assert sum(isinstance(v, dict) for v in values) == 1
    assert sum(isinstance(v, str) for v in values) == 1
    assert box.inspect(ticket)["actor"] in ("A0", "D1")


def test_bad_record_and_missing_evidence_fail_closed(setup):
    box, ticket, _, _ = setup
    with pytest.raises(ContractError):
        box.claim(ticket, None, "k1")
    box.claim(ticket, "D1", "k1")
    with pytest.raises(ContractError, match="source_at"):
        box.answer(
            ticket,
            "D1",
            "a1",
            text="待核",
            source_ref="intel_id.json",
            source_at="2026-10-21",
            reviewer="QA1",
            review_status="approved",
        )
    with pytest.raises(ContractError, match="approval"):
        box.answer(
            ticket,
            "D1",
            "a1",
            text="待核",
            source_ref="intel_id.json",
            source_at=datetime.now(timezone.utc).isoformat(),
            reviewer="QA1",
            review_status="pending",
        )
    with pytest.raises(ContractError, match="evidence"):
        box.answer(
            ticket,
            "D1",
            "a1",
            text="待核",
            source_ref="intel_id.json",
            source_at=datetime.now(timezone.utc).isoformat(),
            reviewer="QA1",
            review_status="approved",
        )
    assert box.inspect(ticket)["state"] == "claimed"
    with pytest.raises(ContractError):
        box.record_delivery(ticket, "send1", accepted=False, why="offline")
    (box.receipts / (ticket + ".json")).write_text("{bad", encoding="utf-8")
    with pytest.raises(ContractError, match="unreadable receipt"):
        box.inspect(ticket)


def test_unverified_tampered_and_stale_evidence_rejected(setup):
    from datetime import timedelta

    box, ticket, _, _ = setup
    box.claim(ticket, "D1", "claim1")
    stamp = datetime.now(timezone.utc).isoformat()
    kw = {
        "text": "无数据，仅待核",
        "source_ref": "intel_id.json",
        "source_at": stamp,
        "reviewer": "QA1",
        "review_status": "approved",
    }
    path = _evidence(box, stamp, verification="pending")
    with pytest.raises(ContractError, match="not verified"):
        box.answer(ticket, "D1", "a1", **kw)
    _evidence(box, stamp, content_sha256="0" * 64)
    with pytest.raises(ContractError, match="hash mismatch"):
        box.answer(ticket, "D1", "a1", **kw)
    _evidence(box, stamp, authority="other")
    with pytest.raises(ContractError, match="not verified"):
        box.answer(ticket, "D1", "a1", **kw)
    _evidence(box, stamp + "mismatch")
    with pytest.raises(ContractError, match="not verified"):
        box.answer(ticket, "D1", "a1", **kw)
    _evidence(box, stamp, ticket="lg-other-ticket")
    with pytest.raises(ContractError, match="not verified"):
        box.answer(ticket, "D1", "a1", **kw)
    path.unlink()
    with pytest.raises(ContractError, match="missing"):
        box.answer(ticket, "D1", "a1", **kw)
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    _evidence(box, old)
    with pytest.raises(ContractError, match="stale"):
        box.answer(ticket, "D1", "a1", **dict(kw, source_at=old))
    assert box.inspect(ticket)["state"] == "claimed"


def test_verified_evidence_cannot_be_reused_for_second_ticket(setup):
    box, ticket, _, _ = setup
    second = "lg-synthetic2"
    (box.root / "inbox" / (second + ".json")).write_text(
        json.dumps(
            {
                "id": second,
                "state": "inbox",
                "source": "feishu_comm",
                "kind": "task_request",
                "payload": {"intent": "stock_consult"},
            }
        ),
        encoding="utf-8",
    )
    box.claim(ticket, "D1", "claim1")
    box.claim(second, "D1", "claim2")
    stamp = datetime.now(timezone.utc).isoformat()
    _evidence(box, stamp, ticket=ticket)
    kw = {
        "text": "仅待核",
        "source_ref": "intel_id.json",
        "source_at": stamp,
        "reviewer": "QA1",
        "review_status": "approved",
    }
    first = box.answer(ticket, "D1", "answer1", **kw)
    assert first["state"] == "answered" and first["ticket"] == ticket
    with pytest.raises(ContractError, match="not verified"):
        box.answer(second, "D1", "answer2", **kw)
    assert box.inspect(second)["state"] == "claimed"


def test_receipt_tampering_and_evidence_change_fail_visible(setup):
    box, ticket, _, _ = setup
    box.claim(ticket, "D1", "claim1")
    stamp = datetime.now(timezone.utc).isoformat()
    path = _evidence(box, stamp)
    kw = {
        "text": "仅待核",
        "source_ref": "intel_id.json",
        "source_at": stamp,
        "reviewer": "QA1",
        "review_status": "approved",
    }
    box.answer(ticket, "D1", "a1", **kw)
    # 同一操作键不允许换掉已经审过的证据文件内容。
    _evidence(box, stamp, content="另一份合成来源")
    with pytest.raises(ContractError, match="conflicting answer replay"):
        box.answer(ticket, "D1", "a1", **kw)
    assert path.exists()
    receipt = box.receipts / (ticket + ".json")
    record = json.loads(receipt.read_text(encoding="utf-8"))
    record["review_status"] = "pending"
    receipt.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ContractError, match="invalid answered receipt"):
        box.inspect(ticket)


def test_stale_lock_is_visible_not_overwritten(setup):
    box, ticket, _, _ = setup
    (box.receipts / (ticket + ".lock")).write_text("stale", encoding="utf-8")
    with pytest.raises(ContractError, match="ticket busy"):
        box.claim(ticket, "A0", "claim1")
    assert box.inspect(ticket)["state"] == "pending"
