"""K-MDC-D1 (郑玄): entity-disambiguation criteria — pure rules + synthetic probes.

判据层, not graph code: decide when two name-strings are the SAME / DIFFERENT
/ CANDIDATE person/seat/organization. Uncertain is always candidate — 消歧不
出事实. Synthetic fixtures only; no real holder names are asserted as facts.
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICTS = ("same", "different", "candidate")


class DisambigRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class EntityMention:
    etype: str  # person | seat | org
    raw_name: str
    source_id: str
    source_native_id: str

    def __post_init__(self) -> None:
        if self.etype not in ("person", "seat", "org"):
            raise DisambigRejected(f"etype {self.etype} not supported")
        for f in (self.raw_name, self.source_id, self.source_native_id):
            if not isinstance(f, str) or not f.strip():
                raise DisambigRejected("nonempty strings required")


_RULES = [
    # (etype, 规则号, 判定, 说明)
    (
        "person",
        "P1",
        "different",
        "全名不同且无一为另一的前缀/别名 → 不同人（默认保守不同）",
    ),
    (
        "person",
        "P2",
        "candidate",
        "同名(逐字相等)但来源不同 → 候选同一人, 须再核持股行为指纹",
    ),
    (
        "person",
        "P3",
        "candidate",
        "短名是长名的前缀(如'陈小群'⊂'陈小群X基金') → 候选关联, 不自动合并",
    ),
    (
        "person",
        "P4",
        "different",
        "同 raw_name 但 holder_type 冲突(自然人 vs 产品) → 不同实体, 产品与其管理人分离",
    ),
    ("seat", "S1", "same", "营业部名称逐字相等且同一来源ID体系 → 同席位"),
    (
        "seat",
        "S2",
        "candidate",
        "名称仅分支机构号不同(如'XX证券北京分公司'两行) → 候选同席位待核地址",
    ),
    ("org", "O1", "same", "工商全名+统一社会信用代码同 → 同组织(代码是强ID)"),
    (
        "org",
        "O2",
        "candidate",
        "简称匹配全名(如'国信'⊂'国信证券股份有限公司') → 候选, 无代码不合并",
    ),
    ("org", "O3", "candidate", "跨市场同名(沪/深/港主体) → 候选不同主体, 需注册地核验"),
]


def rules_for(etype: str) -> list[dict]:
    return [
        {"etype": e, "rule": r, "verdict": v, "note": n}
        for e, r, v, n in _RULES
        if e == etype
    ]


def disambiguate(
    a: EntityMention,
    b: EntityMention,
    *,
    same_native_id: bool = False,
    prefix_match: bool = False,
    type_conflict: bool = False,
) -> str:
    """主判定入口: 返回 same/different/candidate; 判据冲突时抛异常不猜."""
    if a.etype != b.etype:
        raise DisambigRejected("cross-type comparison rejected")
    if same_native_id:
        return "same"
    if type_conflict:
        return "different"  # P4 先于同名: 产品与其管理人强制分离
    if a.raw_name == b.raw_name:
        if a.etype == "seat":
            return "same"  # S1
        return "candidate"  # P2 同名人候选 / O3 跨源同名组织候选
    if prefix_match:
        return "candidate"  # P3/S2/O2
    return "different"  # P1 默认保守
