# -*- coding: utf-8 -*-
"""张松 PIT/数据口径自查报告测试（task-87）。

覆盖：
  1) schema/覆盖数：9 个量学因子、13 个可计算战法、17 项批量四关；
  2) 证据行有效性：文件存在、行号在范围内、函数定义可定位；
  3) PIT 断言：pass 项无未来函数风险命中、检查项为 True；T 日信号→T+1 入场口径；评估器未来收益仅作标签；
  4) 数据口径：17 项均有 source/adjust/universe/sample/is_oos 字段；known_deviation 与 C 级不可计算 4 项齐全；
  5) 审计只读：报告记录的源文件 sha256 == 当前文件 sha256（未修改策略代码）；MD 审核稿 markdown 结构。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
JSON_PATH = REPO / "research" / "reports" / "zhangsong_pit_audit_20261101.json"
MD_PATH = REPO / "research" / "reports" / "zhangsong_pit_audit_20261101.md"
FACTOR_LIB = REPO / "tools" / "research" / "factor_lib.py"
BATCH = REPO / "tools" / "research" / "book_batch_test.py"
BB_REPORT = REPO / "research" / "reports" / "book_batch_test_20261101.json"
FG_REPORT = REPO / "research" / "reports" / "four_gate_20261101.json"


def audit() -> dict:
    return json.loads(JSON_PATH.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1) schema / 覆盖数
# ---------------------------------------------------------------------------
def test_schema_and_counts():
    a = audit()
    for key in ("schema_version", "generated_at", "scope", "signal_entry", "factors",
                "strategies", "batch_17", "known_deviations", "summary", "conclusions", "disclaimer"):
        assert key in a, key
    s = a["summary"]
    assert s["factors_total"] == 9 and len(a["factors"]) == 9
    assert s["strategies_total"] == 13 and len(a["strategies"]) == 13
    assert s["batch_total"] == 17 and len(a["batch_17"]) == 17
    assert s["batch_computable"] == 13 and s["batch_uncomputable"] == 4
    # 判定枚举与汇总一致
    for group, total in (("factors", 9), ("strategies", 13)):
        verdicts = [x["pit_verdict"] for x in a[group]]
        assert len(verdicts) == total and set(verdicts) <= {"pass", "fail", "uncertain"}
    assert s["factors_pass"] == sum(1 for x in a["factors"] if x["pit_verdict"] == "pass")
    assert s["strategies_pass"] == sum(1 for x in a["strategies"] if x["pit_verdict"] == "pass")
    # 本批结论：无 fail
    assert s["factors_fail"] == 0 and s["strategies_fail"] == 0


# ---------------------------------------------------------------------------
# 2) 证据行有效性
# ---------------------------------------------------------------------------
def test_evidence_lines_valid():
    a = audit()
    for item in a["factors"] + a["strategies"]:
        ev = item.get("evidence")
        assert ev, f"{item.get('factor') or item.get('spec_id')} 缺证据"
        path = REPO / ev["file"]
        assert path.is_file(), ev["file"]
        lines = path.read_text(encoding="utf-8").splitlines()
        start, end = ev["lines"]
        assert 1 <= start <= end <= len(lines), ev
        fn = item.get("function")
        assert fn and f"def {fn}(" in lines[start - 1], f"{ev['file']}:{start} 不是 {fn} 定义"
    # 评估器证据：forward_car 行号内确有 shift(-1) 与 entry/exit
    ev = a["signal_entry"]["evaluator_evidence"]
    src = (REPO / ev["file"]).read_text(encoding="utf-8").splitlines()
    start, end = ev["lines"]
    block = "\n".join(src[start - 1:end])
    assert "shift(-1)" in block and "shift(-(1 + horizon))" in block


# ---------------------------------------------------------------------------
# 3) PIT 断言
# ---------------------------------------------------------------------------
def test_pit_assertions():
    a = audit()
    # 信号日与入场日分离：T 日信号 → T+1 入场
    rule = a["signal_entry"]["rule"]
    assert "T" in rule and "T+1" in rule and "入场" in rule
    # pass 项不得有未来函数风险命中，且核心检查为 True
    for item in a["factors"] + a["strategies"]:
        if item["pit_verdict"] == "pass":
            assert item["future_risk_hits"] == [], item.get("factor") or item.get("spec_id")
            checks = item["checks"]
            assert checks.get("uses_only_t_and_before") is True
            assert checks.get("no_future_label") is True
    # 因子全部 pass；战法全部 pass（本批审查结论）
    assert a["summary"]["factors_pass"] == 9
    assert a["summary"]["strategies_pass"] == 13
    # 批量四关：可计算 13 项 PIT pass；C 级 4 项 uncertain
    assert a["summary"]["batch_pit_pass"] == 13
    assert a["summary"]["batch_pit_uncertain"] == 4
    for b in a["batch_17"]:
        if not b["ran"]:
            assert b["pit_verdict"] == "uncertain" and b["uncomputable_reason"]


# ---------------------------------------------------------------------------
# 4) 数据口径书面说明完整性
# ---------------------------------------------------------------------------
def test_data_caliber_completeness():
    a = audit()
    required = ("data_source", "adjust", "universe", "sample_period", "is_oos")
    for b in a["batch_17"]:
        dc = b["data_caliber"]
        for key in required:
            assert dc.get(key), f"{b['spec_id']} 缺数据口径 {key}"
        assert b["data_caliber_verdict"] in ("pass", "fail", "uncertain")
        assert b["data_caliber_notes"]
    # 17 项与 book_batch 报告一致（spec 集合 + grade）
    bb = json.loads(BB_REPORT.read_text(encoding="utf-8-sig"))
    bb_specs = {x["spec_id"] for x in bb["results"]}
    assert {b["spec_id"] for b in a["batch_17"]} == bb_specs
    # C 级不可计算 4 项与报告一致
    uncomputable = {x["spec_id"] for x in bb["results"] if x.get("uncomputable_reason")}
    assert len(uncomputable) == 4
    assert {b["spec_id"] for b in a["batch_17"] if not b["ran"]} == uncomputable
    assert len(a["known_deviations"]) >= 5
    assert len(a["conclusions"]) >= 3


# ---------------------------------------------------------------------------
# 5) 审计只读（未修改策略代码）+ MD
# ---------------------------------------------------------------------------
def test_audit_only_and_markdown():
    a = audit()
    assert a["scope"]["factor_lib"]["sha256"] == sha(FACTOR_LIB)
    assert a["scope"]["strategy_impl"]["sha256"] == sha(BATCH)
    assert a["scope"]["batch_report"]["sha256"] == sha(BB_REPORT)
    assert a["scope"]["four_gate_report"]["sha256"] == sha(FG_REPORT)
    md = MD_PATH.read_text(encoding="utf-8")
    for marker in ("PIT / 数据口径自查", "PIT/前视审查（逐因子）", "逐战法",
                   "17 项批量四关", "数据口径书面说明", "交易制度处理核对",
                   "已知偏差清单", "G7 结论与建议", "不构成投资建议"):
        assert marker in md, marker
