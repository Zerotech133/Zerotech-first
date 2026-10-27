"""K-MDC-D2 (郑玄): relation candidate rules — co-occurrence/holding/supply-chain.

关系候选生成器: 输入=消歧后的实体对+来源事件, 输出=candidate_only 关系候选.
铁律: 任何规则都不产 fact; 时间邻近与共同持有只提问题(调查方向), 不坐实.
三型: R-CO 共现 / R-HOLD 持股 / R-SUP 供应链(公告提及).
"""

from __future__ import annotations

from dataclasses import dataclass

REL_TYPES = ("co_occurrence", "holding", "supply_chain")


class RelationRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class RelationCandidate:
    rel_type: str
    left: str  # 已消歧实体键 (person/seat/org)
    right: str
    evidence_key: str  # 回源指针 (A4 影子/证据台账键)
    question: str  # 该候选提出的调查问题
    basis: str  # 触发规则说明

    def __post_init__(self) -> None:
        if self.rel_type not in REL_TYPES:
            raise RelationRejected(f"rel_type {self.rel_type} not supported")
        for f in (self.left, self.right, self.evidence_key, self.question, self.basis):
            if not isinstance(f, str) or not f.strip():
                raise RelationRejected("nonempty strings required")


def co_occurrence(
    seat_a: str, seat_b: str, ts_code: str, trade_date: str, evidence_key: str
) -> RelationCandidate:
    """R-CO: 两席位同日同票上榜 → 共现候选. (作手新一×陈小群场景的规则化)"""
    if seat_a == seat_b:
        raise RelationRejected("self co-occurrence is meaningless")
    return RelationCandidate(
        "co_occurrence",
        seat_a,
        seat_b,
        evidence_key,
        question=f"{seat_a} 与 {seat_b} 于 {trade_date} 同日同登 {ts_code} 龙虎榜——"
        f"是否同一操盘团体或联动? 须再核历史共现频次与买卖方向",
        basis=f"lhb_same_day_same_stock:{trade_date}:{ts_code}",
    )


def holding(
    holder_key: str, org_key: str, end_date: str, ratio: float, evidence_key: str
) -> RelationCandidate:
    """R-HOLD: 持有人×标的/组织 → 持股候选 (来自 A4 十大股东影子)."""
    if (
        not isinstance(ratio, (int, float))
        or isinstance(ratio, bool)
        or not 0 < ratio <= 100
    ):
        raise RelationRejected("ratio must be (0,100]")
    return RelationCandidate(
        "holding",
        holder_key,
        org_key,
        evidence_key,
        question=f"{holder_key} 于 {end_date} 报告期末持有 {ratio}%——"
        f"是否与其他前十大股东存在关联或一致行动? 须消歧+人审",
        basis=f"top10_holders:{end_date}:{ratio}",
    )


def supply_chain(
    buyer: str, supplier: str, ann_date: str, evidence_key: str
) -> RelationCandidate:
    """R-SUP: 公告提及的采购/供应关系 → 供应链候选 (公告措辞≠业务事实)."""
    return RelationCandidate(
        "supply_chain",
        buyer,
        supplier,
        evidence_key,
        question=f"{ann_date} 公告提及 {buyer} 与 {supplier} 供应关系——"
        f"是否实质业务往来? 须核公告原文与后续履约披露",
        basis=f"ann_mention:{ann_date}",
    )


def batch_co_occurrence(lhb_rows: list[dict]) -> list[RelationCandidate]:
    """从龙虎榜影子行批量产共现候选: 按 (trade_date, ts_code) 分组两两配对.

    输入行须含: trade_date, ts_code, seat(席位名), evidence_key.
    输出全部 candidate_only; 同组席位数>4 时只报 question 不逐对展开(防爆炸).
    """
    groups: dict[tuple, list[dict]] = {}
    for row in lhb_rows:
        for f in ("trade_date", "ts_code", "seat"):
            if not isinstance(row.get(f), str) or not row[f].strip():
                raise RelationRejected(f"row missing {f}")
        groups.setdefault((row["trade_date"], row["ts_code"]), []).append(row)
    out: list[RelationCandidate] = []
    for (d, code), rows in sorted(groups.items()):
        seats = sorted({r["seat"] for r in rows})
        if len(seats) < 2:
            continue
        key = rows[0].get("evidence_key", f"lhb:{d}:{code}")
        if len(seats) > 4:
            out.append(
                RelationCandidate(
                    "co_occurrence",
                    seats[0],
                    f"{len(seats)}席群体",
                    key,
                    question=f"{d} {code} 有 {len(seats)} 席位同榜——群体行为, 逐对展开见明细",
                    basis=f"lhb_group:{d}:{code}:{len(seats)}",
                )
            )
            continue
        for i in range(len(seats)):
            for j in range(i + 1, len(seats)):
                out.append(co_occurrence(seats[i], seats[j], code, d, key))
    return out
