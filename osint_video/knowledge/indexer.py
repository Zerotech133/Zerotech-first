"""Qdrant vector index adapter for knowledge-unit retrieval.

PostgreSQL remains the authoritative source.  This adapter only writes a
sanitized, rebuildable projection and returns candidate identifiers; callers
must hydrate and validate candidates against PostgreSQL before displaying them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
except ImportError:  # pragma: no cover - dependency is optional at import time
    QdrantClient = None  # type: ignore[assignment,misc]
    models = None  # type: ignore[assignment]

from config.settings import get_settings
from store.models import KnowledgeUnit


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    """A safe candidate returned by Qdrant, not an authoritative fact."""

    unit_id: str
    score: float
    index_version: int


class VectorIndex:
    """Idempotent Qdrant index for sanitized knowledge-unit projections."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        collection: str | None = None,
        vector_size: int | None = None,
        distance: Any | None = None,
    ) -> None:
        settings = get_settings()
        if client is None:
            if QdrantClient is None:
                raise RuntimeError("qdrant-client is not installed")
            client = QdrantClient(url=settings.qdrant_url)
        self.client: Any = client
        self.collection: str = collection or settings.qdrant_collection
        self.vector_size: int = int(vector_size or settings.qdrant_vector_size)
        self.distance: Any = distance or (
            models.Distance.COSINE if models else "Cosine"
        )
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create the configured collection only when it does not exist."""
        if models is None:
            raise RuntimeError("qdrant-client is not installed")
        try:
            exists = bool(self.client.collection_exists(self.collection))
        except AttributeError:
            names = self.client.get_collections().collections
            exists = any(item.name == self.collection for item in names)
        if not exists:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.vector_size, distance=self.distance
                ),
            )

    @staticmethod
    def _unit_id(unit: KnowledgeUnit | Mapping[str, Any]) -> str:
        value = (
            unit.unit_key
            if isinstance(unit, KnowledgeUnit)
            else unit.get("unit_key", unit.get("unit_id"))
        )
        if value is None or not str(value).strip():
            raise ValueError("knowledge unit must have a stable unit_key")
        return str(value)

    @staticmethod
    def _payload(unit: KnowledgeUnit | Mapping[str, Any]) -> dict[str, Any]:
        """Build an allow-listed payload; never include raw media or credentials."""
        if isinstance(unit, KnowledgeUnit):
            value = {
                "unit_id": str(unit.unit_id or unit.unit_key),
                "unit_key": unit.unit_key,
                "resource_key": unit.resource_key,
                "blogger_key": unit.blogger_key,
                "topics": list(unit.topics),
                "status": unit.status,
                "review_status": unit.review_status,
                "truth_status": unit.truth_status,
                "content_hash": unit.content_hash,
                "summary": (unit.title or unit.content[:500]),
                "index_version": int(unit.unit_version),
            }
        else:
            value = {
                "unit_id": str(unit.get("unit_id") or unit.get("unit_key")),
                "unit_key": str(unit.get("unit_key") or unit.get("unit_id")),
                "resource_key": unit.get("resource_key"),
                "blogger_key": unit.get("blogger_key"),
                "topics": list(unit.get("topics") or []),
                "status": unit.get("status", "active"),
                "review_status": unit.get("review_status", "unreviewed"),
                "truth_status": unit.get("truth_status", "unknown"),
                "content_hash": unit.get("content_hash"),
                "summary": str(unit.get("title") or unit.get("content") or "")[:500],
                "index_version": int(unit.get("unit_version", 1)),
            }
        return value

    def upsert(
        self,
        unit: KnowledgeUnit | Mapping[str, Any],
        vector: Sequence[float] | None = None,
    ) -> str:
        """Upsert one unit projection and return its stable point identifier.

        A vector is required either as ``vector`` or as ``unit.extension['embedding']``.
        The adapter deliberately does not invent embeddings.
        """
        if vector is None and isinstance(unit, KnowledgeUnit):
            vector = unit.extension.get("embedding")
        if vector is None and isinstance(unit, Mapping):
            vector = unit.get("embedding")
        if vector is None:
            raise ValueError(
                "vector is required; embedding generation belongs outside VectorIndex"
            )
        values = [float(item) for item in vector]
        if len(values) != self.vector_size:
            raise ValueError(f"vector must contain exactly {self.vector_size} values")
        unit_id = self._unit_id(unit)
        # Qdrant accepts unsigned integers or UUIDs as point IDs.  Derive a
        # deterministic UUID while retaining the original unit key in payload.
        point_id = str(uuid5(NAMESPACE_URL, f"osint-video:{unit_id}"))
        point = models.PointStruct(
            id=point_id, vector=values, payload=self._payload(unit)
        )
        self.client.upsert(collection_name=self.collection, points=[point], wait=True)
        return point_id

    def search(
        self,
        query: Sequence[float],
        filters: Mapping[str, Any] | None = None,
        *,
        limit: int = 20,
    ) -> list[VectorCandidate]:
        """Return safe candidate identifiers and scores only."""
        values = [float(item) for item in query]
        if len(values) != self.vector_size:
            raise ValueError(f"query must contain exactly {self.vector_size} values")
        bounded_limit = max(1, min(int(limit), 100))
        query_filter = self._build_filter(filters or {})
        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection,
                query=values,
                query_filter=query_filter,
                limit=bounded_limit,
                with_payload=True,
                with_vectors=False,
            )
            points = response.points
        else:  # qdrant-client older releases
            points = self.client.search(
                collection_name=self.collection,
                query_vector=values,
                query_filter=query_filter,
                limit=bounded_limit,
                with_payload=True,
                with_vectors=False,
            )
        candidates: list[VectorCandidate] = []
        for point in points:
            payload = point.payload or {}
            unit_id = str(payload.get("unit_id") or payload.get("unit_key") or point.id)
            candidates.append(
                VectorCandidate(
                    unit_id, float(point.score), int(payload.get("index_version", 1))
                )
            )
        return candidates

    @staticmethod
    def _build_filter(filters: Mapping[str, Any]) -> Any | None:
        if not filters or models is None:
            return None
        conditions: list[Any] = []
        for key, value in filters.items():
            if key not in {
                "resource_key",
                "blogger_key",
                "status",
                "review_status",
                "truth_status",
            }:
                continue
            values: Iterable[Any] = (
                value if isinstance(value, (list, tuple, set)) else (value,)
            )
            for item in values:
                conditions.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=item))
                )
        return models.Filter(must=conditions) if conditions else None

    def health(self) -> bool:
        """Return whether the Qdrant endpoint responds to a lightweight call."""
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False


__all__ = ["VectorCandidate", "VectorIndex"]
