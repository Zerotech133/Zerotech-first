# -*- coding: utf-8 -*-
"""report_panel_v2（页码校验 + 双模型交叉 + 候选降维）契约测试 —— 不真调 LLM。

覆盖：
  1. 页码并集与 ±1 容差校验、缺页码/越界标记、低有效率角色 pass 票降权；
  2. key_facts 重合度匹配（去标点相似度）与双模型 verdict 一致率；
  3. 全链路 mock：双模型都 pass + 页码通过 → 进候选；单模型 pass → 不进；
  4. 页码校验不通过 → 即使双模型 pass 也不进候选；
  5. 交叉模型失败 → 自动降级 cross_fallback 并标记 fallback_of；
  6. 主模型页码有效率低 → pass 票降权导致候选降维；
  7. 输出 schema（reports/panel/candidates/comparison_v1/fallback）+ Markdown 渲染 + CLI --help；
  8. 缺 v1 基准文件时 comparison 优雅降级、空目录容错。

全部用例注入 mock chat_fn 或直接测纯函数，不触网。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
V2_PATH = REPO / "tools" / "research" / "report_panel_v2.py"


def _load_v2():
    spec = importlib.util.spec_from_file_location("report_panel_v2_under_test", V2_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v2 = _load_v2()
ROLES = v2.ROLES

UNITS = [
    {"chapter": "图表目录", "concept": "M9 材料", "claim": "M9 级覆铜板支持 224Gbps 高速传输。",
     "evidence": "介电损耗低于 0.0015。", "page_start": 2, "page_end": 4},
    {"chapter": "图表目录", "concept": "硅微粉", "claim": "高端球形硅微粉由日本厂商垄断，国产替代空间大。",
     "evidence": "日本厂商份额超六成。", "page_start": 5, "page_end": 5},
    {"chapter": "图表目录", "concept": "CoWoP", "claim": "CoWoP 使单颗 GPU 配套 PCB 价值量达传统方案三倍。",
     "evidence": "单颗价值量约 600 美元。", "page_start": 7, "page_end": 8},
]
REPORT = {"report_id": "rp_AP202610071830180005", "date": "2026-10-07", "pages": 9,
          "units_total": len(UNITS), "path": str(REPO / "nonexistent")}


def _role_payload(role, verdict="pass", page=3):
    return {"role": role, "verdict": verdict, "reasons": ["理由甲", "理由乙"],
            "key_facts": [{"fact": f"{role}关键事实", "page": page}], "risks": ["风险甲"]}


def _fake_model_json(role, verdict="pass", page=3):
    return json.dumps(_role_payload(role, verdict, page), ensure_ascii=False)


def _make_report_dir(tmp_path, units=None, name="rp_AP202610071830180005", pages=9):
    rdir = tmp_path / name
    rdir.mkdir()
    (rdir / "structure.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
    units = UNITS if units is None else units
    (rdir / "knowledge_units.jsonl").write_text(
        "\n".join(json.dumps(u, ensure_ascii=False) for u in units), encoding="utf-8")
    return rdir


def _chat_ok(prompt, model=None, timeout=None, verdict="pass", page=3):
    role = next(r for r in ROLES if f"你是{r}" in prompt)
    return _fake_model_json(role, verdict, page)


# ---------------------------------------------------------------------------
# 1. 页码校验
# ---------------------------------------------------------------------------
def test_allowed_pages_union_and_tolerance_validation():
    pages = v2.allowed_pages(UNITS)
    assert pages == {2, 3, 4, 5, 7, 8}
    role = _role_payload("基本面分析师", page=6)          # 6 不在并集，但 6±1 含 5/7 → 容差内有效
    check = v2.validate_role_facts(role, pages, tolerance=1)
    assert check["valid"] == 1 and check["invalid"] == 0
    role_out = _role_payload("基本面分析师", page=99)
    check_out = v2.validate_role_facts(role_out, pages, tolerance=1)
    assert check_out["invalid"] == 1 and check_out["valid_ratio"] == 0.0
    assert check_out["invalid_details"][0]["page"] == 99
    role_none = _role_payload("风控官", page=None)
    check_none = v2.validate_role_facts(role_none, pages, tolerance=1)
    assert check_none["missing"] == 1


def test_apply_page_validation_and_downweight():
    roles = [_role_payload("基本面分析师", "pass", page=99),   # 页码全错
             _role_payload("量化研究员", "pass", page=3),
             _role_payload("风控官", "pass", page=3)]
    checked, summary = v2.apply_page_validation(roles, UNITS)
    assert summary["pass"] is False and summary["invalid_facts"] == 1
    assert summary["invalid_details"][0]["role"] == "基本面分析师"
    assert checked[0]["page_check"]["downweighted"] is True
    eff = v2.effective_roles(checked)
    assert eff[0]["verdict"] == "neutral" and eff[0]["downweighted_from"] == "pass"
    assert eff[1]["verdict"] == "pass"
    # 空 key_facts → 校验不通过（无从验证）
    empty, summary_empty = v2.apply_page_validation([_role_payload("风控官", page=None)], [])
    assert summary_empty["pass"] is False
    ok, summary_ok = v2.apply_page_validation(roles[1:], UNITS)
    assert summary_ok["pass"] is True and summary_ok["invalid_facts"] == 0


# ---------------------------------------------------------------------------
# 2. 多模型交叉
# ---------------------------------------------------------------------------
def test_match_key_facts_similarity():
    facts_a = [{"fact": "M9 级覆铜板支持 224Gbps 高速传输，介电损耗低于 0.0015", "page": 2},
               {"fact": "高端球形硅微粉由日本厂商垄断", "page": 5}]
    facts_b = [{"fact": "M9级覆铜板支持224Gbps高速传输，介电损耗低于0.0015", "page": 3},
               {"fact": "白酒动销磨底，大众品稳健", "page": 8}]
    res = v2.match_key_facts(facts_a, facts_b)
    assert res["matched_count"] == 1
    assert res["overlap_ratio"] == pytest.approx(0.5)
    assert res["matched_pairs"][0]["page_b"] == 3
    assert v2.match_key_facts([], [])["overlap_ratio"] == 0.0


def test_compare_models_verdict_agreement_and_overlap():
    roles_a = [_role_payload("基本面分析师", "pass"), _role_payload("量化研究员", "avoid"),
               _role_payload("风控官", "pass")]
    roles_b = [_role_payload("基本面分析师", "pass"), _role_payload("量化研究员", "pass"),
               _role_payload("风控官", "pass")]
    run_a = {"model": "m-a", "roles": roles_a, "aggregate": {"verdict": "pass"}}
    run_b = {"model": "m-b", "roles": roles_b, "aggregate": {"verdict": "pass"}}
    cross = v2.compare_models(run_a, run_b)
    assert cross["role_agreement_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert cross["matched_roles"] == ["基本面分析师", "风控官"]
    assert cross["verdict_agreement"] is True and cross["both_pass"] is True
    assert cross["model_verdicts"] == {"m-a": "pass", "m-b": "pass"}
    assert set(cross["role_verdicts"]["m-a"]) == set(ROLES)


# ---------------------------------------------------------------------------
# 3. 全链路：双模型 mock
# ---------------------------------------------------------------------------
def test_run_panel_v2_candidate_when_both_models_pass(tmp_path):
    _make_report_dir(tmp_path)
    payload = v2.run_panel_v2(reports_n=1, chat_fn=_chat_ok, max_workers=1, date_tag="test",
                              root=tmp_path, v1_json=tmp_path / "no_v1.json",
                              timeout=10, deadline_seconds=30)
    assert payload["kind"] == "report_panel_v2" and payload["schema_version"] == 2
    assert len(payload["panel"]) == 1
    item = payload["panel"][0]
    assert item["candidate"] is True
    assert item["candidate_decision"]["conditions"] == {
        "models_pass_ge_2": True, "primary_pass_roles_ge_2": True, "page_validation_pass": True}
    assert len(item["models"]) == 2
    assert {r["model"] for r in item["models"]} == {"qwen3.5:9b", "qwen3.6:35b-a3b"}
    assert payload["comparison_v1"]["v1_available"] is False
    assert payload["selection"]["reports_selected"] == 1
    # JSON 可序列化 + Markdown 渲染
    roundtrip = json.loads(json.dumps(payload, ensure_ascii=False))
    md = v2.render_markdown_v2(roundtrip)
    assert "研报评审面板 v2" in md and "候选判定" in md and "页码校验" in md


def test_run_panel_v2_rejects_when_one_model_avoids(tmp_path):
    _make_report_dir(tmp_path)

    def chat_cross_avoid(prompt, model=None, timeout=None):
        role = next(r for r in ROLES if f"你是{r}" in prompt)
        verdict = "avoid" if model == "qwen3.6:35b-a3b" else "pass"
        return _fake_model_json(role, verdict)

    payload = v2.run_panel_v2(reports_n=1, chat_fn=chat_cross_avoid, max_workers=1,
                              root=tmp_path, v1_json=tmp_path / "no_v1.json",
                              timeout=10, deadline_seconds=30)
    item = payload["panel"][0]
    assert item["candidate"] is False
    assert item["candidate_decision"]["conditions"]["models_pass_ge_2"] is False
    assert payload["candidates"] == []


def test_run_panel_v2_rejects_when_page_invalid(tmp_path):
    _make_report_dir(tmp_path)

    def chat_bad_pages(prompt, model=None, timeout=None):
        role = next(r for r in ROLES if f"你是{r}" in prompt)
        return _fake_model_json(role, "pass", page=999)

    payload = v2.run_panel_v2(reports_n=1, chat_fn=chat_bad_pages, max_workers=1,
                              root=tmp_path, v1_json=tmp_path / "no_v1.json",
                              timeout=10, deadline_seconds=30)
    item = payload["panel"][0]
    assert item["page_validation"]["pass"] is False
    assert item["page_validation"]["issue_count"] == 6
    assert item["candidate_decision"]["conditions"]["page_validation_pass"] is False
    assert item["candidate"] is False
    # 低有效率 → pass 票降权，主模型 effective pass 角色数不足
    assert item["candidate_decision"]["primary_pass_roles"] == 0


# ---------------------------------------------------------------------------
# 4. 交叉模型失败 → 降级 4b
# ---------------------------------------------------------------------------
def test_cross_model_falls_back_to_light_model(tmp_path):
    _make_report_dir(tmp_path)

    def chat_fail_cross(prompt, model=None, timeout=None):
        if model == "qwen3.6:35b-a3b":
            raise v2.v1.PanelError("Ollama 被占满（测试注入）")
        return _chat_ok(prompt, model=model)

    payload = v2.run_panel_v2(reports_n=1, chat_fn=chat_fail_cross, max_workers=1,
                              root=tmp_path, v1_json=tmp_path / "no_v1.json",
                              timeout=10, deadline_seconds=30)
    item = payload["panel"][0]
    cross = item["models"][1]
    assert cross["model"] == "qwen3.5:4b"
    assert cross["is_fallback"] is True and cross["fallback_of"] == "qwen3.6:35b-a3b"
    assert payload["llm"]["model_fallbacks"] == 1
    assert payload["fallback"]["used"] is True
    assert item["fallback_notes"]


# ---------------------------------------------------------------------------
# 5. 空目录 / 缺 v1 / 写盘 / CLI
# ---------------------------------------------------------------------------
def test_empty_root_and_missing_v1_graceful(tmp_path):
    payload = v2.run_panel_v2(reports_n=2, chat_fn=_chat_ok, root=tmp_path / "nope",
                              v1_json=tmp_path / "no_v1.json", timeout=10, deadline_seconds=10)
    assert payload["panel"] == [] and payload["candidates"] == []
    assert payload["comparison_v1"]["v1_available"] is False
    assert payload["fallback"]["notes"]
    assert v2.load_v1_candidates(tmp_path / "no_v1.json")["available"] is False


def test_write_outputs_v2_and_cli_help(tmp_path):
    payload = v2.run_panel_v2(reports_n=1, chat_fn=_chat_ok, max_workers=1, root=tmp_path,
                              v1_json=tmp_path / "no_v1.json", timeout=10, deadline_seconds=10)
    jp, mp = v2.write_outputs_v2(payload, tmp_path / "out" / "p.json", tmp_path / "out" / "p.md")
    assert jp.exists() and mp.exists()
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert set(data) >= {"kind", "schema_version", "models", "llm", "selection", "reports",
                         "panel", "candidates", "comparison_v1", "fallback", "elapsed_seconds"}
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V2_PATH), "run", "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0
    for flag in ("--reports", "--primary", "--cross", "--cross-fallback"):
        assert flag in proc.stdout
