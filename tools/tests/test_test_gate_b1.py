# -*- coding: utf-8 -*-
"""W5 P1-6 落地守卫：test_gate.ps1 B1+B2 关键集 + A5 预算门（2026-10-31）。

静态锁死（不执行门禁本体）：
  · fast = portal + kstock + ide + agent_key + quant_key + tools_key + datawarehouse
    （2026-11-02 S2-08 起；datawarehouse = tools/datawarehouse/tests 目录套件）；
  · agent_key/quant_key/tools_key = W5 报告 B1(57)+B2(31) 清单 + 黄忠聚合入口（共 89 文件），
    文件必须真实存在（B2 扩容工单 K-ORG-20261031-GATEB2；黄忠纳管 WO-HZ-GATE-20261031；W5 口径 30.9%→约 41.0%）；
  · 每次套件独立 pytest（脚本结构保证），baseline 支持 suiteKey 回退；
  · A5 预算门已接入，**2026-10-31 K-ORG-20261031-A5STRICT 起为阶段2 strict**
    （调用必须带 --strict；建议项同样阻断；缺脚本 fail-closed）；
  · 关键集只进 fast，不进 all（all 仍是 portal/kstock/agent/quant/ide）。
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
    # ---- B2 扩容（工单 K-ORG-20261031-GATEB2，+9）----
    "agent_platform/tests/test_lookahead_guard.py",
    "agent_platform/tests/test_quality_gate.py",
    "agent_platform/tests/test_demo_guard.py",
    "agent_platform/tests/test_osleg_p2b_gate.py",
    "agent_platform/tests/test_osleg_http_api.py",
    "agent_platform/tests/test_org_enroll.py",
    "agent_platform/tests/test_check_trigger_chain.py",
    "agent_platform/tests/test_gov_ai.py",
    "agent_platform/tests/test_codex_bridge.py",
    "agent_platform/tests/test_ifs_handoff.py",
    "agent_platform/tests/test_ifs_paper_chain.py",
    "agent_platform/tests/test_ifs_wire_chain.py",
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
    # ---- B2 扩容（工单 K-ORG-20261031-GATEB2，+14）----
    "quant_backtest/tests/test_peak_shift_gates.py",
    "quant_backtest/tests/test_watch_offline_consumption_gate.py",
    "quant_backtest/tests/test_weak_inventory_gate.py",
    "quant_backtest/tests/test_execution_calendar_gate.py",
    "quant_backtest/tests/test_session_aggregate.py",
    "quant_backtest/tests/test_xq_factor_v1.py",
    "quant_backtest/tests/test_xq_t7_cache_tdx_backfill.py",
    "quant_backtest/tests/test_game_signal_ref_bridge.py",
    "quant_backtest/tests/test_watch_dispatch_agent_reporting.py",
    "quant_backtest/tests/test_xq_history_priority_offline.py",
    "quant_backtest/tests/test_osleg_http_contract.py",
    "quant_backtest/tests/test_proxy_dialogue.py",
    "quant_backtest/tests/test_xq_offline_chain_e2e.py",
    "quant_backtest/tests/test_chanlun_learn.py",
}
TOOLS_KEY = {
    "tools/tests/test_release_legion_ide.py",
    "tools/tests/test_auto_rebuild_ide_bundle.py",
    "tools/tests/test_academy_ledger_check.py",
    "tools/tests/test_academy_activation4.py",
    "tools/tests/test_killsvc_guard.py",
    # ---- B2 扩容（工单 K-ORG-20261031-GATEB2，+8）----
    "tools/tests/test_l2_guard_fp.py",
    "tools/tests/test_merge_gated.py",
    "tools/tests/test_git_worktree_guard.py",
    "tools/tests/test_devtask_branch_gate.py",
    "tools/tests/test_merge_dirty_guard.py",
    "tools/tests/test_sql_guard.py",
    "tools/tests/test_devtask_gates.py",
    "tools/tests/test_merge_lock_gated.py",
    # ---- 黄忠工具测试纳管（WO-HZ-GATE-20261031，+1 聚合入口）----
    "tools/tests/test_huangzhong_tools_suite_gate.py",
    "tools/tests/test_interface_registry_contract.py",
}


def _suite_files(name: str) -> list:
    m = re.search(r"'%s'\s*=\s*@\((.*?)\n    \)" % re.escape(name), GATE, re.S)
    assert m, "test_gate.ps1 缺 %r 文件列表" % name
    return re.findall(r"'([^']+\.py)'", m.group(1))


def _suite_list(switch_key: str) -> list:
    m = re.search(r"'%s'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)" % re.escape(switch_key), GATE)
    assert m, "test_gate.ps1 缺 suite 定义 %r" % switch_key
    return re.findall(r"'([a-z_]+)'", m.group(1))


def test_b1_b2_lists_frozen():
    assert set(_suite_files("agent_key")) == AGENT_KEY
    assert set(_suite_files("quant_key")) == QUANT_KEY
    assert set(_suite_files("tools_key")) == TOOLS_KEY
    assert len(AGENT_KEY) == 55 and len(QUANT_KEY) == 23 and len(TOOLS_KEY) == 15
    all_files = AGENT_KEY | QUANT_KEY | TOOLS_KEY
    assert len(all_files) == 93, "B1(57)+B2(31)+IFS(4)+黄忠聚合入口(1) 应为 93 文件"


def test_b1_b2_files_exist_and_under_expected_dir():
    for f in AGENT_KEY:
        assert f.startswith("agent_platform/tests/")
    for f in QUANT_KEY:
        assert f.startswith("quant_backtest/tests/")
    for f in TOOLS_KEY:
        assert f.startswith("tools/tests/")
    missing = [f for f in (AGENT_KEY | QUANT_KEY | TOOLS_KEY) if not (REPO / f).is_file()]
    assert not missing, "关键集文件缺失: %s" % missing[:5]


def test_fast_includes_key_suites_only():
    assert _suite_list("fast") == ["portal", "kstock", "ide", "agent_key", "quant_key",
                                   "tools_key", "datawarehouse"]
    # 关键集只进 fast；all 保持原样（不扩 all，避免夜间/全量时长失控）
    assert _suite_list("all") == ["portal", "kstock", "agent", "quant", "ide"]
    param_block = GATE[: GATE.find("$suites = @()")]
    for key in ("agent_key", "quant_key", "tools_key", "datawarehouse"):
        assert "'%s'" % key in param_block, "ValidateSet 缺 %s" % key
    # S2-08：datawarehouse 必须是目录套件且目录真实存在（拼错=空跑且不报错）
    dw = re.search(r"'datawarehouse'\s*=\s*'([^']+)'", GATE)
    assert dw and dw.group(1) == "tools/datawarehouse/tests", "S2-08 datawarehouse 套件路径缺失"
    assert (REPO / dw.group(1)).is_dir(), "datawarehouse 目录不存在: %s" % dw.group(1)


def test_baseline_suite_key_fallback_present():
    assert "suiteKey" in GATE
    assert re.search(r"\$known\[\$suiteKey\]", GATE)
    assert re.search(r"\$envKnown\.ContainsKey\(\$suiteKey\)", GATE)
    assert re.search(r"\$flaky\[\$suiteKey\]", GATE)


def test_a5_budget_gate_wired_strict():
    # 已接入
    assert "check_portal_split.py" in GATE
    assert re.search(r"if \(\$Suite -in @\('fast', 'all'\)\)", GATE), "预算门应只在 fast/all 跑"
    # 2026-10-31 K-ORG-20261031-A5STRICT：阶段2 必须使用 --strict（建议项同样阻断）
    assert re.search(r"python -X utf8 \$budget --strict", GATE), "阶段2 预算门必须以 --strict 调用"
    assert "PASS（strict）" in GATE
    # 违规/缺脚本必须计入 newFailures（否则门形同虚设）
    assert re.search(r"newFailures\+\+", GATE)
    assert "fail-closed，不假绿" in GATE


def test_per_suite_invocation_and_timing():
    # 一套件一次 pytest；py 文件列表套件不能与目录套件混跑（会 sys.path 冲突）
    assert GATE.count("python -m pytest @targets") == 1
    assert "Seconds" in GATE, "汇总应含各套件耗时列"
