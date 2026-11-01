# -*- coding: utf-8 -*-
"""report_panel_v3（截断/超时修复 + 精确页码 + 人工复核清单）契约测试 —— 不真调 LLM。

覆盖：
  1. num_predict≥1200 / deadline≥900 配置契约 + v3 prompt 页码硬约束；
  2. 精确页码口径（来源单元 page_start ±1）逐条标记越界，与 v2 宽并集口径对照；
  3. 页码有效率不足的角色 pass 票降权 → 候选降维；
  4. 解析失败修复重试：重试成功标记 llm_retry；重试耗尽降级 rule_fallback；
  5. 全链路 mock：候选判定三条件、v2 对比、达标结论、复核清单（☐ 勾选位）；
  6. 交叉模型整体失败 → 降级 4b 并标记 fallback_of；
  7. 输出 schema（review_checklist/comparison_v2/conclusion）+ 写盘 + CLI --help。

全部用例注入 mock chat_fn 或纯函数，不触网。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
V3_PATH = REPO / "tools" / "research" / "report_panel_v3.py"


def _load_v3():
    spec = importlib.util.spec_from_file_location("report_panel_v3_under_test", V3_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v3 = _load_v3()
ROLES = v3.ROLES

UNITS = [
    {"chapter": "图表目录", "concept": "M9 材料", "claim": "M9 级覆铜板支持 224Gbps 高速传输。",
     "evidence": "介电损耗低于 0.0015。", "page_start": 2, "page_end": 14},
    {"chapter": "图表目录", "concept": "硅微粉", "claim": "高端球形硅微粉由日本厂商垄断。",
     "evidence": "日本厂商份额超六成。", "page_start": 2, "page_end": 14},
    {"chapter": "图表目录", "concept": "CoWoP", "claim": "CoWoP 使 PCB 价值量达传统三倍。",
     "evidence": "单颗价值量约 600 美元。", "page_start": 2, "page_end": 14},
]
REPORT = {"report_id": "rp_AP202610071830180005", "date": "2026-10-07", "pages": 28,
          "units_total": len(UNITS), "path": str(REPO / "nonexistent")}


def _role_json(role, verdict="pass", page=2):
    return json.dumps({"role": role, "verdict": verdict, "reasons": ["理由甲", "理由乙"],
                       "key_facts": [{"fact": f"{role}关键事实", "page": page}],
                       "risks": ["风险甲"]}, ensure_ascii=False)


def _make_report_dir(tmp_path, name="rp_AP202610071830180005"):
    rdir = tmp_path / name
    rdir.mkdir()
    (rdir / "structure.json").write_text(json.dumps({"pages": 28}), encoding="utf-8")
    (rdir / "knowledge_units.jsonl").write_text(
        "\n".join(json.dumps(u, ensure_ascii=False) for u in UNITS), encoding="utf-8")
    return rdir


def chat_ok(prompt, model=None, timeout=None, num_predict=None, **kw):
    role = next(r for r in ROLES if f"你是{r}" in prompt)
    return _role_json(role, page=2)


# ---------------------------------------------------------------------------
# 1. 配置契约 + v3 prompt
# ---------------------------------------------------------------------------
def test_v3_fixes_config_and_prompt_page_constraint():
    # ① 截断/超时修复配置
    assert v3.NUM_PREDICT >= 1200
    assert v3.DEFAULT_DEADLINE >= 900
    assert v3.DEFAULT_RETRIES >= 1
    prompt = v3.build_role_prompt_v3("基本面分析师", REPORT, UNITS)
    assert "页码硬约束（v3）" in prompt
    assert "起始页码（2）" in prompt
    assert "避免被截断" in prompt
    # 修复重试 prompt 要求严格 JSON
    repair = v3.build_retry_prompt("风控官", REPORT, "上一轮截断的输出")
    assert "只输出一个严格 JSON 对象" in repair
    assert "风控官" in repair


# ---------------------------------------------------------------------------
# 2. 精确页码口径
# ---------------------------------------------------------------------------
def test_precise_page_validation_flags_out_of_range_and_compares_legacy():
    allowed = v3.precise_allowed_pages(UNITS)
    assert allowed == {1, 2, 3}  # page_start=2 ±1
    legacy = v3.legacy_allowed_pages(UNITS)
    assert 14 in legacy and 14 not in allowed
    role = {"role": "量化研究员", "verdict": "pass",
            "key_facts": [{"fact": "引用正文第 9 页事实", "page": 9},
                          {"fact": "起始页事实", "page": 2}], "risks": []}
    checked, summary = v3.apply_page_validation_v3([role], UNITS)
    assert checked[0]["page_check"]["invalid"] == 1
    assert checked[0]["page_check"]["invalid_details"][0]["page"] == 9
    assert summary["invalid_facts"] == 1
    assert summary["legacy_union_invalid_facts"] == 0  # v2 宽并集口径判为有效
    assert summary["pass"] is False
    assert summary["mode"] == "precise: 来源知识单元 page_start ±1（替代 v2 宽并集）"


def test_precise_page_validation_downweights_and_passes():
    roles = [{"role": "基本面分析师", "verdict": "pass",
              "key_facts": [{"fact": "越界事实", "page": 12}], "risks": []},
             {"role": "量化研究员", "verdict": "pass",
              "key_facts": [{"fact": "起始页事实", "page": 3}], "risks": []},
             {"role": "风控官", "verdict": "avoid",
              "key_facts": [{"fact": "起始页事实", "page": 2}], "risks": []}]
    checked, summary = v3.apply_page_validation_v3(roles, UNITS)
    assert summary["pass"] is False and summary["invalid_facts"] == 1
    assert checked[0]["page_check"]["downweighted"] is True
    eff = v3.v2.effective_roles(checked)
    assert eff[0]["verdict"] == "neutral"
    ok_roles = [{"role": r, "verdict": "pass", "key_facts": [{"fact": "f", "page": 2}], "risks": []}
                for r in ROLES]
    _checked_ok, summary_ok = v3.apply_page_validation_v3(ok_roles, UNITS)
    assert summary_ok["pass"] is True and summary_ok["legacy_union_invalid_facts"] == 0


# ---------------------------------------------------------------------------
# 3. 解析失败修复重试
# ---------------------------------------------------------------------------
def test_parse_failure_retry_succeeds(monkeypatch, tmp_path):
    _make_report_dir(tmp_path)
    calls = {"n": 0}

    def chat_retry(prompt, model=None, timeout=None, num_predict=None, **kw):
        role = next(r for r in ROLES if f"你是{r}" in prompt) if "上一轮输出片段" not in prompt else "风控官"
        if "上一轮输出片段" in prompt:  # 修复重试请求 → 返回合法 JSON
            calls["n"] += 1
            return _role_json(role, page=2)
        return "抱歉，输出被截断了：{\"role\": \"x\", \"verdict\": \"pas"  # 非法 JSON

    payload = v3.run_panel_v3(reports_n=1, chat_fn=chat_retry, max_workers=1, root=tmp_path,
                              v2_json=tmp_path / "no_v2.json", timeout=10, deadline_seconds=30,
                              num_predict=1600, retries=1)
    item = payload["panel"][0]
    assert payload["llm"]["retries"] >= 3 and payload["llm"]["retries_succeeded"] >= 3
    assert calls["n"] >= 3
    assert any(str(r.get("source", "")).startswith("llm_retry") for m in item["models"] for r in m["roles"])


def test_parse_failure_exhausted_falls_back(tmp_path):
    _make_report_dir(tmp_path)

    def chat_garbage(prompt, model=None, timeout=None, num_predict=None, **kw):
        return "完全不是 JSON"

    payload = v3.run_panel_v3(reports_n=1, chat_fn=chat_garbage, max_workers=1, root=tmp_path,
                              v2_json=tmp_path / "no_v2.json", timeout=10, deadline_seconds=30,
                              num_predict=1600, retries=1)
    roles = [r for m in payload["panel"][0]["models"] for r in m["roles"]]
    assert all(r["source"] == "rule_fallback" for r in roles)
    assert payload["fallback"]["used"] is True
    assert payload["fallback"]["rule_fallback_roles"] == 6


# ---------------------------------------------------------------------------
# 4. 全链路：候选 / v2 对比 / 结论 / 复核清单
# ---------------------------------------------------------------------------
def test_run_panel_v3_candidate_and_outputs_schema(tmp_path):
    _make_report_dir(tmp_path)
    payload = v3.run_panel_v3(reports_n=1, chat_fn=chat_ok, max_workers=1, root=tmp_path,
                              v2_json=tmp_path / "no_v2.json", timeout=10, deadline_seconds=30,
                              date_tag="test")
    assert payload["kind"] == "report_panel_v3" and payload["schema_version"] == 3
    item = payload["panel"][0]
    assert item["candidate"] is True
    assert item["page_validation"]["pass"] is True
    assert item["page_validation"]["issue_count"] == 0
    assert item["candidate_decision"]["conditions"] == {
        "models_pass_ge_2": True, "primary_pass_roles_ge_2": True, "page_validation_pass": True}
    assert payload["llm"]["num_predict"] >= 1200
    assert payload["llm"]["deadline_seconds"] == 30
    assert set(payload) >= {"review_checklist", "comparison_v2", "conclusion",
                            "page_validation_summary", "page_precision"}
    assert payload["comparison_v2"]["v2_available"] is False
    assert len(payload["review_checklist"]) == 1
    assert payload["review_checklist"][0]["facts"]
    review_md = v3.render_review_md(payload)
    assert "☐" in review_md and "人工复核清单" in review_md
    md = v3.render_markdown_v3(payload)
    assert "与 v2 对比" in md and "是否达到" in md
    roundtrip = json.loads(json.dumps(payload, ensure_ascii=False))
    assert roundtrip["conclusion"]["total"] == 5


def test_run_panel_v3_rejects_invalid_pages_and_builds_comparison(tmp_path):
    _make_report_dir(tmp_path)

    def chat_bad_page(prompt, model=None, timeout=None, num_predict=None, **kw):
        role = next(r for r in ROLES if f"你是{r}" in prompt)
        return _role_json(role, page=9)  # 精确口径下越界（page_start=2±1={1,2,3}）

    v2_ref = tmp_path / "v2.json"
    v2_ref.write_text(json.dumps({"candidates": [{"report_id": "rp_AP202610071830180005"}],
                                  "llm": {"rule_fallback_roles": 4, "model_fallbacks": 0},
                                  "page_validation_summary": {"invalid_facts": 0},
                                  "elapsed_seconds": 480.0,
                                  "reports": [{"report_id": "rp_AP202610071830180005"}]},
                                 ensure_ascii=False), encoding="utf-8")
    payload = v3.run_panel_v3(reports_n=1, chat_fn=chat_bad_page, max_workers=1, root=tmp_path,
                              v2_json=v2_ref, timeout=10, deadline_seconds=30)
    item = payload["panel"][0]
    assert item["page_validation"]["pass"] is False
    assert item["page_validation"]["issue_count"] == 6
    assert item["page_validation"]["legacy_union_issue_count"] == 0
    assert item["candidate"] is False
    assert payload["candidates"] == []
    cmp_v2 = payload["comparison_v2"]
    assert cmp_v2["v2_available"] is True
    assert cmp_v2["candidates"]["v2"] == 1 and cmp_v2["candidates"]["v3"] == 0
    assert cmp_v2["candidates"]["delta"] == -1
    assert cmp_v2["page_invalid"]["v3_precise"] == 6
    assert cmp_v2["page_invalid"]["v3_legacy_union"] == 0
    assert cmp_v2["elapsed_seconds"]["v2"] == 480.0
    # 复核清单包含被拦截研报与拒绝原因
    assert payload["review_checklist"][0]["is_candidate"] is False
    assert payload["review_checklist"][0]["reject_reasons"]
    assert payload["conclusion"]["passed"] <= payload["conclusion"]["total"]


def test_cross_model_falls_back_when_all_fail(tmp_path):
    _make_report_dir(tmp_path)

    def chat_fail_cross(prompt, model=None, timeout=None, num_predict=None, **kw):
        if model == "qwen3.6:35b-a3b":
            raise v3.v1.PanelError("Ollama 被占满（测试注入）")
        return chat_ok(prompt, model=model, timeout=timeout, num_predict=num_predict)

    payload = v3.run_panel_v3(reports_n=1, chat_fn=chat_fail_cross, max_workers=1, root=tmp_path,
                              v2_json=tmp_path / "no_v2.json", timeout=10, deadline_seconds=30)
    cross = payload["panel"][0]["models"][1]
    assert cross["model"] == "qwen3.5:4b"
    assert cross["is_fallback"] is True and cross["fallback_of"] == "qwen3.6:35b-a3b"
    assert payload["llm"]["model_fallbacks"] == 1
    assert payload["fallback"]["used"] is True


# ---------------------------------------------------------------------------
# 5. 写盘 + CLI
# ---------------------------------------------------------------------------
def test_write_outputs_v3_and_cli_help(tmp_path):
    _make_report_dir(tmp_path)
    payload = v3.run_panel_v3(reports_n=1, chat_fn=chat_ok, max_workers=1, root=tmp_path,
                              v2_json=tmp_path / "no_v2.json", timeout=10, deadline_seconds=30)
    jp, mp, rp = v3.write_outputs_v3(payload, tmp_path / "o" / "p.json",
                                     tmp_path / "o" / "p.md", tmp_path / "o" / "review.md")
    assert jp.exists() and mp.exists() and rp.exists()
    assert "☐" in rp.read_text(encoding="utf-8")
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3 and data["review_checklist"]
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V3_PATH), "run", "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0
    for flag in ("--reports", "--num-predict", "--deadline", "--retries", "--out-review"):
        assert flag in proc.stdout
