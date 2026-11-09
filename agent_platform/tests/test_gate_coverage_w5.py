# -*- coding: utf-8 -*-
"""W5 fast gate 覆盖口径 + 关键链路漂移守卫（WO-LEGION-W5-GATE-20261031，2026-10-31）。

一句话：把“fast gate 覆盖了什么”锁进测试，防止后续合并把覆盖率/关键链路悄悄做回去。

背景
----
* `tools/test_gate.ps1 -Suite fast` 是 `devtask merge` 内联质量闸与
  pre-merge-commit 钩子的共同执行体：**它覆盖哪些测试 = 合入门实际拦住什么**。
* 2026-10-31 W5 P1-6 才把 B1 关键集（agent_key/quant_key/tools_key = 57 文件，
  ACAD1/D1/W3-outcome/IDE/evidence 关键链路）接进 fast；此前 gate 只跑
  portal+k_stock+ide。
* W5 覆盖审计（task-2）实测：fast 覆盖 176/651 测试文件、30,990/104,160 非空行
  = 30.99%（2026-10-31 11:47）。本文件是该审计的“防回退”产物。

覆盖率口径（诚实说明，别混用）
----------------------------
* 计划 v2 W5 / `docs/audit/2026-10-31_W5质量还债_v1.md` 报的百分比 =
  **五套件内 `test_*.py` 非空行中，被 fast gate 目标覆盖的占比（测试资产行占比）**，
  **不是 coverage.py 真实代码行覆盖**。本文件按同口径复算并设下限（见
  `COVERAGE_FLOOR_PCT`），只保证“不倒退”，不声称“核心代码 30%”。
* 真实行覆盖另测（coverage.py，2026-10-31 只读）：
  `agent_platform/legion_ide` 8 模块共 1,480 stmts，只跑 ide 套件 = 71%（422 miss）；
  补入 `test_legion_ide_audit.py` 后 = 72%（414 miss，exec_runner/writer 各 +2~3 stmts）。
  全局五套件真实覆盖未做（跨套件 sys.path/进程隔离，成本高，另立单）。

为什么本文件借 `ide` 套件跑（而不是进 agent_key）
----------------------------------------------
* `fast` 组合与 agent_key/quant_key/tools_key 三清单被
  `tools/tests/test_test_gate_b1.py` 精确冻结（B1=57 文件），本单写面不含
  `tools/tests/`，故**不扩三清单、不新增 fast 套件**；覆盖守卫与 IDE 关键集同属
  “质量门自证”链路，跟随 ide 套件每次 fast 执行（实测约 +0.1s）。
* W5 P1-7b（WO-LEGION-W5-GATE-ART-20261031）：W1 合并后新增的
  `test_ide_release_artifact.py`（release artifact 文件/manifest sha256 对照 +
  `/api/ide/version` all_match smoke，4 用例）也补入 ide 套件，纳入本守卫的
  IDE 关键集冻结。
* 2026-11-02 S2-08：fast 新增第 7 套件 datawarehouse（tools/datawarehouse/tests，158 项），
  本守卫 FAST_SUITES 与 test_test_gate_b1 / a5strict / org_gate / wtcheck_gate /
  artifact_guard 同批同步；三清单（agent_key/quant_key/tools_key）未变。
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE_PATH = REPO / "tools" / "test_gate.ps1"
BASELINE_PATH = REPO / "tools" / "test_baseline.json"

SUITE_DIRS = (
    "portal/tests",
    "k_stock/tests",
    "agent_platform/tests",
    "quant_backtest/tests",
    "tools/tests",
    "analysis/tests",
    "commercial/tests",
)
FAST_SUITES = ["portal", "kstock", "ide", "agent_key", "quant_key", "tools_key",
               "datawarehouse", "analysis", "commercial"]
# IDE 关键集：W5 审计结论是“ide 相关测试仅 test_legion_ide_audit.py 未入门”，
# 本单补齐后冻结；集合只增不减（新增 IDE 测试可以直接进 ide 套件，不影响本断言）。
IDE_KEY_FILES = {
    "agent_platform/tests/test_legion_ide_http.py",
    "agent_platform/tests/test_hermes_orchestrator.py",
    "agent_platform/tests/test_legion_ide_session.py",
    "agent_platform/tests/test_legion_ide_undo_store.py",
    "agent_platform/tests/test_ide_extension_e2e.py",
    "agent_platform/tests/test_ide_rw.py",
    "agent_platform/tests/test_release_live_smoke.py",
    "agent_platform/tests/test_ide_release_artifact.py",  # W5 P1-7b 补入（W1 产出）
    "agent_platform/tests/test_legion_ide_audit.py",  # W5 P1-7 补入
    "agent_platform/tests/test_gate_coverage_w5.py",  # 本文件
    # WO-QUAL-BUNDLE-20261031：fresh worktree 缺 gitignored IDE bundle 引导回归
    "agent_platform/tests/test_ide_bundle_bootstrap.py",
}
# ACAD1/D1/W3(outcome) 关键链路：W5 验收项要求“关键 IDE/ACAD/D1 测试入 gate”，
# B1 已全部接入；这里锁住“不得被移出”，防止后续精简清单时静默丢失。
ACAD1_D1_W3_KEY_FILES = {
    # ACAD1
    "agent_platform/tests/test_academy_wiring.py",
    "agent_platform/tests/test_academy_ops.py",
    "agent_platform/tests/test_academy_role_attrs.py",
    "tools/tests/test_academy_ledger_check.py",
    "tools/tests/test_academy_activation4.py",
    # D1
    "agent_platform/tests/test_d1_evidence_policy.py",
    "agent_platform/tests/test_d1_l4_provider.py",
    "agent_platform/tests/test_d1_json_contract.py",
    "agent_platform/tests/test_d1_decision_strict.py",
    "agent_platform/tests/test_d1_calibration.py",
    "agent_platform/tests/test_d1_research_mcp.py",
    "agent_platform/tests/test_d1_data_fallback.py",
    # W3：outcome 记录闭环
    "agent_platform/tests/test_decision_outcomes.py",
}
# 已审计确认“未入 fast gate”的 W3/W4 关键测试（债务台账，不代表已覆盖）：
# 它们都在 tools/tests 或 quant_backtest/tests，接入必须扩 B1/B2 三清单并同步
# tools/tests/test_test_gate_b1.py 冻结守卫——超出本单写面 → 记录于此，随 B2/B3
# 批次处理；接入后必须删对应项（本测试会红，强制台账同步）。
KNOWN_UNGATED_W3_W4 = {
    "tools/tests/test_a0_patrol_decision.py",  # W4：A0Patrol 决策记忆
    "tools/tests/test_agent_census_parse.py",  # W4：66 席运行审计（解析）
    "tools/tests/test_agent_census_status.py",  # W4：66 席运行审计（状态）
    "quant_backtest/tests/test_lcs1_harness.py",  # W3：LCS-1 Go/No-Go 沙盘
}
# 覆盖率下限（W5 口径：测试资产非空行占比）。2026-10-31 11:47 实测 30.99%；
# B1 计划目标 30%，下限取 30.0 留约 1pp（≈1,000 行）缓冲；B2 接入（计划 ~41%）
# 后应上调到 39.0 左右（届时本行注释与数值同改）。
COVERAGE_FLOOR_PCT = 30.0
FILES_FLOOR_PCT = 25.0


def _gate_text() -> str:
    return GATE_PATH.read_text(encoding="utf-8-sig")


def _file_lists(gate: str) -> dict:
    """解析 $paths 表里的“文件列表套件”（ide/agent_key/quant_key/tools_key）。"""
    out = {}
    for m in re.finditer(r"'(\w+)'\s*=\s*@\((.*?)\n    \)", gate, re.S):
        files = re.findall(r"'([^']+\.py)'", m.group(2))
        if files:
            out[m.group(1)] = files
    return out


def _fast_suites(gate: str) -> list:
    m = re.search(r"'fast'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)", gate)
    assert m, "test_gate.ps1 缺 fast 套件定义"
    return re.findall(r"'([a-z_]+)'", m.group(1))


def _gated_files(gate: str) -> set:
    """fast gate 实际覆盖的测试文件全集：目录套件全量 + 文件列表套件。"""
    lists = _file_lists(gate)
    gated = set()
    for d in ("portal/tests", "k_stock/tests", "tools/datawarehouse/tests",
              "analysis/tests", "commercial/tests"):
        gated.update(p.relative_to(REPO).as_posix() for p in (REPO / d).rglob("test_*.py"))
    for key in ("ide", "agent_key", "quant_key", "tools_key"):
        gated.update(lists.get(key, []))
    return gated


def _nonblank_lines(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    return sum(1 for ln in data.splitlines() if ln.strip())


def _coverage_metric(gate: str) -> tuple:
    """复算 W5 口径覆盖率：(覆盖文件数, 总文件数, 覆盖非空行, 总非空行)。"""
    gated = _gated_files(gate)
    total_files = total_lines = cov_files = cov_lines = 0
    for d in SUITE_DIRS:
        for p in (REPO / d).rglob("test_*.py"):
            rel = p.relative_to(REPO).as_posix()
            n = _nonblank_lines(p)
            total_files += 1
            total_lines += n
            if rel in gated:
                cov_files += 1
                cov_lines += n
    return cov_files, total_files, cov_lines, total_lines


def test_fast_suite_composition_frozen():
    assert _fast_suites(_gate_text()) == FAST_SUITES, (
        "fast 套件组合变了：本守卫与 tools/tests/test_test_gate_b1.py 都按 "
        "portal+kstock+ide+agent_key+quant_key+tools_key+datawarehouse 冻结，改动需同批更新两者")


def test_gate_targets_exist():
    """gate 里列出的 .py 目标必须真实存在（拼错= pytest rc!=0 假红/空跑不报）。"""
    gate = _gate_text()
    lists = _file_lists(gate)
    assert lists, "test_gate.ps1 未解析到任何文件列表套件"
    missing = [f for files in lists.values() for f in files if not (REPO / f).is_file()]
    assert not missing, "gate 目标文件不存在: %s" % missing[:5]
    for d in ("portal/tests", "k_stock/tests"):
        assert (REPO / d).is_dir(), "目录套件不存在: %s" % d
    for key in ("ide", "agent_key", "quant_key", "tools_key"):
        assert lists.get(key), "缺文件列表套件 %s" % key
    # 套件内不得重复登记（重复=白跑且掩盖遗漏）
    for key, files in lists.items():
        dup = sorted({f for f in files if files.count(f) > 1})
        assert not dup, "%s 套件重复登记: %s" % (key, dup)


def test_ide_key_gap_gated():
    """IDE 关键集必须全在 ide 套件内（含 W5 补入的 audit/live-smoke 不得被移出）。"""
    ide = set(_file_lists(_gate_text()).get("ide", []))
    missing = sorted(IDE_KEY_FILES - ide)
    assert not missing, "IDE 关键测试未在 ide 套件: %s" % missing


def test_acad1_d1_w3_key_tests_still_gated():
    """ACAD1/D1/W3(outcome) 关键测试必须仍在 fast gate 内（W5 验收项）。"""
    gated = _gated_files(_gate_text())
    missing = sorted(ACAD1_D1_W3_KEY_FILES - gated)
    assert not missing, "ACAD1/D1/W3 关键测试被移出 gate: %s" % missing


def test_known_ungated_w3_w4_debt_ledger():
    """锁住已知未入门的 W3/W4 债务：接入后必须同步修改本台账（防静默漂移）。"""
    gated = _gated_files(_gate_text())
    still_ungated = sorted(f for f in KNOWN_UNGATED_W3_W4 if f not in gated)
    assert still_ungated == sorted(KNOWN_UNGATED_W3_W4), (
        "债务台账失真：%s 已不在“未入门”状态——若已接入 gate，请从 "
        "KNOWN_UNGATED_W3_W4 删除，并同步 tools/tests/test_test_gate_b1.py 冻结清单"
        % sorted(set(KNOWN_UNGATED_W3_W4) - set(still_ungated)))
    missing = [f for f in KNOWN_UNGATED_W3_W4 if not (REPO / f).is_file()]
    assert not missing, "债务台账文件已不存在（需更新台账）: %s" % missing


def test_coverage_metric_floor():
    """复算 W5 口径覆盖率并设下限；新增测试文件若不入 gate 会拉低下限→这里先红。"""
    cov_files, total_files, cov_lines, total_lines = _coverage_metric(_gate_text())
    file_pct = 100.0 * cov_files / total_files
    line_pct = 100.0 * cov_lines / total_lines
    assert line_pct >= COVERAGE_FLOOR_PCT, (
        "fast gate 覆盖非空行 %.2f%% < 下限 %.1f%%（W5 口径=测试资产行占比，"
        "不是 coverage.py）；新增测试请同时接入 gate，或正式修订本下限"
        % (line_pct, COVERAGE_FLOOR_PCT))
    assert file_pct >= FILES_FLOOR_PCT, (
        "fast gate 覆盖文件 %.2f%% < 下限 %.1f%%" % (file_pct, FILES_FLOOR_PCT))


def test_baseline_suite_key_fallback_ready():
    """文件列表套件的 known/env/flaky 按 suiteKey 回退，基线缺块=既存登记失效。"""
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    for key in ("agent_platform/tests", "quant_backtest/tests", "tools/tests"):
        assert key in baseline, "test_baseline.json 缺 suiteKey 块 %s" % key
        assert isinstance(baseline[key].get("known_failures", []), list)
        assert isinstance(baseline[key].get("flaky", []), list)
    assert "suiteKey" in GATE_PATH.read_text(encoding="utf-8-sig"), (
        "test_gate.ps1 缺 suiteKey 回退逻辑，文件列表套件的既存登记将失效")


# ── portal 根级回归测试入门守卫（2026-10-31 WO-QUAL-GATE-PORTAL-20261031）──
# 根因：fast 原只收 portal/tests 目录，portal/ 根级两条回归测试
# （test_bot_ws_backoff / test_km_card_kind）长期失败却不被门拦，2026-10-31
# task-16 修复时才暴露。本守卫锁死「显式纳入且仅这两条」，既防回退成只收
# portal/tests，也防“图省事”把 portal 根 140 个测试全量塞进门禁（性能）。
PORTAL_ROOT_REGRESSIONS = {
    "portal/test_bot_ws_backoff.py",
    "portal/test_km_card_kind.py",
    # 2026-11-08 P1-6 分层落地：portal/conftest.py 把 portal/ append 进 sys.path 后，
    # 两个 watch_tower 根级测试由「收集即错」变为 2 + 6 条全绿，纳入 fast 防回退。
    # 同批 alerts_level / alert_source_health 实测红、整模块 skip 挂起，不进套件。
    "portal/test_watch_tower_linkage.py",
    "portal/test_watch_tower_snapshot.py",
}


def _portal_suite_targets(gate: str) -> list:
    """解析 $paths 表里 portal 套件的目标（兼容 字符串 与 数组 两种写法）。"""
    m = re.search(r"'portal'\s*=\s*@\((.*?)\n    \)", gate, re.S)
    if m:
        return re.findall(r"'([^']+)'", m.group(1))
    m = re.search(r"'portal'\s*=\s*'([^']+)'", gate)
    return [m.group(1)] if m else []


def test_portal_root_regressions_gated():
    """portal 根级四个可达回归测试必须显式在 fast portal 套件内。"""
    gate = _gate_text()
    targets = _portal_suite_targets(gate)
    assert "portal/tests" in targets, (
        "portal 套件必须仍覆盖 portal/tests 目录（实际 %s）" % targets)
    root_files = [t for t in targets
                  if t.startswith("portal/") and t.endswith(".py")]
    assert set(root_files) == PORTAL_ROOT_REGRESSIONS, (
        "fast portal 套件根级文件应为且仅为 %s（实际 %s）：不得回退成只收 "
        "portal/tests，也不得全量纳入 portal 根 140 个测试"
        % (sorted(PORTAL_ROOT_REGRESSIONS), root_files))
    for f in PORTAL_ROOT_REGRESSIONS:
        assert (REPO / f).is_file(), "gate 目标不存在: %s" % f
    # 多目标必须真被 pytest 消费：脚本按 @($paths[$name]) 展开为多路径参数
    assert "$targets = @($paths[$name])" in gate, (
        "test_gate.ps1 缺多目标展开，portal 数组会只跑第一项")


def test_portal_suite_still_switchable():
    """-Suite portal 仍映射到 portal 套件（数组改造不得丢 switch 分支）。"""
    assert re.search(
        r"'portal'\s*\{\s*\$suites\s*=\s*@\('portal'\)\s*\}", _gate_text())


def test_test_gate_ps1_keeps_utf8_bom():
    """test_gate.ps1 必须保留 UTF-8 BOM（2026-10-31 本单实测踩坑）。

    门禁由 `powershell -File tools/test_gate.ps1` 执行；Windows PowerShell 5.1
    对无 BOM 的 UTF-8 按 ANSI(GBK) 解码，中文注释/字符串的引号会错位 →
    整脚本 ParserError（GATE 恒失败或恒不跑）。编辑工具重写 ps1 时易丢 BOM，
    故用字节级断言锁死。
    """
    raw = GATE_PATH.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        "test_gate.ps1 丢了 UTF-8 BOM：Windows PowerShell 5.1 会按 ANSI 解码中文 → "
        "整脚本解析失败（用 UTF-8 BOM 保存即可）")
    text = raw.decode("utf-8-sig")
    assert "GATE PASS" in text and "新增失败" in text


# ── fresh worktree 缺 gitignored IDE bundle 引导守卫（WO-QUAL-BUNDLE-20261031）──
BUNDLE_REL = "portal/agent_ide_bundle.json"
BUNDLE_BUILDER = "tools/build_agent_ide_bundle.py"


def test_gate_bootstraps_missing_ide_bundle():
    """fresh worktree 缺 gitignored IDE bundle：gate 必须先受控生成（防假红）。

    根因（2026-10-31 task-21 实测）：portal/agent_ide_bundle.json 被 .gitignore
    忽略，fresh worktree 无该文件 → test_hermes_orchestrator 2 + test_ide_enrich 1
    假红（读不到 bundle，非代码/测试坏）。引导块必须：在套件循环之前、仅 worktree、
    缺件才生成、失败 fail-open（不新增假红、不锁死合并）。
    """
    gate = _gate_text()
    assert BUNDLE_REL in gate and BUNDLE_BUILDER in gate
    assert (REPO / BUNDLE_BUILDER).is_file()
    assert "if (-not (Test-Path -LiteralPath $bundlePath))" in gate
    assert "$inWorktree = ($repo -match" in gate, (
        "引导需限定 worktree：不得自动改写主仓/生产 bundle")
    boot = gate.find("fresh worktree 缺 gitignored IDE bundle 引导")
    assert boot >= 0, "test_gate.ps1 缺 bundle 引导块（marker 注释）"
    assert boot < gate.find("foreach ($s in $suites)"), (
        "bundle 引导必须在套件循环之前，否则 pytest 已先跑出假红")
    block = gate[boot:gate.find("$newFailures = 0", boot)]
    assert "build_agent_ide_bundle.py" in block
    assert "fail-open" in block, "引导失败须 fail-open（不新增假红）"
    assert "exit 1" not in block, "引导失败不得直接 exit 1（否则缺件仍锁死合并）"
