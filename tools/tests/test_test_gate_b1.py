# -*- coding: utf-8 -*-
"""W5 P1-6 落地守卫：test_gate.ps1 B1 关键集 + A5 预算门（2026-10-31）。

静态锁死（不执行门禁本体）：
  · fast = portal + kstock + ide + agent_key + quant_key + tools_key；
  · agent_key/quant_key/tools_key = W5 报告 B1 清单（57 文件），文件必须真实存在；
  · 每次套件独立 pytest（脚本结构保证），baseline 支持 suiteKey 回退；
  · A5 预算门已接入且**阶段1 非 strict**（不得出现 --strict 调用）；
  · B1 只进 fast，不进 all（all 仍是 portal/kstock/agent/quant/ide）。
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GATE_PATH = REPO / "tools" / "test_gate.ps1"
GATE = GATE_PATH.read_text(encoding="utf-8")

AGENT_KEY = {
    "agent_platform/tests/test_d1_evidence_policy.py",
    "agent_platform/tests/test_legion_ide.py",
    "agent_platform/tests/test_d1_l4_provider.py",
    "agent_platform/tests/test_ide_release_info.py",
    "agent_platform/tests/test_a0_evidence_judge.py",
    "agent_platform/tests/test_evidence.py",
    "agent_platform/tests/test_academy_wiring.py",
    "agent_platform/tests/test_legion_meeting_backfill.py",
    "agent_platform/tests/test_idem_acceptance.py",
    "agent_platform/tests/test_idempotency.py",
    "agent_platform/tests/test_academy_ops.py",
    "agent_platform/tests/test_ide_enrich.py",
    "agent_platform/tests/test_d1_json_contract.py",
    "agent_platform/tests/test_dev_legion_metrics.py",
    "agent_platform/tests/test_legion_litellm_route.py",
    "agent_platform/tests/test_ide_roles.py",
    "agent_platform/tests/test_d1_decision_strict.py",
    "agent_platform/tests/test_legion_readiness.py",
    "agent_platform/tests/test_roster_d1_contract.py",
    "agent_platform/tests/test_identity_display_names.py",
    "agent_platform/tests/test_academy_role_attrs.py",
    "agent_platform/tests/test_legion_command_run_scope.py",
    "agent_platform/tests/test_dev_legion_api.py",
    "agent_platform/tests/test_legion_contract.py",
    "agent_platform/tests/test_ide_worktree.py",
    "agent_platform/tests/test_d1_calibration.py",
    "agent_platform/tests/test_dev_legion_ui.py",
    "agent_platform/tests/test_dev_legion_ai_card.py",
    "agent_platform/tests/test_d1_research_mcp.py",
    "agent_platform/tests/test_dev_legion_quant_card.py",
    "agent_platform/tests/test_dev_legion_risk_card.py",
    "agent_platform/tests/test_ide_context.py",
    "agent_platform/tests/test_d1_data_fallback.py",
    "agent_platform/tests/test_dev_legion_real.py",
    "agent_platform/tests/test_decision_outcomes.py",
    "agent_platform/tests/test_devtask_dev_gate.py",
    "agent_platform/tests/test_gate3_verdict.py",
    "agent_platform/tests/test_inbox_gateway_lifecycle.py",
    "agent_platform/tests/test_gateway.py",
    "agent_platform/tests/test_gate_content_hash.py",
    "agent_platform/tests/test_dev_gate_warn_report.py",
    "agent_platform/tests/test_run_shadow_guard.py",
    "agent_platform/tests/test_signature_guard.py",
}
QUANT_KEY = {
    "quant_backtest/tests/test_xq_acceptance_evidence_offline.py",
    "quant_backtest/tests/test_multisource_evidence_contract.py",
    "quant_backtest/tests/test_game_watch_alert_evidence_adapter.py",
    "quant_backtest/tests/test_intel_evidence_dto.py",
    "quant_backtest/tests/test_intel_evidence_store.py",
    "quant_backtest/tests/test_valuation_identity.py",
    "quant_backtest/tests/test_calendar_provider.py",
    "quant_backtest/tests/test_xq_request_gate_offline.py",
    "quant_backtest/tests/test_quant_sector_dual_gate.py",
}
TOOLS_KEY = {
    "tools/tests/test_release_legion_ide.py",
    "tools/tests/test_auto_rebuild_ide_bundle.py",
    "tools/tests/test_academy_ledger_check.py",
    "tools/tests/test_academy_activation4.py",
    "tools/tests/test_killsvc_guard.py",
}


def _suite_files(name: str) -> list:
    m = re.search(r"'%s'\s*=\s*@\((.*?)\n    \)" % re.escape(name), GATE, re.S)
    assert m, "test_gate.ps1 缺 %r 文件列表" % name
    return re.findall(r"'([^']+\.py)'", m.group(1))


def _suite_list(switch_key: str) -> list:
    m = re.search(r"'%s'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)" % re.escape(switch_key), GATE)
    assert m, "test_gate.ps1 缺 suite 定义 %r" % switch_key
    return re.findall(r"'([a-z_]+)'", m.group(1))


def test_b1_lists_frozen():
    assert set(_suite_files("agent_key")) == AGENT_KEY
    assert set(_suite_files("quant_key")) == QUANT_KEY
    assert set(_suite_files("tools_key")) == TOOLS_KEY
    assert len(AGENT_KEY) == 43 and len(QUANT_KEY) == 9 and len(TOOLS_KEY) == 5
    all_files = AGENT_KEY | QUANT_KEY | TOOLS_KEY
    assert len(all_files) == 57, "B1 应为 57 文件（W5 报告 §8.3）"


def test_b1_files_exist_and_under_expected_dir():
    for f in AGENT_KEY:
        assert f.startswith("agent_platform/tests/")
    for f in QUANT_KEY:
        assert f.startswith("quant_backtest/tests/")
    for f in TOOLS_KEY:
        assert f.startswith("tools/tests/")
    missing = [f for f in (AGENT_KEY | QUANT_KEY | TOOLS_KEY) if not (REPO / f).is_file()]
    assert not missing, "B1 文件缺失: %s" % missing[:5]


def test_fast_includes_b1_suites_only():
    assert _suite_list("fast") == ["portal", "kstock", "ide", "agent_key", "quant_key", "tools_key"]
    # B1 只进 fast；all 保持原样（不扩 all，避免夜间/全量时长失控）
    assert _suite_list("all") == ["portal", "kstock", "agent", "quant", "ide"]
    param_block = GATE[: GATE.find("$suites = @()")]
    for key in ("agent_key", "quant_key", "tools_key"):
        assert "'%s'" % key in param_block, "ValidateSet 缺 %s" % key


def test_baseline_suite_key_fallback_present():
    assert "suiteKey" in GATE
    assert re.search(r"\$known\[\$suiteKey\]", GATE)
    assert re.search(r"\$envKnown\.ContainsKey\(\$suiteKey\)", GATE)
    assert re.search(r"\$flaky\[\$suiteKey\]", GATE)


def test_a5_budget_gate_wired_non_strict():
    # 已接入
    assert "check_portal_split.py" in GATE
    assert re.search(r"if \(\$Suite -in @\('fast', 'all'\)\)", GATE), "预算门应只在 fast/all 跑"
    # 阶段1 必须非 strict（预算调用行出现 --strict 即说明未按计划分阶段；
    # 注释里允许写“清债后再切 --strict”）
    assert not re.search(r"\$budget[^\n]*--strict", GATE), "阶段1 预算调用不得使用 --strict"
    assert re.search(r"python -X utf8 \$budget", GATE), "预算门应以非 strict 方式调用脚本"
    # 硬违规必须计入 newFailures（否则门形同虚设）
    assert re.search(r"newFailures\+\+", GATE)


def test_per_suite_invocation_and_timing():
    # 一套件一次 pytest；py 文件列表套件不能与目录套件混跑（会 sys.path 冲突）
    assert GATE.count("python -m pytest @targets") == 1
    assert "Seconds" in GATE, "汇总应含各套件耗时列"
