"""Service-level lifecycle validation for resources, evidence and link completion."""

from __future__ import annotations

import math

from store.models import KnowledgeResource, StrategyEvidence


def validate_evidence_for_resource(evidence: StrategyEvidence, resource: KnowledgeResource) -> None:
    """Validate finite, ordered quote bounds against a resource duration.

    This validation is repeated at the service boundary so callers cannot bypass
    lifecycle invariants by constructing objects through deserialization.
    """
    duration = resource.duration_sec
    if duration is not None:
        duration_value = float(duration)
        if not math.isfinite(duration_value) or duration_value < 0:
            raise ValueError("resource duration_sec must be finite and non-negative")
    else:
        duration_value = None

    start = evidence.quote_start_sec
    end = evidence.quote_end_sec
    if start is not None:
        start = float(start)
        if not math.isfinite(start) or start < 0:
            raise ValueError("quote_start_sec must be finite and non-negative")
    if end is not None:
        end = float(end)
        if not math.isfinite(end) or end < 0:
            raise ValueError("quote_end_sec must be finite and non-negative")
    if start is not None and end is not None and end <= start:
        raise ValueError("quote_end_sec must be strictly greater than quote_start_sec")
    if duration_value is not None:
        for name, value in (("quote_start_sec", start), ("quote_end_sec", end)):
            if value is not None and value > duration_value:
                raise ValueError(f"{name} must not exceed resource duration ({duration_value})")


def prepare_evidence(evidence: StrategyEvidence, resource: KnowledgeResource) -> StrategyEvidence:
    """Validate and return evidence before persistence."""
    validate_evidence_for_resource(evidence, resource)
    return evidence


__all__ = ["validate_evidence_for_resource", "prepare_evidence"]
