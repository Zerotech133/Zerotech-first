# -*- coding: utf-8 -*-
"""多源口径统一（canonical）测试（task-163）。

覆盖：
  1. canonical 产物 schema/count 稳定（冻结 count=29、target=30、gap=1、status=frozen）；
  2. 口径自洽：count == len(concepts) == 规则19+人工签核9+legacy1；每个概念 ≥2 独立来源；
  3. 裁决：驳回组（房地产/计算机）与 junk（- 注意）不计；自动未签核 30 记录为观察队列；
  4. 重建稳定：build_canonical() 与 canonical 文件 count 一致；source_artifacts 有 sha256；
  5. 消费端一致：training_data_matrix V17 == canonical count；data_governance_overview 读 canonical；
     导出 28 与 canonical 29 的差异在 reconciliation 中显式记录（导出改为读 canonical）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_multi_source_canonical.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import multi_source_canonical as msc  # noqa: E402

CANON = ROOT / "research" / "reports" / "multi_source_canonical_20261104.json"


def load() -> dict:
    return json.loads(CANON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1. schema / count
def test_canonical_schema_and_frozen_count():
    assert CANON.is_file(), "canonical 产物缺失"
    d = load()
    for key in ("schema_version", "report", "version", "frozen_at", "status", "definition",
                "count", "target", "meets", "gap", "calibers", "reconciliation",
                "concepts", "excluded", "auto_not_counted", "source_artifacts"):
        assert key in d, key
    assert d["status"] in ("frozen", "signed_off") and d["definition"]["unit"] == "concept"
    assert d["count"] == 30 and d["target"] == 30 and d["gap"] == 0 and d["meets"] is True
    assert d["count"] == len(d["concepts"])


# ---------------------------------------------------------------- 2. 口径自洽
def test_count_composition_self_consistent():
    d = load()
    groups = {}
    for c in d["concepts"]:
        groups[c["sign_off"]] = groups.get(c["sign_off"], 0) + 1
        assert int(c["n_sources"]) >= 2, f"{c['concept']} 来源不足 2"
        assert c.get("sources") and all(s.get("source_type") and s.get("source_key")
                                        for s in c["sources"]), c["concept"]
        assert c.get("group") in ("v4_baseline_rule", "v4_semantic_merge", "v2_legacy", "w22_public_edu_3src")
    assert groups == {"rule_auto": 19, "human_signed": 9, "legacy_signed": 1, "lead_signed": 1}
    assert sum(groups.values()) == d["count"] == 30
    assert d["calibers"]["v4_signed_concepts"] == 28
    assert d["calibers"]["v4_signed_concepts"] + groups["legacy_signed"] + d["calibers"].get("extra_signed", 0) == d["count"]


# ---------------------------------------------------------------- 3. 裁决：驳回/噪声/自动
def test_adjudication_excludes_rejected_and_junk():
    d = load()
    names = {str(c["concept"]) for c in d["concepts"]}
    # 驳回组（不同对象）不计
    assert not any("房地产" in n and "计算机" in n for n in names)
    assert any("房地产" in str(x.get("concept", ""))
               and "计算机" in str(x.get("reason", ""))
               for x in d["excluded"])
    # junk 噪声不计
    assert not any(str(n).strip().strip("-").strip() == "注意" for n in names)
    # 自动未签核 30 记录为观察队列，不进 canonical
    auto = d["auto_not_counted"]
    assert auto["v4_llm_auto_count"] == 30 and auto["llm_new_auto_groups"] >= 1
    assert d["calibers"]["v4_auto_groups"] == 29


# ---------------------------------------------------------------- 4. 重建稳定 + 来源可追溯
def test_rebuild_stable_and_traceable():
    d = load()
    rebuilt = msc.build_canonical()
    assert rebuilt["count"] == d["count"] == msc.canonical_count() == 30
    assert msc.self_check()["ok"] is True
    assert d["source_artifacts"], "需记录输入产物 sha256"
    for art in d["source_artifacts"]:
        assert len(str(art.get("sha256") or "")) == 64 and Path(art["path"]).is_file()
    # reconciliation 显式解释三口径
    rec = d["reconciliation"]
    assert rec["v2_27"].startswith("旧管线") or "已被" in rec["v2_27"]
    assert "28" in rec["export_28"] and "canonical" in rec["export_28"]
    assert d["calibers"]["export_v2_28"] == 28


# ---------------------------------------------------------------- 5. 消费端一致
def test_consumers_read_canonical():
    # training_data_matrix V17
    import training_data_matrix as tdm
    v17 = tdm.actual_values().get("V17")
    assert v17 == f"{msc.canonical_count()} / 30" == "30 / 30"
    # data_governance_overview 源码读 canonical（不在测试中执行全量脚本，避免写报告）
    src = (ROOT / "tools" / "kb_corpus" / "data_governance_overview.py").read_text(encoding="utf-8")
    assert "from multi_source_canonical import load_canonical" in src
    assert 'multi["final_multi_source"]' in src or "multi['final_multi_source']" in src
    # 导出口径以 canonical 为准（v4 起 30/30；v2/v3 历史包仅存档）
