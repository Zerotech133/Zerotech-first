"""EvidenceChain contract and sanitization tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from knowledge import repository
from queue_link.api import app


@pytest.fixture
def unit_row() -> dict:
    return {
        "unit_id": 1,
        "unit_key": "unit-a",
        "unit_version": 2,
        "unit_type": "fact",
        "title": "title",
        "content": "content",
        "source_channel": "asr",
        "source_video_id": "video-a",
        "source_url": "https://example.test/video-a",
        "source_link": "https://example.test/video-a",
        "start_sec": 10,
        "end_sec": 30,
        "content_hash": "abc",
        "review_status": "reviewed",
        "blogger_key": "blogger-a",
        "resource_key": "resource-a",
        "evidence_refs": [],
        "quality": {},
        "status": "published",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }


def _evidence(**overrides: object) -> dict:
    value = {
        "evidence_id": 10,
        "strategy_key": "strategy-a",
        "knowledge_unit_key": "unit-a",
        "knowledge_unit_version": 2,
        "evidence_key": "evidence-a",
        "evidence_role": "supports",
        "quote": "quoted text",
        "relevance": 0.9,
        "confidence": 0.8,
        "quote_start_sec": 12,
        "quote_end_sec": 20,
        "evidence_status": "valid",
        "snapshot_hash": "sha256:abc",
        "metadata": {"reliability": 0.95, "raw_path": "C:\\private\\secret.mp4"},
        "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
    }
    value.update(overrides)
    return value


def test_evidence_dto_validates_version_time_and_allowlist(unit_row: dict) -> None:
    dto = repository._evidence_dto(
        {
            **_evidence(),
            "resource": {
                "resource_key": "resource-a",
                "source_url": "https://example.test",
            },
        },
        unit_row,
    )
    assert dto["validity"] == {
        "status": "valid",
        "is_valid": True,
        "time_valid": True,
        "version_match": True,
    }
    assert "metadata" not in dto
    assert "raw_path" not in dto
    assert dto["source_resource"]["source_url"] == "https://example.test"


def test_evidence_dto_flags_out_of_bounds_and_version_mismatch(unit_row: dict) -> None:
    dto = repository._evidence_dto(
        {
            **_evidence(quote_start_sec=29, quote_end_sec=31, knowledge_unit_version=1),
            "resource": None,
        },
        unit_row,
    )
    assert dto["validity"]["is_valid"] is False
    assert dto["validity"]["time_valid"] is False
    assert dto["validity"]["version_match"] is False


@pytest.mark.parametrize("field", ["quote", "snapshot_hash"])
def test_evidence_dto_requires_quote_and_snapshot_hash(
    unit_row: dict, field: str
) -> None:
    dto = repository._evidence_dto(
        {**_evidence(**{field: None}), "resource": None}, unit_row
    )
    assert dto["validity"]["is_valid"] is False


def test_knowledge_detail_joins_exact_version_and_returns_envelope(
    monkeypatch: pytest.MonkeyPatch, unit_row: dict
) -> None:
    evidence = _evidence()

    def fetch_all(sql: str, params: dict) -> list[dict]:
        if "FROM osint_knowledge_unit" in sql:
            return [unit_row]
        if "FROM osint_knowledge_resource" in sql:
            return [
                {
                    "resource_key": "resource-a",
                    "source_url": "https://example.test",
                    "duration_sec": 60,
                }
            ]
        if "FROM osint_strategy_evidence" in sql:
            return [evidence]
        if "FROM osint_index_task" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(repository.db, "fetch_all", fetch_all)
    client = TestClient(app)
    response = client.get("/api/v1/knowledge/units/unit-a?version=2")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "data", "message"}
    assert body["code"] == 0
    assert body["data"]["current_version"] == 2
    assert body["data"]["versions"][0]["evidence_refs"][0]["unit_version"] == 2
    assert "raw_path" not in str(body)


def test_strategy_chain_filters_requested_unit_version(
    monkeypatch: pytest.MonkeyPatch, unit_row: dict
) -> None:
    seen: list[dict] = []

    def fetch_all(sql: str, params: dict) -> list[dict]:
        seen.append(params)
        if "FROM osint_strategy_evidence" in sql:
            return [
                {
                    **_evidence(),
                    "resource_key": None,
                    "unit_version": 2,
                    "source_channel": "asr",
                    "start_sec": 10,
                    "end_sec": 30,
                }
            ]
        if "FROM osint_knowledge_resource" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(repository.db, "fetch_all", fetch_all)
    result = repository.get_strategy_evidence_chain("strategy-a", version=2)
    assert result is not None
    assert result["evidence"][0]["unit_version"] == 2
    assert any(params.get("unit_version") == 2 for params in seen)
