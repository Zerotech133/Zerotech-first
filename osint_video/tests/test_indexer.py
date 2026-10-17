"""Unit tests for the Qdrant adapter without requiring a running service."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from knowledge.indexer import VectorIndex


class FakeClient:
    def __init__(self) -> None:
        self.points = []
        self.created = False

    def collection_exists(self, collection: str) -> bool:
        return self.created

    def create_collection(self, **_: object) -> None:
        self.created = True

    def upsert(self, **kwargs: object) -> None:
        self.points.extend(kwargs["points"])

    def query_points(self, **_: object) -> object:
        return SimpleNamespace(points=[SimpleNamespace(id="u1", score=0.9, payload={"unit_id": "u1", "index_version": 2})])

    def get_collections(self) -> object:
        return SimpleNamespace(collections=[])


def test_upsert_and_search_are_safe_projection() -> None:
    client = FakeClient()
    index = VectorIndex(client, collection="test", vector_size=2)
    point_id = index.upsert({"unit_key": "u1", "content": "secret source text", "embedding": [0.1, 0.2]})
    assert point_id
    assert client.points[0].payload["summary"] == "secret source text"
    assert "embedding" not in client.points[0].payload
    assert index.search([0.1, 0.2])[0].unit_id == "u1"


def test_vector_dimension_is_validated() -> None:
    index = VectorIndex(FakeClient(), collection="test", vector_size=2)
    with pytest.raises(ValueError, match="exactly 2"):
        index.upsert({"unit_key": "u1", "embedding": [1.0]})
