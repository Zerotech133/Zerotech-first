# -*- coding: utf-8 -*-
"""C3 守卫的回归测试：确保 kg_cq 静态/实时断言逻辑不被改坏。

CI 里与本体的 CQ 回归同源运行：本体删类型 → schema_validate 必 FAIL。
"""

import os
import sys

import pytest

_QB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "quant_backtest")
if _QB not in sys.path:
    sys.path.insert(0, _QB)

import kg_cq as K          # noqa: E402
import cq_registry as CQ    # noqa: E402


def test_extract_refs_varlen_and_multitype():
    cypher = ("MATCH (s:Security)-[:BELONGS_TO*1..3]->(p:Concept)"
              "-[:HAS_LIMIT|HAS_VOL_EVENT]->(e:MarketEvent) RETURN count(*)")
    labels, rels = K.extract_refs(cypher)
    assert labels == {"Security", "Concept", "MarketEvent"}
    assert rels == {"BELONGS_TO", "HAS_LIMIT", "HAS_VOL_EVENT"}


def test_schema_validate_passes_for_real_registry():
    issues = K.schema_validate(CQ.all_cqs())
    assert issues == [], "当前 CQ 注册表不应有静态引用问题"


def test_schema_validate_catches_deleted_type():
    fake = [dict(id="CQ-X", cypher="MATCH (s:Security)-[:DELETED_EDGE]->(x:NonExistNode) RETURN count(*)")]
    issues = K.schema_validate(fake)
    assert len(issues) == 2
    kinds = {(i[1], i[2]) for i in issues}
    assert ("edge", "DELETED_EDGE") in kinds
    assert ("node", "NonExistNode") in kinds


class _Rec:
    def __init__(self, d):
        self._d = d
    def keys(self):
        return list(self._d.keys())
    def __getitem__(self, k):
        return self._d[k]


class _MockSession:
    def __init__(self, val):
        self.val = val
    def run(self, cypher):
        return [_Rec({"c": self.val})]


def test_assess_live_nonempty_pass():
    res = K.assess(CQ.all_cqs(), session=_MockSession(5))
    assert res["passed"] is True
    assert res["live_failures"] == 0
    assert res["live_ran"] == len(CQ.all_cqs())


def test_assess_live_nonempty_zero_fails():
    res = K.assess(CQ.all_cqs(), session=_MockSession(0))
    # 8 条 live_non_empty 的 CQ 应失败（其他仅要求可执行）
    assert res["passed"] is False
    assert res["live_failures"] == 8


def test_assess_no_session_skips_live():
    res = K.assess(CQ.all_cqs(), session=None)
    assert res["passed"] is True           # 仅静态，当前无回归
    assert res["live_ran"] == 0
    assert res["live_skipped"] == 0         # None 时连 skip 计数都不产生
    assert res["exit_code"] == 0
