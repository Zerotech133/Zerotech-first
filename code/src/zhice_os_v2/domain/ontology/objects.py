"""Palantir 式本体层：统一对象、关系、来源。

所有业务对象都应继承 OntologyObject，使系统具备统一的对象 ID、
对象关系和数据血缘能力。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ObjectRef:
    object_id: str
    object_type: str


@dataclass(frozen=True)
class ObjectLink:
    relation: str
    target: ObjectRef


@dataclass(frozen=True)
class Provenance:
    source_system: str
    transform_name: str | None = None
    transform_version: str | None = None
    input_refs: tuple[ObjectRef, ...] = ()
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class OntologyObject:
    object_id: str
    object_type: str
    properties: dict = field(default_factory=dict)
    links: list[ObjectLink] = field(default_factory=list)
    provenance: Provenance | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def ref(self) -> ObjectRef:
        return ObjectRef(object_id=self.object_id, object_type=self.object_type)

    def add_link(self, relation: str, target: ObjectRef) -> None:
        self.links.append(ObjectLink(relation=relation, target=target))
        self.updated_at = utc_now()

    def set_provenance(self, provenance: Provenance) -> None:
        self.provenance = provenance
        self.updated_at = utc_now()
