"""K-MDC-D1 tests: disambiguation criteria — the 老田 scenarios as fixtures.

合成反例集: 作手新一/陈小群席位共现、同名自然人、国资简称、产品vs自然人
全部走判据且验证「不确定即候选」铁律。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intel_disambig_rules import (  # noqa: E402
    DisambigRejected, EntityMention, disambiguate, rules_for,
)


def person(name, src="tushare", nid="r1"):
    return EntityMention("person", name, src, nid)


def test_same_name_across_sources_is_candidate_never_same():
    # 老田场景: 前100股东里的同名 —— 不同来源同名不自动同一人
    a = person("张三", src="tushare")
    b = person("张三", src="xueqiu")
    assert disambiguate(a, b) == "candidate"


def test_prefix_name_is_candidate_not_merged():
    # '陈小群' vs '陈小群旗下基金X' — 前缀只出候选
    a = person("陈小群")
    b = person("陈小群旗下私募基金X")
    assert disambiguate(a, b, prefix_match=True) == "candidate"
    assert disambiguate(a, b) == "different"  # 无前缀标志且名不同 → 保守不同


def test_type_conflict_splits_product_from_manager():
    a = person("某某1号私募证券投资基金")
    b = person("某某1号私募证券投资基金")
    assert disambiguate(a, b, type_conflict=True) == "different"


def test_seat_same_source_exact_name_is_same():
    s1 = EntityMention("seat", "华泰证券深圳益田路营业部", "tushare", "seat-1")
    s2 = EntityMention("seat", "华泰证券深圳益田路营业部", "tushare", "seat-1")
    assert disambiguate(s1, s2, same_native_id=True) == "same"


def test_org_short_name_is_candidate_until_code_verified():
    o1 = EntityMention("org", "国信证券", "ann", "a1")
    o2 = EntityMention("org", "国信证券股份有限公司", "ann", "a2")
    assert disambiguate(o1, o2, prefix_match=True) == "candidate"


def test_cross_market_same_name_org_is_candidate():
    o1 = EntityMention("org", "中信证券", "ann", "cn-1")
    o2 = EntityMention("org", "中信证券", "hkex", "hk-1")
    assert disambiguate(o1, o2) == "candidate"  # 跨源同名组织→候选不同主体


def test_cross_type_and_bad_input_rejected():
    with pytest.raises(DisambigRejected, match="cross-type"):
        disambiguate(person("张三"), EntityMention("seat", "X营业部", "s", "1"))
    with pytest.raises(DisambigRejected, match="not supported"):
        EntityMention("ticker", "600519", "s", "1")
    with pytest.raises(DisambigRejected, match="nonempty"):
        EntityMention("person", "  ", "s", "1")


def test_rules_registry_complete_and_verdicts_legal():
    from intel_disambig_rules import VERDICTS
    for etype in ("person", "seat", "org"):
        rs = rules_for(etype)
        assert rs, f"{etype} 判据缺失"
        for r in rs:
            assert r["verdict"] in VERDICTS and r["note"]
