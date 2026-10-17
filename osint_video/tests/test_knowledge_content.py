"""Focused model checks for durable video knowledge content semantics."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from store.models import KnowledgeResource, KnowledgeUnit, StrategyEvidence
from store.service import validate_evidence_for_resource


def test_resource_keeps_transcripts_but_no_video_body() -> None:
    resource = KnowledgeResource(
        resource_key="resource-1",
        source_url="https://example.test/video/1",
        published_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        duration_sec=90,
        asr_text="原始转写",
        ocr_text="屏幕 OCR",
        subtitle_text="字幕原文",
    )
    assert resource.asr_text == "原始转写"
    assert resource.ocr_text == "屏幕 OCR"
    assert resource.subtitle_text == "字幕原文"
    assert not hasattr(resource, "video_bytes")
    assert not hasattr(resource, "video_body")


def test_unit_truth_and_extension_are_explicit() -> None:
    unit = KnowledgeUnit(
        unit_key="unit-1",
        content="待核实观点",
        source_url="https://example.test/video/1",
        truth_status="incorrect",
        extension={"implications": ["需要反例验证"]},
    )
    assert unit.truth_status == "incorrect"
    assert unit.extension["implications"]


def test_evidence_supporting_and_contradicting_roles() -> None:
    supporting = StrategyEvidence("strategy", "unit", "ev-support", evidence_role="supporting")
    contradicting = StrategyEvidence("strategy", "unit", "ev-against", evidence_role="contradicting")
    assert supporting.evidence_role == "supporting"
    assert contradicting.evidence_role == "contradicting"
    with pytest.raises(ValueError):
        StrategyEvidence("strategy", "unit", "ev-bad", evidence_role="random")


def test_service_checks_duration_and_non_finite_values() -> None:
    resource = KnowledgeResource("resource-2", duration_sec=10)
    with pytest.raises(ValueError):
        validate_evidence_for_resource(
            StrategyEvidence("s", "u", "ev-too-long", quote_start_sec=1, quote_end_sec=11), resource
        )
    with pytest.raises(ValueError):
        KnowledgeResource("resource-nan", duration_sec=math.nan)
