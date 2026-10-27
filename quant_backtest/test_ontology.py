# -*- coding: utf-8 -*-
"""L1 本体注册表（ontology.py）单元测试。

三层模式的单元层：不连 Neo4j（快照/对账的碰图路径在集成层
test_kg_portal_integration.py 与每日链 graph_check 后的 export 步覆盖），
固化注册表自身纪律：
  · 完备性：kg_schema 声明的每个 LBL_/REL_ 常量必须已注册
    （K264 教训：情报线 10 边族曾零注册，检查器全盲）
  · 元数据齐全：domain/key/trust/writer 必填；trust 档合法
  · 时序铁律：temporal 边与 kg_schema.EDGE_PROPS 口径一致
  · registry_json/trust_grade 视图组装（档随 kg_source_rating 实证走）
  · snapshot 离线降级：Neo4j 不可达 → ok=False 不抛（warn-only）
"""

import pytest

import ontology as OT
import kg_schema as S


# ── 完备性：schema 常量 ↔ 注册表 ────────────────────────────


def _schema_labels():
    lbls = set()
    for k, v in vars(S).items():
        if k.startswith("LBL_"):
            lbls.add(v)
    return lbls


def _schema_rels():
    rels = set()
    for k, v in vars(S).items():
        if k.startswith("REL_"):
            rels.add(v)
    return rels


def test_registry_covers_schema_labels():
    """kg_schema 的 LBL_ 常量必须全部注册（防再出现零注册标签）。"""
    miss = _schema_labels() - set(OT.NODE_TYPES)
    assert not miss, "未注册标签：%s" % sorted(miss)


def test_registry_covers_schema_rels():
    """kg_schema 的 REL_ 常量必须全部注册（K264 教训回归）。"""
    miss = _schema_rels() - set(OT.EDGE_TYPES)
    assert not miss, "未注册边型：%s" % sorted(miss)


def test_registry_size_floor():
    """规模下限（当前实况 30 标签/42 边型；只增不减防误删）。"""
    assert len(OT.NODE_TYPES) >= 30
    assert len(OT.EDGE_TYPES) >= 41


# ── 元数据纪律 ──────────────────────────────────────────────


@pytest.mark.parametrize("lbl,m", sorted(OT.NODE_TYPES.items()))
def test_node_meta_complete(lbl, m):
    assert m["domain"] and m["trust"] in OT.TRUST_SCALE
    assert m["source"] and m["writer"] and m["desc"]
    assert m.get("key")  # 无唯一约束也须显式声明
    assert "date_field" in m  # 静态=None 亦须显式


@pytest.mark.parametrize("rel,m", sorted(OT.EDGE_TYPES.items()))
def test_edge_meta_complete(rel, m):
    assert m["domain"] and m["trust"] in OT.TRUST_SCALE
    assert m["source"] and m["writer"] and m["desc"]
    assert m["frm"] and m["to"]
    assert isinstance(m["temporal"], bool)


def test_temporal_edges_declare_validstart():
    """temporal 边的保鲜锚必须是 validStart（时序铁律口径）。"""
    for rel, m in OT.EDGE_TYPES.items():
        if m["temporal"]:
            assert m["date_field"] == "validStart", rel


def test_edge_common_props_match_schema():
    """_edge_common 键集 = kg_schema.EDGE_PROPS（唯一权威源）。"""
    assert set(OT.PROP_META["_edge_common"]) == set(S.EDGE_PROPS)


def test_prop_meta_only_known_labels():
    for lbl in OT.PROP_META:
        if lbl != "_edge_common":
            assert lbl in OT.NODE_TYPES, lbl


# ── 视图组装 ────────────────────────────────────────────────


def test_registry_json_shape():
    r = OT.registry_json()
    assert r["counts"] == {
        "node_types": len(OT.NODE_TYPES),
        "edge_types": len(OT.EDGE_TYPES),
    }
    sec = r["node_types"]["Security"]
    assert sec["props"]["ts_code"]["note"]  # 属性元数据已带上
    assert "count" not in sec  # 纯注册表不含计数
    tr = r["edge_types"]["TRADED_ON"]
    assert tr["frm"] == "Seat" and tr["to"] == "Security"


def test_trust_grade_prefers_rating():
    """档随 kg_source_rating 实证走：human_curation 在评级表为 A。"""
    assert OT.trust_grade("human_curation") == "A"
    assert OT.trust_grade("tushare_pro") == "B"
    # 组合族：任一命中即取该档
    assert OT.trust_grade("xtcloud_local+tushare_pro") == "B"
    assert OT.trust_grade("不存在的族") is None


def test_labels_rels_sorted():
    assert OT.labels() == sorted(OT.NODE_TYPES)
    assert OT.rels() == sorted(OT.EDGE_TYPES)


# ── 碰图路径离线降级 ────────────────────────────────────────


def test_snapshot_degrades_offline(monkeypatch):
    """Neo4j 不可达 → ok=False + error，不抛（warn-only 语义）。"""

    class Boom:
        def _session(self):
            raise ConnectionError("refused")

    monkeypatch.setattr(OT, "_connect", lambda: Boom())
    snap = OT.snapshot()
    assert snap["ok"] is False
    assert "Neo4j 不可达" in snap["error"]
    # 纯注册表视图仍在（消费者可降级展示元数据）
    assert snap["node_types"]["Security"]["desc"]


def test_validate_offline_skip(monkeypatch):
    class Boom:
        def _session(self):
            raise ConnectionError("refused")

    monkeypatch.setattr(OT, "_connect", lambda: Boom())
    issues = OT.validate()
    assert issues and issues[0][0] == "SKIP"
