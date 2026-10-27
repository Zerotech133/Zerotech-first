"""K-MDC-G2 (郑玄): graph minimal loop — one-stock-one-dossier queryable edges.

一票一卷可查边: 内存邻域视图, 不建图数据库. 每条边回源(evidence_key/basis),
全部 candidate_only; 与 D1/D2 判据与规则衔接.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from intel_relation_rules import RelationCandidate, RelationRejected


@dataclass
class GraphView:
    """邻域索引: entity -> [(edge)] 双向登记, 只读查询."""

    _edges: list[RelationCandidate] = field(default_factory=list)
    _by_entity: dict[str, list[int]] = field(default_factory=dict)
    _by_stock: dict[str, list[int]] = field(default_factory=dict)

    def add(
        self, candidate: RelationCandidate, *, stock_code: str | None = None
    ) -> int:
        if not isinstance(candidate, RelationCandidate):
            raise RelationRejected("RelationCandidate required")
        idx = len(self._edges)
        self._edges.append(candidate)
        self._by_entity.setdefault(candidate.left, []).append(idx)
        self._by_entity.setdefault(candidate.right, []).append(idx)
        if stock_code:
            if not isinstance(stock_code, str) or not stock_code.strip():
                raise RelationRejected("stock_code must be nonempty")
            self._by_stock.setdefault(stock_code, []).append(idx)
        return idx

    def neighborhood(self, entity: str) -> list[dict]:
        """实体邻域: 该实体全部边, 含回源指针与调查问题."""
        out = []
        for idx in self._by_entity.get(entity, []):
            e = self._edges[idx]
            out.append(
                {
                    "rel_type": e.rel_type,
                    "left": e.left,
                    "right": e.right,
                    "evidence_key": e.evidence_key,
                    "question": e.question,
                    "basis": e.basis,
                    "verdict": "candidate",
                }
            )
        return out

    def dossier(self, stock_code: str) -> dict:
        """一票一卷: 该票涉及的全部候选边 + 截断可见(共 N 显 M)."""
        idxs = self._by_stock.get(stock_code, [])
        edges = []
        for idx in idxs:
            e = self._edges[idx]
            edges.append(
                {
                    "rel_type": e.rel_type,
                    "pair": f"{e.left} × {e.right}",
                    "evidence_key": e.evidence_key,
                    "question": e.question,
                }
            )
        shown = edges[:20]
        return {
            "stock_code": stock_code,
            "total_edges": len(edges),
            "shown": len(shown),
            "truncated": len(edges) > len(shown),
            "edges": shown,
            "verdict": "candidate_only",
        }

    def stats(self) -> dict:
        return {
            "edges": len(self._edges),
            "entities": len(self._by_entity),
            "stocks": len(self._by_stock),
        }
