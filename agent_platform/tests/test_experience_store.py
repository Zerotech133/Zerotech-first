# -*- coding: utf-8 -*-
"""WO-LEGION-S26 Z4 经验入库测试。

验收（《赵云进化层V3》Z4）：一条经验可被下个同类任务检索到。
另覆盖：签字链、风控域只读、四字段完整性、去重键、Z5 对 evidence 的路径把关。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))
sys.path.insert(0, str(_PLATFORM / "engine"))

from engine import experience_store as S  # noqa: E402


@pytest.fixture()
def root(tmp_path):
    return tmp_path


def _write(**kw):
    base = dict(domain="stock_screen",
                cue="误拦率偏高的判据是否值得采纳",
                lesson="采纳样本外口径，误拦率 -7pp",
                evidence="对照实验 card=EX-1 verifier=3",
                confidence=0.6,
                signed_by="laotian")
    base.update(kw)
    return base


# ---------------------------------------------------------------- 硬门槛

def test_requires_signature(root):
    with pytest.raises(S.SignatureRequired):
        S.write_entry(**_write(signed_by=""), root=root)
    with pytest.raises(S.SignatureRequired):
        S.write_entry(**_write(signed_by="   "), root=root)


def test_riskguard_domain_readonly(root):
    with pytest.raises(S.StoreError) as ei:
        S.write_entry(**_write(domain="riskguard"), root=root)
    assert "read-only" in str(ei.value)


def test_unknown_domain_rejected(root):
    with pytest.raises(S.StoreError):
        S.write_entry(**_write(domain="nope"), root=root)


def test_required_fields(root):
    for field in ("cue", "lesson", "evidence"):
        kw = _write()
        kw[field] = "  "
        with pytest.raises(S.StoreError) as ei:
            S.write_entry(**kw, root=root)
        assert field in str(ei.value)


def test_confidence_range(root):
    for bad in (-0.1, 1.5, "high"):
        with pytest.raises(S.StoreError):
            S.write_entry(**_write(confidence=bad), root=root)


def test_duplicate_cue_rejected(root):
    S.write_entry(**_write(), root=root)
    with pytest.raises(S.StoreError) as ei:
        S.write_entry(**_write(cue="  误拦率偏高的判据是否值得采纳  "), root=root)
    assert "duplicate" in str(ei.value)


def test_evidence_leak_path_rejected(root):
    with pytest.raises(S.StoreError) as ei:
        S.write_entry(**_write(evidence="见 /data/.grader/rules.py 第 3 条"), root=root)
    assert "lookahead" in str(ei.value)


# ---------------------------------------------------------------- 落盘形态

def test_write_creates_three_artifacts(root):
    e = S.write_entry(**_write(), root=root)
    assert e.entry_id == "EXP-STOCK_SCREEN-001"
    exp = (root / "stock_screen" / "EXPERIENCE.md").read_text(encoding="utf-8")
    idx = (root / "stock_screen" / "INDEX.md").read_text(encoding="utf-8")
    rows = (root / "_index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    for token in ("cue", "lesson", "evidence", "confidence", "采纳"):
        assert token in exp
    assert "EXP-STOCK_SCREEN-001" in idx
    assert len(rows) == 1 and json.loads(rows[0])["domain"] == "stock_screen"


def test_second_entry_numbering(root):
    S.write_entry(**_write(), root=root)
    e2 = S.write_entry(**_write(cue="另一条经验：影子账本备份轮转"), root=root)
    assert e2.entry_id == "EXP-STOCK_SCREEN-002"


def test_domain_isolation(root):
    S.write_entry(**_write(domain="execution", cue="执行链断裂恢复"), root=root)
    S.write_entry(**_write(domain="tooling", cue="CLI 生成器失败重试"), root=root)
    e_exe = S.search("执行链", domain="execution", root=root)
    e_tool = S.search("执行链", domain="tooling", root=root)
    assert e_exe and not e_tool


# ---------------------------------------------------------------- 检索（验收判据）

def test_search_finds_entry_by_cue(root):
    e = S.write_entry(**_write(), root=root)
    hits = S.search("误拦率 判据", root=root)
    assert hits and hits[0]["entry_id"] == e.entry_id


def test_search_by_lesson_keyword(root):
    S.write_entry(**_write(lesson="样本外验证不达标必须回滚"), root=root)
    assert S.search("样本外验证", root=root)


def test_search_empty_and_unknown(root):
    S.write_entry(**_write(), root=root)
    assert S.search("", root=root) == []
    with pytest.raises(S.StoreError):
        S.search("x", domain="bogus", root=root)


def test_search_confidence_breaks_tie(root):
    S.write_entry(**_write(cue="备份策略 A", confidence=0.9), root=root)
    S.write_entry(**_write(cue="备份策略 B", confidence=0.3), root=root)
    hits = S.search("备份策略", root=root)
    assert hits[0]["cue"] == "备份策略 A"


def test_demo_end_to_end(tmp_path):
    assert S._demo(tmp_path) == 0
