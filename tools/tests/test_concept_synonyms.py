# -*- coding: utf-8 -*-
"""tools/tests/test_concept_synonyms.py · 概念命名统一/同义归并（W1-2）单元测试。

覆盖（任务验收要求）：
  1. **同义合并**：均线/MA/移动平均线、中枢/缠论中枢、涨停/涨停板 等同表归一 → 同簇；
  2. **包含合并**：中枢判断逻辑 ⊃ 中枢（修饰差异）→ 合并；"第一定律/第二定律" 等数字差异 → 不合并；
  3. **不误合并**（关键）：五日均线 ≠ 均线、伏击涨停 ≠ 涨停、NPO ≠ SNPO、进口/出口 等反向/参数/
     缩写差异不合并；泛词（价格）不作包含短词；
  4. **规则清洗 + 模糊合并**：序号/副标题/括注清洗、黄金分割/黄金分隔、东盟区域/地区 归并；
  5. **只读 + 输出**：读真实 W1-1 JSON（输出到 tmp），输入 sha256 前后一致、输出 JSON/MD schema 完整。

不写真实报告、不改输入。运行：
C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_concept_synonyms.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import concept_synonyms as cs  # noqa: E402

INPUT_JSON = ROOT / "research" / "reports" / "knowledge_multisource_20261103.json"


# ------------------------------------------------------------------ 工具
def _entry(concept: str, n_sources: int = 1, source_types=None, sources=None,
           confidence: str = "低") -> dict:
    return {"concept": concept, "confidence": confidence, "n_sources": n_sources,
            "source_types": source_types or ["blogger"], "sources": sources or []}


def _clusters_of(*concepts: str) -> dict[str, list[str]]:
    """把概念列表跑一遍合并，返回 {展示名: [成员概念]}。"""
    entries = [_entry(c) for c in concepts]
    result = cs.merge_concepts(entries)
    out = {}
    for cluster in result["clusters"]:
        out[cluster["display"]] = [m["concept"] for m in cluster["members"]]
    return out


def _same_cluster(out: dict, a: str, b: str) -> bool:
    for members in out.values():
        if a in members and b in members:
            return True
    return False


# ------------------------------------------------------------------ 1. 同义合并
def test_synonym_merge():
    assert len(cs.DOMAIN_SYNONYM_GROUPS) >= 40
    syn = cs.build_synonym_map()
    # 任务点名四组
    assert cs.canonical_concept("移动平均线", syn) == cs.canonical_concept("MA", syn) \
        == cs.canonical_concept("均线", syn)
    assert cs.canonical_concept("缠论中枢", syn) == cs.canonical_concept("中枢", syn)
    assert cs.canonical_concept("涨停板", syn) == cs.canonical_concept("涨停", syn)
    assert cs.canonical_concept("仓位管理", syn) == cs.canonical_concept("仓位", syn)
    assert cs.canonical_concept("止损位", syn) == cs.canonical_concept("止损", syn)
    assert cs.canonical_concept("背驰", syn) == cs.canonical_concept("背离", syn)
    assert cs.canonical_concept("箱体", syn) == cs.canonical_concept("平台", syn)
    # 合并成同一簇（来自不同来源类型 → 合并后多源确认）
    out = _clusters_of("均线", "移动平均线", "MA")
    assert _same_cluster(out, "均线", "MA")


# ------------------------------------------------------------------ 2. 包含合并 + 数字守卫
def test_containment_merge_and_numeral_guard():
    out = _clusters_of("中枢", "中枢判断逻辑", "缠论中枢")
    assert _same_cluster(out, "中枢", "中枢判断逻辑")
    assert _same_cluster(out, "中枢", "缠论中枢")
    # 序号/定律数字不同 → 不合并
    out2 = _clusters_of("伏击涨停第一定律", "伏击涨停第二定律")
    assert not _same_cluster(out2, "伏击涨停第一定律", "伏击涨停第二定律")
    # 窗口参数不同 → 不合并
    out3 = _clusters_of("均线", "五日均线")
    assert not _same_cluster(out3, "均线", "五日均线")


# ------------------------------------------------------------------ 3. 不误合并
def test_no_false_merge():
    # 泛词不作包含短词
    ok, _ = cs.can_contain_merge("价格", "稀土价格分化", cs.canonical_set())
    assert not ok
    # 反向/内容差异不合并
    for a, b in [("进口产品结构", "出口产品结构"), ("高开低走", "低开高走"),
                 ("基本金属铜", "基本金属铝"), ("NPO", "SNPO"),
                 ("伏击涨停", "涨停"), ("底分形结构", "分形结构")]:
        out = _clusters_of(a, b)
        assert not _same_cluster(out, a, b), f"{a} 与 {b} 不应合并"
    # 差异含方向词时包含合并被拒
    ok2, why = cs.can_contain_merge("中枢", "中枢与背驰", cs.canonical_set())
    assert not ok2 and "实质内容" in why
    # 五日均线不会被纳入均线簇（窗口差异）
    assert not cs.should_fuzzy_merge("均线", "五日均线")[0]


# ------------------------------------------------------------------ 4. 清洗 + 模糊合并
def test_cleaning_and_fuzzy_merge():
    assert cs.clean_concept("- 01 均值回复过程——价差围绕均值波动") == "均值回复过程"
    assert cs.clean_concept("- 08 KDJ") == "kdj"
    assert cs.clean_concept("第 10 个") == ""          # 纯序号 → 清洗为空，按噪声剔除
    assert cs.junk_reason(cs.clean_concept("第 10 个")) is not None
    assert cs.junk_reason("注意") is not None
    assert cs.junk_reason("量学理论") is None
    # 常见错别字/同义：黄金分割 vs 黄金分隔、东盟区域 vs 东盟地区
    assert cs.should_fuzzy_merge("黄金分割", "黄金分隔")[0]
    assert cs.should_fuzzy_merge("东盟区域贸易地位", "东盟地区贸易地位")[0]
    assert not cs.should_fuzzy_merge("新低", "新高")[0]
    # 词序重排合并
    assert cs.should_fuzzy_merge("缠论结构走势", "缠论走势结构")[0]


# ------------------------------------------------------------------ 5. 只读 + 输出（真实 W1-1 JSON -> tmp）
def test_run_read_only_and_schema(tmp_path):
    if not INPUT_JSON.is_file():
        pytest.skip(f"W1-1 输入不存在: {INPUT_JSON}")
    sha_before = hashlib.sha256(INPUT_JSON.read_bytes()).hexdigest()
    out_j, out_m = tmp_path / "syn.json", tmp_path / "syn.md"
    payload = cs.run(INPUT_JSON, out_j, out_m)
    # 只读：输入 sha256 前后一致
    assert hashlib.sha256(INPUT_JSON.read_bytes()).hexdigest() == sha_before
    assert payload["meta"]["read_only"] is True
    assert payload["meta"]["read_only_verified"] is True
    assert payload["meta"]["input"]["sha256_before"] == payload["meta"]["input"]["sha256_after"]
    # schema / 统计自洽
    assert set(payload) == {"meta", "stats", "merged_multi_source", "uncertain",
                            "merged_clusters", "clusters"}
    s = payload["stats"]
    assert s["concepts_after"] <= s["concepts_before"] == 1092
    assert s["multi_after_confirmed"] >= s["multi_before"]
    assert s["clusters"] == s["concepts_after"]
    assert sum(1 for c in payload["clusters"]) == s["clusters"]
    for c in payload["merged_clusters"]:
        for key in ("cluster_id", "display", "members", "n_members", "merge_reasons",
                    "status", "confidence_after", "merged_n_sources_lower",
                    "merged_n_sources_upper", "source_types_after"):
            assert key in c, key
        assert c["status"] in ("多源-确认", "多源-待确认", "单源")
    # 输出文件 + 可序列化 + MD 核心段
    assert out_j.is_file() and out_m.is_file()
    json.loads(out_j.read_text(encoding="utf-8"))
    md = out_m.read_text(encoding="utf-8")
    for section in ("合并前后对比", "合并簇样例", "未合并", "同义表", "下一步建议", "复现"):
        assert section in md, section
    # 真实数据上至少有 1 个合并簇（否则同义表/模糊规则失效）
    assert s["merged_clusters"] >= 1
