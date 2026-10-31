# -*- coding: utf-8 -*-
"""report_panel（多智能体研报评审面板）契约测试 —— 不真调 LLM。

覆盖：
  1. prompt 构建器：三角色 + 严格 JSON schema + 页码事实 + 未知角色报错；
  2. LLM 输出 JSON 容错解析：代码围栏/前后杂文/尾逗号/中文 verdict/缺字段修复/非法输出报错；
  3. 投票聚合：pass≥2 进候选、avoid 多数、中性不出候选、空输入容错；
  4. 全流程 schema（注入 mock chat_fn）：reports/panel/candidates/fallback 齐全；
  5. 降级路径：LLM 异常/JSON 不可解析 → rule_fallback，fallback 标记与 notes；
  6. 缺数据容错：空目录/无 knowledge_units.jsonl/缺页码；
  7. 规则式摘要确定性 + Markdown 渲染 + 标题兜底 + CLI --help。

全部用例使用 mock chat_fn 或纯函数，不触网、不调 Ollama。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PANEL_PATH = REPO / "tools" / "research" / "report_panel.py"


def _load_panel():
    spec = importlib.util.spec_from_file_location("report_panel_under_test", PANEL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rp = _load_panel()

ROLE_JSON = {
    "基本面分析师": {"role": "基本面分析师", "verdict": "pass",
                 "reasons": ["驱动链条清晰", "竞争格局有数据支撑"],
                 "key_facts": [{"fact": "M9 覆铜板支持 224Gbps", "page": 2}],
                 "risks": ["高端材料国产化不及预期"]},
    "量化研究员": {"role": "量化研究员", "verdict": "pass",
                "reasons": ["有同比/占比等可量化指标", "口径可回测"],
                "key_facts": [{"fact": "价值量为传统方案三倍", "page": 5}],
                "risks": ["样本期较短"]},
    "风控官": {"role": "风控官", "verdict": "neutral",
            "reasons": ["风险有披露但缺反面证据", "下行空间未量化"],
            "key_facts": [{"fact": "国产替代存在不确定性", "page": 3}],
            "risks": ["政策与垄断格局变化"]},
}

UNITS = [
    {"chapter": "图表目录", "concept": "M9 级覆铜板", "claim": "M9 级覆铜板支持 224Gbps 高速传输与高层 PCB 量产。",
     "evidence": "英伟达 Rubin 平台采用 M9 体系，介电损耗低于 0.0015。", "page_start": 2, "page_end": 3},
    {"chapter": "图表目录", "concept": "球形硅微粉", "claim": "高端球形硅微粉由日本厂商垄断，国产替代空间巨大。",
     "evidence": "日本厂商合计份额超六成，2025 年进口占比仍高。", "page_start": 3, "page_end": 3},
    {"chapter": "图表目录", "concept": "CoWoP 方案", "claim": "CoWoP 方案使单颗 GPU 配套 PCB 价值量达到传统方案三倍。",
     "evidence": "单颗 GPU 配套 PCB 价值量约 600 美元。", "page_start": 5, "page_end": 5},
]


def _report(report_id="rp_AP202610071830180005"):
    return {"report_id": report_id, "date": "2026-10-07", "pages": 28,
            "units_total": len(UNITS), "path": str(REPO / "nonexistent")}


def _fake_chat_ok(prompt, model=None, timeout=None):
    """按 prompt 中出现的角色名返回各自 JSON（模拟 LLM 成功）。"""
    for role, payload in ROLE_JSON.items():
        if f"你是{role}" in prompt:
            return json.dumps(payload, ensure_ascii=False)
    raise AssertionError("prompt 未包含任何已知角色")


# ---------------------------------------------------------------------------
# 1. prompt 构建器
# ---------------------------------------------------------------------------
def test_build_role_prompt_contains_role_schema_and_pages():
    for role in rp.ROLES:
        prompt = rp.build_role_prompt(role, _report(), UNITS)
        assert f"你是{role}" in prompt
        assert "rp_AP202610071830180005" in prompt
        for key in ("verdict", "reasons", "key_facts", "risks", "pass|avoid|neutral"):
            assert key in prompt
        assert "p.2" in prompt and "M9 级覆铜板" in prompt
        assert "只输出一个严格 JSON 对象" in prompt
    with pytest.raises(rp.PanelError):
        rp.build_role_prompt("不存在的角色", _report(), UNITS)


def test_build_prompt_handles_empty_units():
    prompt = rp.build_role_prompt("风控官", _report(), [])
    assert "无可用知识单元" in prompt
    assert "数据不足" in prompt


# ---------------------------------------------------------------------------
# 2. JSON 容错解析
# ---------------------------------------------------------------------------
def test_parse_role_json_tolerates_fences_prose_and_trailing_commas():
    body = json.dumps(ROLE_JSON["基本面分析师"], ensure_ascii=False)
    body_with_trailing = body.replace('"risks": [', '"risks": [').replace("]}\n", "]}\n")
    loose = body_with_trailing[:-1] + ",}"  # 补一个尾逗号
    text = "好的，下面是评审结果：\n```json\n" + loose + "\n```\n以上，请复核。"
    parsed = rp.parse_role_json(text, "基本面分析师")
    assert parsed["role"] == "基本面分析师"
    assert parsed["verdict"] == "pass"
    assert len(parsed["reasons"]) == 2
    assert parsed["key_facts"][0]["page"] == 2
    assert parsed["repaired"] is False


def test_parse_role_json_normalizes_chinese_verdict_and_missing_fields():
    raw = '{"verdict": "观望", "reasons": "样本口径缺失；结论不可回测", "key_facts": []}'
    parsed = rp.parse_role_json(raw, "量化研究员")
    assert parsed["verdict"] == "neutral"
    assert parsed["reasons"] == ["样本口径缺失", "结论不可回测"]
    assert parsed["repaired"] is True  # 缺 risks 被修复
    assert parsed["risks"]  # 有占位风险

    raw_pass = '{"verdict": "推荐", "reasons": ["a", "b"], "key_facts": [{"fact": "x", "page": "第3页"}]}'
    parsed_pass = rp.parse_role_json(raw_pass, "基本面分析师")
    assert parsed_pass["verdict"] == "pass"
    assert parsed_pass["key_facts"][0]["page"] == 3


def test_parse_role_json_invalid_raises():
    with pytest.raises(rp.PanelParseError):
        rp.parse_role_json("完全不是 JSON 的一段话", "风控官")
    with pytest.raises(rp.PanelParseError):
        rp.parse_role_json('{"verdict": ', "风控官")


# ---------------------------------------------------------------------------
# 3. 投票聚合
# ---------------------------------------------------------------------------
def _roles(verdicts):
    return [{"role": role, "verdict": v} for role, v in zip(rp.ROLES, verdicts)]


def test_aggregate_votes_and_candidate_pool():
    two_pass = rp.aggregate_roles(_roles(["pass", "pass", "neutral"]))
    assert two_pass["verdict"] == "pass" and two_pass["candidate"] is True
    assert two_pass["votes"] == {"pass": 2, "avoid": 0, "neutral": 1}

    one_pass = rp.aggregate_roles(_roles(["pass", "neutral", "avoid"]))
    assert one_pass["verdict"] == "neutral" and one_pass["candidate"] is False

    avoid_major = rp.aggregate_roles(_roles(["avoid", "avoid", "pass"]))
    assert avoid_major["verdict"] == "avoid" and avoid_major["candidate"] is False

    empty = rp.aggregate_roles([])
    assert empty["votes"] == {"pass": 0, "avoid": 0, "neutral": 0}
    assert empty["verdict"] == "neutral" and empty["candidate"] is False


# ---------------------------------------------------------------------------
# 4. 全流程 schema（mock LLM）
# ---------------------------------------------------------------------------
def test_run_panel_schema_with_mock_llm(tmp_path):
    # 空目录：不崩溃、结构完整、有说明
    payload = rp.run_panel(root=REPO / "nonexistent_root", reports_n=1,
                           chat_fn=_fake_chat_ok, rule_only=False, date_tag="20261101")
    assert payload["reports"] == [] and payload["panel"] == [] and payload["candidates"] == []
    assert payload["fallback"]["notes"]

    # 构造真实可读研报目录，mock LLM 全成功 → 全链路 schema + 候选池
    rdir = tmp_path / "rp_AP202610071830180005"
    rdir.mkdir()
    (rdir / "structure.json").write_text(json.dumps({"pages": 28}), encoding="utf-8")
    (rdir / "knowledge_units.jsonl").write_text(
        "\n".join(json.dumps(u, ensure_ascii=False) for u in UNITS), encoding="utf-8")
    payload = rp.run_panel(root=tmp_path, reports_n=1, chat_fn=_fake_chat_ok,
                           rule_only=False, max_workers=1, date_tag="20261101")
    assert set(payload) >= {"schema_version", "generated_at", "model", "llm", "selection",
                            "reports", "panel", "candidates", "fallback", "elapsed_seconds"}
    assert len(payload["reports"]) == 1 and len(payload["panel"]) == 1
    assert payload["kind"] == "report_panel_mvp"
    assert isinstance(payload["panel"], list)
    assert payload["reports"][0]["report_id"] == "rp_AP202610071830180005"
    roles = payload["panel"][0]["roles"]
    assert len(roles) == 3 and all(r["source"] == "llm" for r in roles)
    assert payload["llm"]["llm_ok_roles"] == 3 and payload["llm"]["rule_fallback_roles"] == 0
    # mock 数据里 2 个 pass + 1 neutral → 候选池
    assert payload["panel"][0]["aggregate"]["candidate"] is True
    assert [c["report_id"] for c in payload["candidates"]] == ["rp_AP202610071830180005"]
    assert payload["candidates"][0]["pass_votes"] == 2
    # 序列化 + Markdown 冒烟（候选池列名出现）
    roundtrip = json.loads(json.dumps(payload, ensure_ascii=False))
    assert roundtrip["candidates"]
    md = rp.render_markdown(roundtrip)
    assert "rp_AP202610071830180005" in md and "pass 票 ≥ 2" in md


# ---------------------------------------------------------------------------
# 5. LLM 失败 → 规则式降级
# ---------------------------------------------------------------------------
def test_run_panel_falls_back_when_llm_fails_or_garbles(monkeypatch, tmp_path):
    def bad_chat(prompt, model=None, timeout=None):
        raise rp.PanelError("Ollama 被占满（测试注入）")

    # 需要一个真实可读的研报目录：用 tmp 构造
    rdir = tmp_path / "rp_AP202610071830180009"
    rdir.mkdir()
    (rdir / "structure.json").write_text(json.dumps({"pages": 9}), encoding="utf-8")
    (rdir / "knowledge_units.jsonl").write_text(
        "\n".join(json.dumps(u, ensure_ascii=False) for u in UNITS), encoding="utf-8")

    payload = rp.run_panel(root=tmp_path, reports_n=1, chat_fn=bad_chat,
                           rule_only=False, max_workers=1, date_tag="20261101")
    assert len(payload["panel"]) == 1
    roles = payload["panel"][0]["roles"]
    assert len(roles) == 3
    assert all(r["source"] == "rule_fallback" for r in roles)
    assert all(r["verdict"] in rp.VERDICTS for r in roles)
    assert payload["fallback"]["used"] is True
    assert payload["fallback"]["llm_unavailable_roles"] == 3
    assert payload["llm"]["llm_ok_roles"] == 0
    assert any("规则式" in n for n in payload["fallback"]["notes"])
    # 聚合仍成立且结构完整
    assert payload["panel"][0]["aggregate"]["votes"]["pass"] + \
           payload["panel"][0]["aggregate"]["votes"]["avoid"] + \
           payload["panel"][0]["aggregate"]["votes"]["neutral"] == 3

    # JSON 不可解析也应降级而非崩溃
    payload2 = rp.run_panel(root=tmp_path, reports_n=1,
                            chat_fn=lambda *a, **k: "不是 JSON",
                            rule_only=False, max_workers=1)
    assert all(r["source"] == "rule_fallback" for r in payload2["panel"][0]["roles"])
    assert any("解析失败" in e for e in payload2["panel"][0]["errors"])


# ---------------------------------------------------------------------------
# 6. 缺数据容错 + 选篇规则
# ---------------------------------------------------------------------------
def test_select_reports_orders_by_date_then_richness_and_skips_empty(tmp_path):
    def mk(name, pages, units):
        d = tmp_path / name
        d.mkdir()
        (d / "structure.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
        lines = [json.dumps({"claim": f"主张{i}", "page_start": i + 1}, ensure_ascii=False)
                 for i in range(units)]
        (d / "knowledge_units.jsonl").write_text("\n".join(lines), encoding="utf-8")

    mk("rp_AP202610071830180001", pages=10, units=5)
    mk("rp_AP202610081830180002", pages=30, units=3)   # 最新
    mk("rp_AP202610091830180003", pages=20, units=0)   # 零单元 → 跳过
    mk("rp_AP202610061830180004", pages=50, units=8)   # 最老
    catalog = rp.load_report_catalog(tmp_path)
    assert len(catalog) == 4
    selected = rp.select_reports(catalog, 3)
    assert [r["report_id"] for r in selected] == [
        "rp_AP202610081830180002", "rp_AP202610071830180001", "rp_AP202610061830180004"]
    # 缺 knowledge_units.jsonl 目录不进入 catalog；坏行/缺页码容错
    assert rp.load_report_units(tmp_path / "not_exist") == []
    (tmp_path / "rp_AP202610081830180002" / "knowledge_units.jsonl").write_text(
        "not-json\n" + json.dumps({"claim": "只有主张无页码"}, ensure_ascii=False), encoding="utf-8")
    units = rp.load_report_units(tmp_path / "rp_AP202610081830180002")
    assert len(units) == 1 and units[0]["page_start"] is None


def test_rule_based_role_is_deterministic_and_markdown_renders():
    report = _report()
    first = rp.rule_based_role("风控官", report, UNITS, reason="test")
    second = rp.rule_based_role("风控官", report, UNITS, reason="test")
    assert first["verdict"] == second["verdict"]
    assert first["source"] == "rule_fallback"
    assert first["key_facts"] and all("fact" in f for f in first["key_facts"])

    neutral = rp.rule_based_role("基本面分析师", report, [], reason="no_data")
    assert neutral["verdict"] == "neutral"
    assert any("知识单元不足" in r for r in neutral["reasons"])

    payload = rp.run_panel(root=REPO / "nonexistent_root", reports_n=1, rule_only=True,
                           chat_fn=_fake_chat_ok, date_tag="20261101")
    md = rp.render_markdown(payload)
    assert "多智能体研报评审面板" in md
    assert "## 候选池" in md
    assert "## 每篇角色观点" in md or payload["reports"] == []
    assert "## 局限与说明" in md


# ---------------------------------------------------------------------------
# 7. 标题兜底 + CLI
# ---------------------------------------------------------------------------
def test_report_title_skips_boilerplate_chapter():
    units = [dict(u) for u in UNITS]
    assert rp.report_title(_report(), units) == "M9 级覆铜板"  # 章节全是「图表目录」→ 用概念
    units2 = [dict(UNITS[0], chapter="产业并购重组快速推进"), dict(UNITS[1])]
    assert rp.report_title(_report(), units2) == "产业并购重组快速推进"
    assert rp.report_title({"report_id": "rp_X"}, []) == "rp_X"


def test_cli_help_runs_without_network():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(PANEL_PATH), "run", "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0
    for flag in ("--reports", "--model", "--timeout", "--rule-only"):
        assert flag in proc.stdout
