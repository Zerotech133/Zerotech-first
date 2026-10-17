"""Contract tests for task 61 video knowledge content and evidence semantics.

These tests intentionally exercise the public/read-model contract rather than
PostgreSQL.  The repository is mocked so the suite is deterministic and does
not require a running database.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from knowledge import repository
from store.models import KnowledgeUnit, StrategyEvidence


@pytest.fixture
def unit_row() -> dict:
    return {
        "unit_id": 1,
        "unit_key": "unit-contract",
        "unit_version": 3,
        "unit_type": "proposition",
        "content": "待核实命题",
        "source_channel": "asr",
        "source_video_id": "video-contract",
        "source_url": "https://example.test/video",
        "source_link": "https://example.test/video",
        "start_sec": 10,
        "end_sec": 30,
        "resource_key": None,
        "review_status": "reviewed",
        "status": "published",
        "truth_status": "contested",
        "extension": {"implications": ["needs corroboration"]},
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }


def evidence_row(*, key: str, role: str = "context", **overrides: object) -> dict:
    row = {
        "evidence_id": 1,
        "strategy_key": "strategy-contract",
        "knowledge_unit_key": "unit-contract",
        "knowledge_unit_version": 3,
        "unit_version": 3,
        "evidence_key": key,
        "evidence_role": role,
        "quote": "quoted source text",
        "relevance": 0.8,
        "confidence": 0.8,
        "quote_start_sec": 12,
        "quote_end_sec": 20,
        "evidence_status": "valid",
        "snapshot_hash": "sha256:snapshot",
        "metadata": {},
        "resource_key": None,
        "source_channel": "asr",
        "start_sec": 10,
        "end_sec": 30,
        "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("unit_type", ["proposition", "extension", "judgment"])
def test_video_knowledge_content_types_are_preserved(unit_type: str) -> None:
    """The three task-61 content kinds must survive model construction."""
    unit = KnowledgeUnit(unit_key=f"unit-{unit_type}", unit_type=unit_type)
    assert unit.unit_type == unit_type


@pytest.mark.parametrize("truth_status", ["unknown", "correct", "incorrect", "contested"])
def test_judgment_truth_status_is_explicit(truth_status: str) -> None:
    unit = KnowledgeUnit(unit_key=f"unit-{truth_status}", truth_status=truth_status)
    assert unit.truth_status == truth_status


@pytest.mark.parametrize(
    "status",
    ["supported", "refuted", "contested", "uncertain", "unverified"],
)
def test_judgment_projection_preserves_five_review_states(unit_row: dict, status: str) -> None:
    """Judgment is explicit and never inferred from proposition/source text."""
    projected = repository._unit_content_fields(
        {
            **unit_row,
            "facts": {
                "proposition": "市场将继续扩张",
                "proposition_type": "proposition",
                "source_content": "视频中的原始转写",
                "extension": {"implications": ["需要外部验证"]},
                "judgment": {"status": status, "reasoning": "人工审核", "confidence": 0.7},
            },
        }
    )
    assert projected["proposition"] == "市场将继续扩张"
    assert projected["proposition_type"] == "proposition"
    assert projected["source_content"] == "视频中的原始转写"
    assert projected["extension"]["implications"] == ["需要外部验证"]
    assert projected["judgment"]["status"] == status
    assert projected["judgment"]["confidence"] == 0.7


def test_nested_sensitive_metadata_never_enters_evidence_dto(unit_row: dict) -> None:
    """Secrets in arbitrarily nested metadata must not cross the read boundary."""
    secret = {
        "temporary_media_path": "D:/private/video.mp4",
        "token": "bearer-secret",
        "nested": {"cookie": "session-secret", "raw_path": "/mnt/raw.mp4"},
    }
    dto = repository._evidence_dto(
        evidence_row(key="ev-secret", role="supporting", metadata=secret),
        unit_row,
    )
    assert "metadata" not in dto
    serialized = repr(dto)
    assert all(value not in serialized for value in ("video.mp4", "bearer-secret", "session-secret", "raw.mp4"))


def test_source_content_alone_does_not_prove_a_fact(unit_row: dict) -> None:
    """Raw source content is context; validity still requires quote and snapshot."""
    dto = repository._evidence_dto(
        evidence_row(
            key="ev-source-only",
            role="context",
            quote="",
            snapshot_hash=None,
            metadata={"source_content": "未经核验的原始转写"},
        ),
        unit_row,
    )
    assert dto["validity"]["is_valid"] is False
    assert dto["validity"]["status"] == "valid"


def test_evidence_relation_counts_supports_contradicts_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """A strategy chain aggregates each supported evidence relation separately."""
    rows = [
        evidence_row(key="ev-support", role="supports"),
        evidence_row(key="ev-contradict", role="contradicts"),
        evidence_row(key="ev-context", role="context"),
    ]

    def fetch_all(sql: str, params: dict) -> list[dict]:
        if "FROM osint_strategy_evidence" in sql:
            return rows
        if "FROM osint_knowledge_resource" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(repository.db, "fetch_all", fetch_all)
    chain = repository.get_strategy_evidence_chain("strategy-contract")
    assert chain is not None
    assert chain["counts"] == {"supports": 1, "contradicts": 1, "context": 1}
    assert {item["evidence_role"] for item in chain["evidence"]} == {
        "supports",
        "contradicts",
        "context",
    }


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [(9, 20, False), (12, 31, False), (12, 20, True), (20, 20, False)],
)
def test_timecode_must_fit_unit_bounds(
    unit_row: dict, start: int, end: int, expected: bool
) -> None:
    dto = repository._evidence_dto(
        evidence_row(key=f"ev-{start}-{end}", quote_start_sec=start, quote_end_sec=end),
        unit_row,
    )
    assert dto["validity"]["time_valid"] is expected
    assert dto["validity"]["is_valid"] is expected


def test_version_mismatch_is_not_valid_even_with_valid_quote(unit_row: dict) -> None:
    dto = repository._evidence_dto(
        evidence_row(key="ev-old", knowledge_unit_version=2),
        unit_row,
    )
    assert dto["validity"]["version_match"] is False
    assert dto["validity"]["is_valid"] is False


def test_strategy_evidence_model_rejects_unknown_relation() -> None:
    with pytest.raises(ValueError):
        StrategyEvidence("strategy", "unit", "ev", evidence_role="unrelated")
