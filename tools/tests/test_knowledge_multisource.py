# -*- coding: utf-8 -*-
"""tools/tests/test_knowledge_multisource.py · 多源交叉验证（W1-1）单元测试。

覆盖（任务验收要求）：
  1. **概念归一化**：去 Markdown/疑问前缀/泛化后缀、保守同义词合并（涨停板→涨停，但伏击涨停不误并）；
  2. **聚类与置信判定**：≥2 独立来源且答案一致→高；≥2 源措辞不同→中；单源→低；同来源多条只算 1 源；
  3. **冲突识别**：方向相反 / 否定-肯定表述 / 阈值方向相反 → 待仲裁清单；一致且无冲突时不误报；
  4. **只读 + 输出**：用 tmp 目录 fixture 跑完整 pipeline，输入 sha256 前后一致、只生成两个输出文件；
  5. **真实默认输入集成**：默认输入存在时跑一遍（输出到 tmp），schema/置信分布自洽（缺文件自动 skip）。

不写真实报告、不改输入；整体 < 10s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_knowledge_multisource.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import knowledge_multisource as km  # noqa: E402

MODULE = ROOT / "tools" / "kb_corpus" / "knowledge_multisource.py"


# ------------------------------------------------------------------ 工具
def _rec(concept: str, answer: str, source_type: str = "blogger",
         source_key: str = "A") -> dict:
    return {"concept": concept, "answer": answer, "source_type": source_type,
            "source_key": source_key,
            "source_label": f"{km.SOURCE_TYPE_LABEL.get(source_type, source_type)}·{source_key}",
            "origin": f"test:{source_key}", "evidence": ""}


def _assess(records: list[dict], strict: bool = False) -> list[dict]:
    clusters = km.cluster_records(records, aliases={}, strict=strict)
    return [km.assess_cluster(c) for c in clusters.values()]


def _write_json(path: Path, doc) -> str:
    text = json.dumps(doc, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 1. 概念归一化
def test_normalize_concept_and_conservative_synonyms():
    # 去 Markdown / 疑问前缀 / 标点
    assert km.normalize_concept("**什么是「价升量缩战法」？**") == "价升量缩"
    # 泛化后缀只在 strict=False 时去掉
    assert km.normalize_concept("价升量缩战法", strict=True) == "价升量缩战法"
    assert km.normalize_concept("价升量缩战法") == "价升量缩"
    # 保守同义词：涨停板→涨停（长度接近、字符重合高）
    aliases = km.load_synonym_aliases()
    assert aliases, "synonyms_v2.json 应能加载出别名（缺文件时此断言失败）"
    assert km.normalize_concept("涨停板", aliases) == km.normalize_concept("涨停", aliases)
    # 不同概念不误并：伏击涨停（战法）≠ 涨停（价格现象）
    assert km.normalize_concept("伏击涨停", aliases) != km.normalize_concept("涨停", aliases)
    assert km.normalize_concept("涨停基因", aliases) != km.normalize_concept("涨停", aliases)
    # 一致性函数：同句复述→一致；不同措辞→不一致
    same = km.answer_consistency("二十日均线上穿六十日均线时买入，属于趋势确认信号。",
                                 "二十日均线上穿六十日均线时买入，属于趋势确认信号。")
    assert same["consistent"] and same["similarity"] > 0.9
    diff = km.answer_consistency("黄金分割常用 0.382、0.618 与 0.764 作为支撑压力位。",
                                 "仓位管理建议底仓一两成试探，突破后加仓五六成。")
    assert not diff["consistent"]


# ------------------------------------------------------------------ 2. 聚类与置信
def test_cluster_and_confidence_levels():
    answer = "量柱是股市密探，倍量柱代表主力增仓，需结合价柱位置判断。"
    records = [
        _rec("量柱", answer, "book", "伏击涨停"),
        _rec("量柱", answer, "book", "涨停密码"),
        # 同来源多条只算 1 个独立来源
        _rec("仓位管理", "底仓一两成试探，突破压力后加仓五六成。", "blogger", "乔木"),
        _rec("仓位管理", "控制仓位，不要轻易追高，等待反转信号。", "blogger", "一修"),
        _rec("仓位管理", "仓位管理还有第三种说法。", "blogger", "一修"),
        _rec("独有概念", "单源概念无法交叉验证，只能标低置信。", "blogger", "K哥"),
    ]
    got = {a["concept"]: a for a in _assess(records)}
    assert got["量柱"]["confidence"] == "高"
    assert got["量柱"]["n_sources"] == 2 and got["量柱"]["n_source_types"] == 1
    assert got["仓位管理"]["n_sources"] == 2            # 一修 3 条记录 → 1 源
    assert got["仓位管理"]["confidence"] == "中"
    assert got["独有概念"]["confidence"] == "低" and got["独有概念"]["n_sources"] == 1
    # 答案内容完全不同的多源 → 措辞不同（中）
    assert got["仓位管理"]["consistency"]["consistent"] is False


# ------------------------------------------------------------------ 3. 冲突识别
def test_conflict_detection():
    # 方向相反
    c1 = _assess([
        _rec("突破形态", "该形态看多，建议买入加仓。", "blogger", "A"),
        _rec("突破形态", "该形态看空，建议卖出减仓。", "blogger", "B"),
    ])
    assert c1[0]["conflicts"], "方向相反应被识别"
    assert c1[0]["conflicts"][0]["conflict_type"] == "方向相反"
    assert c1[0]["confidence"] == "中"                   # 冲突存在 → 不能是高
    # 否定 vs 肯定
    c2 = _assess([
        _rec("止损纪律", "必须严格止损，务必执行。", "blogger", "A"),
        _rec("止损纪律", "不要止损，无需设置止损位。", "blogger", "B"),
    ])
    assert any(c["conflict_type"] == "否定/肯定表述冲突" for c in c2[0]["conflicts"])
    # 相反事实（阈值方向相反）
    c3 = _assess([
        _rec("涨幅过滤", "当日涨幅至少 20% 才有效。", "blogger", "A"),
        _rec("涨幅过滤", "当日涨幅最多 10% 才有效。", "blogger", "B"),
    ])
    assert any("相反事实" in c["conflict_type"] for c in c3[0]["conflicts"])
    # 一致时不得误报冲突
    c4 = _assess([
        _rec("止损纪律", "收盘跌破形态柱实底止损，破底为亡。", "book", "A"),
        _rec("止损纪律", "收盘跌破形态柱实底止损，破底为亡。", "book", "B"),
    ])
    assert c4[0]["conflicts"] == []
    assert c4[0]["confidence"] == "高"


# ------------------------------------------------------------------ 4. 只读 + 输出
def test_run_read_only_and_outputs(tmp_path):
    units = {"units": [
        {"source": "书籍", "book": "伏击涨停(2)", "concept": "倍量柱",
         "claim": "倍量柱是当日量柱超过前一日两倍，代表主力增仓行为。", "evidence": "p1"},
        {"source": "研报", "book": "rp_APTEST0001", "concept": "倍量柱",
         "claim": "倍量柱是当日量柱超过前一日两倍，代表主力增仓行为。", "evidence": "p2"},
    ]}
    cards = {"cards": [
        {"concept": "倍量柱", "a": "倍量柱是当日量柱超过前一日两倍，代表主力增仓行为。",
         "source": "视频号学习_8博主 · 20261103_测试博主.md"},
        {"concept": "独家概念", "a": "这个说法只有一位博主提过，需要后续验证。",
         "source": "视频号学习_8博主 · 20261103_测试博主.md"},
    ]}
    pu, pc = tmp_path / "units.json", tmp_path / "cards.json"
    hu, hc = _write_json(pu, units), _write_json(pc, cards)
    out_j, out_m = tmp_path / "out.json", tmp_path / "out.md"

    payload = km.run([("units", pu), ("cards", pc)], out_json=out_j, out_md=out_m)

    # 只生成两个输出文件；输入 sha256 前后一致（只读）
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cards.json", "out.json", "out.md", "units.json"]
    assert hashlib.sha256(pu.read_bytes()).hexdigest() == hu
    assert hashlib.sha256(pc.read_bytes()).hexdigest() == hc
    meta = payload["meta"]
    assert meta["read_only"] is True and meta["read_only_verified"] is True
    assert all(i["unchanged"] for i in meta["inputs"])
    assert all(i["sha256"] == i["sha256_after"] for i in meta["inputs"] if i["exists"])
    # schema
    assert set(payload) == {"meta", "multi_source_concepts", "concepts", "conflicts"}
    for key in ("report", "version", "generated_at", "inputs", "records_used",
                "concepts_total", "multi_source_concepts", "confidence_counts",
                "conflict_count", "source_type_counts", "limitations"):
        assert key in meta, key
    assert out_j.is_file() and out_m.is_file()
    doc = json.loads(out_j.read_text(encoding="utf-8"))
    assert doc["meta"]["records_used"] == 4          # units 2 + cards 2（去重只在同来源内生效）
    # 书 + 研报 两源同答案 → 高；博主单源 → 低
    multi = {a["concept"]: a for a in doc["multi_source_concepts"]}
    assert multi["倍量柱"]["confidence"] == "高" and multi["倍量柱"]["n_sources"] == 3
    assert "独家概念" not in multi
    # 每概念条目 schema
    for a in doc["multi_source_concepts"]:
        for key in ("concept", "concept_key", "n_sources", "source_types", "confidence",
                    "consistency", "sources", "conflicts"):
            assert key in a, key
    md = out_m.read_text(encoding="utf-8")
    for section in ("多源概念清单", "置信分布", "待仲裁清单", "口径与局限", "复现"):
        assert section in md, section


# ------------------------------------------------------------------ 5. 真实默认输入集成
def test_default_inputs_integration(tmp_path):
    existing = [(kind, path) for kind, path in km.DEFAULT_INPUTS if Path(path).is_file()]
    if not existing:
        pytest.skip("默认输入文件均不存在（跳过集成测试）")
    out_j, out_m = tmp_path / "real.json", tmp_path / "real.md"
    payload = km.run(existing, out_json=out_j, out_md=out_m)
    meta = payload["meta"]
    assert meta["read_only_verified"] is True
    assert meta["records_used"] > 0
    assert meta["concepts_total"] >= meta["multi_source_concepts"]
    cc = meta["confidence_counts"]
    assert sum(cc.values()) == meta["concepts_total"]
    assert cc["高"] + cc["中"] == meta["multi_source_concepts"]
    assert set(meta["source_type_counts"]) <= {"blogger", "book", "research_report", "other"}
    # JSON 可序列化且 MD 有核心段
    json.dumps(payload, ensure_ascii=False)
    md = out_m.read_text(encoding="utf-8")
    assert "知识多源交叉验证" in md and "只读" in md
