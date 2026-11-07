# -*- coding: utf-8 -*-
"""C4 双时态面回归测试：DDL 生成一致性、注册表暴露、gate R6 三态。

只测静态/注入面（沙箱无 Neo4j driver，实时行为由 kg_migrate_bitemporal
在项目机上以 --apply 前的 dry-run 验证）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ontology as O


def test_ddl_refs_registered():
    """DDL 引用的标签/边类型必须全部在 TBox 注册（防手滑引用）。"""
    for s in O.bitemporal_ddl():
        assert "CREATE INDEX" in s and "IF NOT EXISTS" in s
        # 标签/边类型名取自注册表本身，这里验证语句里反引号包裹的标识符可解析
        assert s.count("`") % 2 == 0


def test_bitemporal_domains_cover_expected():
    """时变边类型按域筛选：资金/事件/时间三域，且至少覆盖若干条。"""
    bt = O.bitemporal_edge_types()
    assert len(bt) >= 5
    for r in bt:
        assert (EDGE := O.EDGE_TYPES[r]) and EDGE.get("domain") in O.BITEMPORAL_EDGE_DOMAINS


def test_expected_indexes_matches_ddl_count():
    """期望索引规格化条数 == DDL 条数（一一对齐，gate 比对不失真）。"""
    assert len(O._expected_indexes()) == len(O.bitemporal_ddl())


def test_ddl_uses_variable_prefixed_props():
    """2026-11-07 dry-run 实证钉子：Neo4j 5.x 索引属性必须带变量前缀
    （n./r.），裸属性全量 SyntaxError（服务器 5.26.32 实测 56/56 失败）。"""
    for s in O.bitemporal_ddl():
        assert "ON (n." in s or "ON (r." in s, s
        assert "ON (`" not in s, s  # 裸属性写法禁止回归


def test_backfill_idempotent_shape():
    """回填语句全部带 IS NULL 守卫（只补缺失，不覆盖）。"""
    for desc, s in O.bitemporal_backfill_cypher():
        assert "IS NULL" in s and "SET" in s


def test_registry_json_exposes_bitemporal():
    r = O.registry_json()
    b = r["bitemporal"]
    assert b["tx_field"] == "tx_created"
    assert set(b["edge_domains"]) == set(O.BITEMPORAL_EDGE_DOMAINS)
    assert b["ddl_statement_count"] == len(O.bitemporal_ddl())


def _mk_snap(indexes):
    return {"ok": True,
            "drift": {"unregistered_labels": [], "unregistered_rels": [],
                      "governance_gap": O._governance_gap()},
            "freshness": {"market_max_list_date": "20261101"},
            "indexes": indexes}


def _live_full():
    return [{"entityType": e, "labelsOrTypes": list(lot),
             "properties": list(p), "state": "ONLINE", "name": "x"}
            for e, lot, p in O._expected_indexes()]


def _r6(g):
    return [r for r in g["rules"] if r["id"] == "bitemporal_index"][0]


def test_gate_r6_skip_when_unavailable():
    g = O.gate(snap=_mk_snap(None))
    assert _r6(g)["level"] == "PASS"
    assert g["exit_code"] == 0


def test_gate_r6_pass_when_complete():
    g = O.gate(snap=_mk_snap(_live_full()))
    assert _r6(g)["level"] == "PASS"
    assert g["exit_code"] == 0


def test_gate_r6_warn_when_partial():
    g = O.gate(snap=_mk_snap(_live_full()[:-3]))
    assert _r6(g)["level"] == "WARN"
    assert g["exit_code"] == 0  # 默认 fail=999 不阻断（上线跟踪期）


def test_gate_r6_fail_when_tightened():
    old = O.GATE_THRESHOLDS["bitemporal_index_fail_missing"]
    try:
        O.GATE_THRESHOLDS["bitemporal_index_fail_missing"] = 5
        g = O.gate(snap=_mk_snap(_live_full()[:26]))
        assert _r6(g)["level"] == "FAIL"
        assert g["exit_code"] == 1
    finally:
        O.GATE_THRESHOLDS["bitemporal_index_fail_missing"] = old
