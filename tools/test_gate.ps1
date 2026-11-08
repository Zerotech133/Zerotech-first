# test_gate.ps1 - 测试门禁脚本（GY-01，2026-10-27；W5 P1-6 2026-10-31 扩展）
#
# 用法（仓库任意目录）:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast
# -Suite: fast(portal+k_stock+ide+agent_key+quant_key+tools_key+datawarehouse) | portal | kstock | ide |
#         agent_key | quant_key | tools_key | datawarehouse | agent | quant | tools | all
# 退出码: 0=无新增失败(允许既存失败) | 1=有新增失败/收集错误 | 2=脚本错误
# 产物:  C:\Temp\test_gate_<suite>_<ts>.log
#
# 判据: 本次 FAILED/ERROR 减去 tools/test_baseline.json 的 known_failures（tools 套件仅手工触发、不进 fast；关键单文件请登记进 tools_key）
#       得到"新增失败"; 新增=0 才放行。
#
# 2026-10-31 W5 P1-6（docs/audit/2026-10-31_W5质量还债_v1.md）：
#   · fast 增 B1+B2 关键集三套件（agent_key/quant_key/tools_key，共 88 文件，每次独立
#     pytest；覆盖率 23.0%→30.9%→约 41.0%）。不同套件不能合并一次 pytest（sys.path 冲突）。
#   · B2 扩容（工单 K-ORG-20261031-GATEB2）：再 +31 文件（agent 9 / quant 14 / tools 8）。
#   · 新增 A5 portal 预算门（2026-10-31 K-ORG-20261031-A5STRICT 起为**阶段2 strict**：
#     5 项债清完、strict 实测 PASS；硬规则+建议项全部阻断，缺脚本/执行异常 fail-closed）。
#   · 文件列表套件的 baseline 查找支持 suiteKey（agent_platform/tests 等）回退。
# 2026-10-31 K-ORG-20261031-ORGGATEWIRE：新增 org 模型一致性检查（fast/all，warn-only；
#   当前 fail=6 为真实配置债，记录 fail/warn/info 到汇总但不断言失败，等 W4 决策后再 strict）。
# 2026-11-02 S2-08（数据仓研发计划 v3）：datawarehouse 测试套件（tools/datawarehouse/tests，
#   158 项）进 fast 门——独立目录套件（每次独立 pytest，防 sys.path 冲突），fast 6→7 套件；
#   只进 fast 不进 all（all 保持夜间全量口径，避免时长失控）。冻结守卫同批同步。
param(
    [ValidateSet('fast', 'portal', 'kstock', 'ide', 'agent_key', 'quant_key', 'tools_key',
                 'datawarehouse', 'agent', 'quant', 'tools', 'all')]
    [string]$Suite = 'fast',
    [int]$PytestTimeout = 120
)
$ErrorActionPreference = 'Continue'
$origEAP = $ErrorActionPreference
$baselineBroken = $false
if ($PSScriptRoot) { $repo = Split-Path -Parent $PSScriptRoot } else { $repo = 'C:\项目\老田的AI助手' }
$baselinePath = Join-Path $repo 'tools\test_baseline.json'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'

# ===== 2026-10-31 K-ORG-20261031-BUNDLEB5：clean worktree 缺 ignored bundle 时先自愈 =====
# Phase2-B 后 bundle 不再 tracked；干净 worktree/CI 没有该文件，会让读取 bundle 的测试假红。
# 只做"缺失才生成"，已有文件不动；生成失败 fail-visible（退出 2，不假绿）。
$bootstrapScript = Join-Path $repo 'tools\bundle_bootstrap.py'
if (Test-Path -LiteralPath $bootstrapScript) {
    & python -X utf8 $bootstrapScript --repo $repo --check *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[bootstrap] bundle missing; generating before gate...' -ForegroundColor Yellow
        & python -X utf8 $bootstrapScript --repo $repo --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[bootstrap] FAILED: cannot generate bundle before gate (fail-visible)' -ForegroundColor Red
            exit 2
        }
    }
}

$known = @{}
$envKnown = @{}
$flaky = @{}
$inWorktree = ($repo -match '[\\/]\.worktrees[\\/]')
if (Test-Path -LiteralPath $baselinePath) {
    try {
        $b = Get-Content -LiteralPath $baselinePath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($prop in $b.PSObject.Properties) {
            $known[$prop.Name] = @($prop.Value.known_failures)
            if ($prop.Value.env_failures) { $envKnown[$prop.Name] = @($prop.Value.env_failures) }
            if ($prop.Value.flaky) { $flaky[$prop.Name] = @($prop.Value.flaky) }
        }
    } catch {
        Write-Host "[ERR] baseline 解析失败: $($_.Exception.Message)" -ForegroundColor Red
        $baselineBroken = $true
    }
}

# 2026-11-08 CI 上移（K-CI-20261108-PROFILE）：环境覆盖层。
# 干净 runner 上必然缺的本机运行时资产——reports/ 整目录未入库、.worktrees/、
# C://项目//_vault、PG catalog、行情湖 parquet、docker / schtasks——会让一批
# 用例在云端必然红。把这些人肉登记进 test_baseline.json 会连带放宽本机判定，
# 真回归被一起糊掉（这是本仓明令禁止的"假绿"）。故改为按 profile 加载独立
# 基线：TEST_GATE_PROFILE=ci 时叠加 tools/test_baseline.ci.json。
# 本机不设该变量 → profile=local → 判定与今天完全一致，严格性零放宽。
$profileName = 'local'
if ($env:TEST_GATE_PROFILE) { $profileName = $env:TEST_GATE_PROFILE.Trim().ToLower() }
if ($profileName -ne 'local') {
    $profilePath = Join-Path $repo ('tools\test_baseline.' + $profileName + '.json')
    if (-not (Test-Path -LiteralPath $profilePath)) {
        # profile 名写错必须可见：静默回落到 local 会满屏假红，静默放宽则是假绿。
        Write-Host "[ERR] 环境基线不存在: $profilePath (profile=$profileName)" -ForegroundColor Red
        $baselineBroken = $true
    } else {
        try {
            $pb = Get-Content -LiteralPath $profilePath -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($prop in $pb.PSObject.Properties) {
                if ($prop.Name -like '_*') { continue }
                if ($prop.Value.env_failures) {
                    if (-not $envKnown.ContainsKey($prop.Name)) { $envKnown[$prop.Name] = @() }
                    $envKnown[$prop.Name] += @($prop.Value.env_failures)
                }
                if ($prop.Value.known_failures) {
                    if (-not $known.ContainsKey($prop.Name)) { $known[$prop.Name] = @() }
                    $known[$prop.Name] += @($prop.Value.known_failures)
                }
                if ($prop.Value.flaky) {
                    if (-not $flaky.ContainsKey($prop.Name)) { $flaky[$prop.Name] = @() }
                    $flaky[$prop.Name] += @($prop.Value.flaky)
                }
            }
            Write-Host "[baseline] 环境覆盖层: $profileName"
        } catch {
            Write-Host "[ERR] 环境基线解析失败: $($_.Exception.Message)" -ForegroundColor Red
            $baselineBroken = $true
        }
    }
}

$suites = @()
switch ($Suite) {
    # 2026-10-30 P1：LDE HTTP 契约进 fast 门（单独文件，不拖入整个 agent 套件）
    # 2026-10-31 W5 P1-6 / K-ORG-20261031-GATEB2：B1+B2 关键集三套件进 fast（88 文件）
    # 2026-11-02 S2-08：datawarehouse 目录套件（158 项）进 fast；只进 fast 不进 all。
    'fast'    { $suites = @('portal', 'kstock', 'ide', 'agent_key', 'quant_key', 'tools_key', 'datawarehouse') }
    'portal'  { $suites = @('portal') }
    'kstock'  { $suites = @('kstock') }
    'ide'     { $suites = @('ide') }
    'agent_key' { $suites = @('agent_key') }
    'quant_key' { $suites = @('quant_key') }
    'tools_key' { $suites = @('tools_key') }
    'datawarehouse' { $suites = @('datawarehouse') }
    'agent'   { $suites = @('agent') }
    'quant'   { $suites = @('quant') }
    'tools'   { $suites = @('tools') }
    'all'     { $suites = @('portal', 'kstock', 'agent', 'quant', 'ide') }
    default   { Write-Host "[ERR] 未知 Suite: $Suite"; exit 2 }
}
$paths = @{
    # 2026-10-31 WO-QUAL-GATE-PORTAL-20261031：portal 根级回归测试显式纳入 fast 门。
    # 背景：fast 只收 portal/tests 目录，导致 portal/ 根级 test_bot_ws_backoff.py /
    # test_km_card_kind.py 长期失败不被门拦（2026-10-31 task-16 修复时才暴露）。
    # 只显式加入口可达的根级回归，不全量纳入 portal 根 163 个测试（性能）；
    # 覆盖守卫见 agent_platform/tests/test_gate_coverage_w5.py::test_portal_root_regressions_gated。
    # 2026-11-08 P1-6 分层落地：test_watch_tower_{linkage,snapshot} 原是「收集即错」的
    # 根级测试（--import-mode=importlib 下 portal/ 不入 sys.path），portal/conftest.py
    # 补 append 后实测 2 + 6 条全绿，纳入 fast 防回退；同批 test_alerts_level /
    # test_alert_source_health 实测红（13/14 方法含失败），整模块 skip 挂起待 owner
    # 定性，故不进套件——「挂起」不等于「纳入」。
    'portal' = @(
        'portal/tests',
        'portal/test_bot_ws_backoff.py',
        'portal/test_km_card_kind.py',
        'portal/test_watch_tower_linkage.py',
        'portal/test_watch_tower_snapshot.py'
    )
    'kstock' = 'k_stock/tests'
    'agent'  = 'agent_platform/tests'; 'quant' = 'quant_backtest/tests'
    'tools'  = 'tools/tests'
    # 2026-11-02 S2-08：数据仓工具测试（158 项，目录套件；独立 pytest 防 sys.path 冲突）
    'datawarehouse' = 'tools/datawarehouse/tests'
    'ide'    = @(
        # W5 P1-7（WO-LEGION-W5-GATE-20261031，2026-10-31）：IDE 关键缺口补入——
        # rw_guard 锁互斥/stale 回收/审计留痕/exec 沙箱；此前是 IDE 相关测试里唯一
        # 未进 fast gate 的文件（6 用例，约 +0.3s）。
        'agent_platform/tests/test_legion_ide_audit.py',
        # W5 P1-7：fast gate 覆盖口径 + 关键链路漂移守卫（约 +0.1s）。借 ide 套件跑：
        # B1/B2 三清单被 tools/tests/test_test_gate_b1.py 精确冻结，本单写面不含
        # tools/tests/；追加块放在 ide 数组开头，避开 K-ORG-20261031-GATEB2 的
        # B2 扩容区域（agent_key/quant_key/tools_key 与头部注释），降低并行合并冲突面。
        'agent_platform/tests/test_gate_coverage_w5.py',
        'agent_platform/tests/test_legion_ide_http.py',
        'agent_platform/tests/test_hermes_orchestrator.py',
        'agent_platform/tests/test_legion_ide_session.py',
        'agent_platform/tests/test_legion_ide_undo_store.py',
        'agent_platform/tests/test_ide_extension_e2e.py',
        'agent_platform/tests/test_ide_rw.py',
        # WO-INTEL-N1AB-FIX-20261031：INTEL_TO_N1 接线 + reviewed JEV 只读上下文
        # 离线上线，必须进 fast gate，防止再次出现“分支绿、合并红”。
        'agent_platform/tests/test_intel_context.py',
        'agent_platform/tests/test_intel_to_n1.py',
        # WO-INTEL-D1EVID-FIX-20261031：D1/run_shadow 的 reviewed 情报证据旁路
        # 也放入 fast gate，锁定 off 逐字不变与 on/fallback 两态。
        'agent_platform/tests/test_d1_intel_evidence.py',
        # W1（2026-10-31）：release/junction/live-version 信任链离线断言；
        # live 分支默认 skip（LEGION_LIVE_SMOKE=1 才跑），不拖慢门禁。
        'agent_platform/tests/test_release_live_smoke.py',
        # W5 P1-7b（WO-LEGION-W5-GATE-ART-20261031）：W1 release artifact 文件/manifest
        # sha256 对照 + /api/ide/version all_match live smoke（4 用例，约 +0.5s）；
        # W1 已合并（ef5e32b04）但此前未入 gate，属“关键 IDE 测试入 gate”的新缺口。
        'agent_platform/tests/test_ide_release_artifact.py',
        # WO-QUAL-BUNDLE-20261031：fresh worktree 缺 gitignored IDE bundle 的引导
        # 回归（builder --out 受控生成 + bundle 契约断言，约 +1~2s）。
        'agent_platform/tests/test_ide_bundle_bootstrap.py'
        'agent_platform/tests/test_evidence_raw_pit.py'
        'agent_platform/tests/test_decision_outcomes_pit.py'
        'agent_platform/tests/test_approval_control.py'
        'agent_platform/tests/test_agent_policy.py'
        'agent_platform/tests/test_a0_agent_p1.py'
        'agent_platform/tests/test_a0_chief_evidence_fields.py'
        'agent_platform/tests/test_acad1_pool_contract.py'
        'agent_platform/tests/test_agent_sandbox.py'
        'agent_platform/tests/test_anonymous_review.py'
        'agent_platform/tests/test_approvals_registry.py'
        'agent_platform/tests/test_approvals_store.py'
        'agent_platform/tests/test_approval_card.py'
        'agent_platform/tests/test_approval_control_m4_enforce.py'
        'agent_platform/tests/test_approval_rollup.py'
        'agent_platform/tests/test_execution_gate.py'
        'agent_platform/tests/test_v11_m0_preflight.py'
    )
    # W5 P1-6 B1+B2 关键集（2026-10-31）：每次独立 pytest（不同套件 sys.path 不共用，
    # 混跑会 ImportError）。B1 清单见 W5 报告 §8.3；B2 扩容见 §8.4（工单 GATEB2，+31）。
    # tools/tests/test_test_gate_b1.py 冻结清单防漂移。
    'agent_key' = @(
        'agent_platform/tests/test_d1_evidence_policy.py',
        'agent_platform/tests/test_legion_ide.py',
        'agent_platform/tests/test_d1_l4_provider.py',
        'agent_platform/tests/test_ide_release_info.py',
        'agent_platform/tests/test_a0_evidence_judge.py',
        'agent_platform/tests/test_evidence.py',
        'agent_platform/tests/test_academy_wiring.py',
        'agent_platform/tests/test_legion_meeting_backfill.py',
        'agent_platform/tests/test_idem_acceptance.py',
        'agent_platform/tests/test_idempotency.py',
        'agent_platform/tests/test_academy_ops.py',
        'agent_platform/tests/test_ide_enrich.py',
        'agent_platform/tests/test_d1_json_contract.py',
        'agent_platform/tests/test_dev_legion_metrics.py',
        'agent_platform/tests/test_legion_litellm_route.py',
        'agent_platform/tests/test_ide_roles.py',
        'agent_platform/tests/test_d1_decision_strict.py',
        'agent_platform/tests/test_legion_readiness.py',
        'agent_platform/tests/test_roster_d1_contract.py',
        'agent_platform/tests/test_identity_display_names.py',
        'agent_platform/tests/test_academy_role_attrs.py',
        'agent_platform/tests/test_legion_command_run_scope.py',
        'agent_platform/tests/test_dev_legion_api.py',
        'agent_platform/tests/test_legion_contract.py',
        'agent_platform/tests/test_ide_worktree.py',
        'agent_platform/tests/test_d1_calibration.py',
        'agent_platform/tests/test_dev_legion_ui.py',
        'agent_platform/tests/test_dev_legion_ai_card.py',
        'agent_platform/tests/test_d1_research_mcp.py',
        'agent_platform/tests/test_dev_legion_quant_card.py',
        'agent_platform/tests/test_dev_legion_risk_card.py',
        'agent_platform/tests/test_ide_context.py',
        'agent_platform/tests/test_d1_data_fallback.py',
        'agent_platform/tests/test_dev_legion_real.py',
        'agent_platform/tests/test_decision_outcomes.py',
        'agent_platform/tests/test_devtask_dev_gate.py',
        'agent_platform/tests/test_gate3_verdict.py',
        'agent_platform/tests/test_inbox_gateway_lifecycle.py',
        'agent_platform/tests/test_gateway.py',
        'agent_platform/tests/test_gate_content_hash.py',
        'agent_platform/tests/test_dev_gate_warn_report.py',
        'agent_platform/tests/test_run_shadow_guard.py',
        'agent_platform/tests/test_signature_guard.py',
        # ---- B2 扩容（K-ORG-20261031-GATEB2，+9；W5 报告 §8.4）----
        'agent_platform/tests/test_lookahead_guard.py',
        'agent_platform/tests/test_quality_gate.py',
        'agent_platform/tests/test_demo_guard.py',
        'agent_platform/tests/test_osleg_p2b_gate.py',
        'agent_platform/tests/test_osleg_http_api.py',
        'agent_platform/tests/test_org_enroll.py',
        'agent_platform/tests/test_check_trigger_chain.py',
        'agent_platform/tests/test_gov_ai.py',
        'agent_platform/tests/test_codex_bridge.py'
    )
    'quant_key' = @(
        'quant_backtest/tests/test_xq_acceptance_evidence_offline.py',
        'quant_backtest/tests/test_multisource_evidence_contract.py',
        'quant_backtest/tests/test_game_watch_alert_evidence_adapter.py',
        'quant_backtest/tests/test_intel_evidence_dto.py',
        'quant_backtest/tests/test_intel_evidence_store.py',
        'quant_backtest/tests/test_valuation_identity.py',
        'quant_backtest/tests/test_calendar_provider.py',
        'quant_backtest/tests/test_xq_request_gate_offline.py',
        'quant_backtest/tests/test_quant_sector_dual_gate.py',
        # ---- B2 扩容（K-ORG-20261031-GATEB2，+14；含 2 个离线替代单测）----
        'quant_backtest/tests/test_peak_shift_gates.py',
        'quant_backtest/tests/test_watch_offline_consumption_gate.py',
        'quant_backtest/tests/test_weak_inventory_gate.py',
        'quant_backtest/tests/test_execution_calendar_gate.py',
        'quant_backtest/tests/test_session_aggregate.py',
        'quant_backtest/tests/test_xq_factor_v1.py',
        'quant_backtest/tests/test_xq_t7_cache_tdx_backfill.py',
        'quant_backtest/tests/test_game_signal_ref_bridge.py',
        'quant_backtest/tests/test_watch_dispatch_agent_reporting.py',
        'quant_backtest/tests/test_xq_history_priority_offline.py',
        'quant_backtest/tests/test_osleg_http_contract.py',
        'quant_backtest/tests/test_proxy_dialogue.py',
        'quant_backtest/tests/test_xq_offline_chain_e2e.py',
        # ---- 2026-11-08 P3-5 xq 41 项修完纳管（K-ORG-20261108-XQSEAM）----
        # 这 4 个文件修复前红 41 项（38+2+1），是 quality-gate 激活的唯一前置。
        # 根因是 LLM 替身接缝断开：xq_classify 经 safe_urlopen 出网，loopback
        # 目标走 _NO_PROXY_OPENER（OpenerDirector.open），不经
        # urllib.request.urlopen，旧 monkeypatch 完全失效 → 测试真调本机
        # Ollama（qwen3.5:9b），答案随机。修法=patch 模块内 safe_urlopen，
        # 并加 socket.connect 守卫防接缝再断。详见各文件 docstring。
        'quant_backtest/tests/test_xq_classify_retry_degraded.py',
        'quant_backtest/tests/test_xq_recontent_exit.py',
        'quant_backtest/tests/test_xq_retry_cap.py',
        'quant_backtest/tests/test_verify_daily_report.py',
        'quant_backtest/tests/test_chanlun_learn.py'
    )
    'tools_key' = @(
        'tools/tests/test_release_legion_ide.py',
        'tools/tests/test_auto_rebuild_ide_bundle.py',
        'tools/tests/test_academy_ledger_check.py',
        'tools/tests/test_academy_activation4.py',
        'tools/tests/test_killsvc_guard.py',
        # ---- B2 扩容（K-ORG-20261031-GATEB2，+8）----
        'tools/tests/test_l2_guard_fp.py',
        'tools/tests/test_merge_gated.py',
        'tools/tests/test_git_worktree_guard.py',
        'tools/tests/test_devtask_branch_gate.py',
        'tools/tests/test_merge_dirty_guard.py',
        'tools/tests/test_sql_guard.py',
        'tools/tests/test_devtask_gates.py',
        'tools/tests/test_merge_lock_gated.py',
        # ---- 黄忠工具测试纳管（WO-HZ-GATE-20261031，+1 聚合入口）----
        # 黄忠 15 个测试文件位于 tools/ 根与 agent_platform/tests（不在 tools/tests/ 约定内），
        # 由唯一聚合 gate 测试用子进程跑全套（独立 pytest 防 sys.path 污染）。
        'tools/tests/test_huangzhong_tools_suite_gate.py',
        # ---- 学习线后置治理契约（2026-11-04 K-ORG-REVIEW，+2）----
        # pipeline→curation→effective 幂等契约（修复每日重跑覆盖人工治理的 BUG）
        'tools/tests/test_learning_curation.py',
        'tools/tests/test_learning_pipeline_curation.py',
        # ---- 盘后分析工程（2026-11-06 POSTMARKET-ENG，+1）----
        # 宽度/涨跌停/分桶/限期降级登记共 16 条；纯合成数据，无生产依赖，5s。
        'tools/tests/test_market_analysis.py',
        # ---- 回环禁代理豁免副本一致性（2026-11-07 K-ORG-20261107-GATESAFEURL，+1）----
        # 每份 safe_urlopen.py 必须含 is_loopback/_open_no_proxy；legion_command/safe_http.py
        # 必须委派。第 7 份退化副本曾静默存在半个月，扫描器按「调的是 safe_urlopen」判
        # GUARDED，看不见它。8 条纯离线，约 1s。
        'tools/tests/test_safe_urlopen.py',
        # ---- 凭据扫描器误报回归（2026-11-07 K-ORG-20261107-SCANFPGATE，+1）----
        # scan_secrets.py 是每次 commit 都跑的 pre-commit 闸门，也是 devtask merge
        # 硬门的引擎；但它自己的检测逻辑此前零自动覆盖（-Suite tools 无人自动调）。
        # 退化方向是「少报」，假阴性不报错、只会让硬门变纸门。42 条全合成夹具，0.6s。
        'tools/tests/test_scan_secrets_fp.py',
        # ---- 看护预算契约（2026-11-07 K-ORG-20261107-WATCHDOG，+1） ----
        'tools/tests/test_keepalive_budget.py',
        # ---- worktree 判活 + 删不动兜底（2026-11-08 K-ORG-20261108-OVWSPLIT，+1）----
        # 该文件 2026-10-31 就存在，却一直没进套件：devtask 的并发闸与回收逻辑
        # 此前没有自动覆盖。2026-11-08 两个真坑都是人工发现的：
        #   ① 空壳登记（.git 指针已失）让 `git -C <path> status` 向上读到主仓状态，
        #      把主树的未提交改动误判成"这个 worktree 脏了"；
        #   ② Windows 删大目录 186~251s，180s 超时必然误判，留下"指针没了树还在"。
        # 两者都会让闸门判断失真，故纳入常态门。21 条纯合成夹具，约 8s。
        'tools/tests/test_devtask_worktree_mech.py'
    )
}

# ===== 2026-10-31 WO-QUAL-BUNDLE-20261031：fresh worktree 缺 gitignored IDE bundle 引导 =====
# 背景：portal/agent_ide_bundle.json 是 .gitignore 忽略的 nginx 静态产物（~800KB）。
# fresh worktree 检出无该文件 → hermes_orchestrator 2 + ide_enrich 1 假红（非代码坏）。
# 处理：仅在 **worktree**（合并门实际执行处）且缺件时，用本 checkout 的受控源
# （registry/prompts → tools/build_agent_ide_bundle.py）确定性生成到该 worktree；
# 不写主仓/生产 bundle、不入 git；生成失败 fail-open（不阻断门禁）并留日志。
$bundleRel = 'portal/agent_ide_bundle.json'
$bundlePath = Join-Path $repo $bundleRel
$inWorktree = ($repo -match '[\\/]\.worktrees[\\/]')
if (-not (Test-Path -LiteralPath $bundlePath)) {
    if (-not $inWorktree) {
        Write-Host "[bootstrap] 缺 $bundleRel 且当前非 worktree：不自动生成（避免覆盖生产 bundle）；相关测试可能假红" -ForegroundColor Yellow
    } else {
        $builder = Join-Path $repo 'tools\build_agent_ide_bundle.py'
        if (Test-Path -LiteralPath $builder) {
            $blog = Join-Path $env:TEMP "test_gate_bundle_$ts.log"
            $bsw = [System.Diagnostics.Stopwatch]::StartNew()
            Push-Location -LiteralPath $repo
            try {
                python -X utf8 $builder *> $blog
                $brc = $LASTEXITCODE
            } finally {
                Pop-Location
            }
            $bsw.Stop()
            $bsecs = [math]::Round($bsw.Elapsed.TotalSeconds, 1)
            if ($brc -eq 0 -and (Test-Path -LiteralPath $bundlePath)) {
                $bsize = (Get-Item -LiteralPath $bundlePath).Length
                Write-Host "[bootstrap] 缺 $bundleRel → 已用受控源生成（$bsize bytes，仅本 worktree，不入 git）[${bsecs}s]"
            } else {
                Write-Host "[bootstrap] 生成失败 rc=$brc（fail-open：不阻断门禁；日志 $blog）" -ForegroundColor Yellow
            }
        } else {
            Write-Host "[bootstrap] 缺 $bundleRel 且缺生成器（跳过，不阻断）" -ForegroundColor Yellow
        }
    }
}

$newFailures = 0
$summary = @()
Push-Location -LiteralPath $repo
try {
    # ===== 2026-10-31 K-ORG-20261031-ARTGUARDWIRE：release artifact 完整性 guard（前置）=====
    # 背景：14:12 事故——另一并行进程绕过 release 工具改写 current/app.js（83,125B vs manifest
    # 81,060B），直到 ide 测试才暴露。本前置只读校验 current.json→junction→manifest→逐文件
    # sha256+bytes（不 repair/不 lock/不发布），在跑任何测试套件前尽早失败可见。
    # rc 映射：0=PASS；1=篡改/缺文件/junction drift → newFailures++ 判失败（不假绿）；
    # 2/缺脚本/无 release 根 → 只告警（开发机/旧 worktree 无发布物，不阻断）。
    if ($Suite -in @('fast', 'all')) {
        $guardTool = Join-Path $repo 'tools\release_artifact_guard.py'
        $releaseRoot = 'C:\项目\_vault\legion_ide\release\legion-ide'
        $glog = Join-Path $env:TEMP "test_gate_artifact_$ts.log"
        if (-not (Test-Path -LiteralPath $guardTool)) {
            Write-Host "[artifact] ⚠ 缺 tools/release_artifact_guard.py（只告警，不阻断；开发机/旧 worktree）" -ForegroundColor Yellow
            $summary += [pscustomobject]@{
                Suite = 'artifact'; Rc = -1; Failed = 0; Known = 0
                New = 0; Seconds = 0; Log = ''
            }
        } elseif (-not (Test-Path -LiteralPath $releaseRoot)) {
            Write-Host "[artifact] ⚠ 无 release 根 $releaseRoot（开发机无发布物；只告警，不阻断）" -ForegroundColor Yellow
            $summary += [pscustomobject]@{
                Suite = 'artifact'; Rc = -2; Failed = 0; Known = 0
                New = 0; Seconds = 0; Log = ''
            }
        } else {
            $gsw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $guardTool --check --json --release-root $releaseRoot *> $glog
            $grc = $LASTEXITCODE
            $gsw.Stop()
            $gsecs = [math]::Round($gsw.Elapsed.TotalSeconds, 1)
            $gjson = $null
            if (Test-Path -LiteralPath $glog) {
                try { $gjson = Get-Content -LiteralPath $glog -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $gjson = $null }
            }
            $gver = ''
            $gdrift = $null
            $gbad = 0
            if ($gjson) {
                if ($gjson.pointer) { $gver = [string]$gjson.pointer.version }
                if ($gjson.junction) { $gdrift = $gjson.junction.drift }
                if ($gjson.summary) { $gbad = [int]$gjson.summary.files_bad }
            }
            if ($grc -eq 0) {
                Write-Host "[artifact] PASS：version=$gver junction_drift=$gdrift（current/manifest/逐文件 sha 全一致）[${gsecs}s]" -ForegroundColor Green
                $summary += [pscustomobject]@{
                    Suite = 'artifact'; Rc = 0; Failed = 0; Known = 0
                    New = 0; Seconds = $gsecs; Log = $glog
                }
            } elseif ($grc -eq 1) {
                $newFailures++
                Write-Host "[artifact] FAIL：artifact 完整性失败（篡改/缺文件/junction drift）→ 判失败（version=$gver drift=$gdrift bad_files=$gbad）" -ForegroundColor Red
                if ($gjson -and $gjson.errors) {
                    @($gjson.errors) | Select-Object -First 5 | ForEach-Object { Write-Host "  | $_" }
                }
                if (Test-Path -LiteralPath $glog) {
                    Get-Content -LiteralPath $glog -Encoding UTF8 | Select-Object -Last 6 | ForEach-Object { Write-Host "  | $_" }
                }
                $summary += [pscustomobject]@{
                    Suite = 'artifact'; Rc = 1; Failed = $gbad; Known = 0
                    New = 1; Seconds = $gsecs; Log = $glog
                }
            } else {
                Write-Host "[artifact] ⚠ guard 环境错误 rc=$grc（缺 current/current.json 等；只告警，不阻断；日志 $glog）" -ForegroundColor Yellow
                $summary += [pscustomobject]@{
                    Suite = 'artifact'; Rc = $grc; Failed = 0; Known = 0
                    New = 0; Seconds = $gsecs; Log = $glog
                }
            }
        }
    }

    foreach ($s in $suites) {
        $name = $s
        $targets = @($paths[$name])
        $log = Join-Path $env:TEMP "test_gate_${name}_$ts.log"
        Write-Host "===== [$name] $($targets -join ' ') ====="
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        python -m pytest @targets -q --timeout=$PytestTimeout -p no:cacheprovider *> $log
        $rc = $LASTEXITCODE
        $sw.Stop()
        $secs = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        $rcBad = ($rc -ne 0 -and $rc -ne 1)
        if ($rcBad) {
            Write-Host "[$name] pytest 异常退出 rc=$rc（未收集/用法/内部错误，按失败处理）" -ForegroundColor Red
        }
        $lines = @()
        if (Test-Path -LiteralPath $log) { $lines = @(Get-Content -LiteralPath $log -Encoding UTF8) }
        $failed = @()
        if ($rcBad) { $failed += "PYTEST_ABNORMAL_RC_$rc" }
        foreach ($line in $lines) {
            # 2026-11-02 S2-08：改 -cmatch（大小写敏感）。pytest 短摘要行固定大写 FAILED/ERROR；
            # 原 -match 大小写不敏感，会把 OTel exporter stderr 'Failed to export spans batch...' 误判为
            # 测试失败（datawarehouse test_otel_tracing 触发该噪声 → fast 门假红）。
            if ($line -cmatch '^(FAILED|ERROR) ') {
                $f = $line -replace '^(FAILED|ERROR) ', ''
                $f = $f -replace ' - .*$', ''
                $failed += $f.Trim()
            }
        }
        $knownList = @()
        # 2026-10-28：env_failures 不再仅限 worktree——数据/环境依赖（如行情湖未更新到
        # 当日、worktree 无数据根）在主仓同样成立；仍单独打印，不掩盖真实新增失败。
        foreach ($t in $targets) {
            $knownList += @($known[$t])
            if ($envKnown.ContainsKey($t)) { $knownList += @($envKnown[$t]) }
            $knownList += @($flaky[$t])
            # 2026-10-31 W5 P1-6：文件列表套件（agent_key/quant_key/tools_key）按
            # suiteKey（如 agent_platform/tests）回退查基线，保持 existing 登记生效。
            if ($t -match '\.py$') {
                $suiteKey = $t -replace '/[^/]+$', ''
                if ($suiteKey -ne $t) {
                    $knownList += @($known[$suiteKey])
                    if ($envKnown.ContainsKey($suiteKey)) { $knownList += @($envKnown[$suiteKey]) }
                    $knownList += @($flaky[$suiteKey])
                }
            }
        }
        $new = @()
        foreach ($f in $failed) { if ($knownList -notcontains $f) { $new += $f } }
        # W5 P1-6：文件列表套件按 suiteKey 回退会重复累加同一登记；去重并剔除 $null，
        # 让汇总 Known 列保持可读（不影响新增失败判定）。
        $knownList = @($knownList | Where-Object { $_ } | Select-Object -Unique)
        $newFailures += $new.Count
        $tail = ''
        if ($lines.Count -gt 0) { $tail = $lines[$lines.Count - 1] }
        $summary += [pscustomobject]@{
            Suite = $name; Rc = $rc; Failed = $failed.Count; Known = $knownList.Count
            New = $new.Count; Seconds = $secs; Log = $log
        }
        if ($new.Count -gt 0) {
            Write-Host "[$name] 新增失败 $($new.Count) 项:" -ForegroundColor Red
            $new | Select-Object -First 20 | ForEach-Object { Write-Host "  - $_" }
        } else {
            Write-Host "[$name] 无新增失败(既存 $($failed.Count) 项已登记) [${secs}s]" -ForegroundColor Green
        }
        if ($tail) { Write-Host "  $tail" }
    }

    # ===== 2026-10-31 W5 P1-6 / A5：portal 拆分预算门（阶段2 **strict**）=====
    # 2026-10-31 K-ORG-20261031-A5STRICT：5 项债已清（api_common 533≤1200、api_screen 1016≤1200、
    # 三冻结文件回基线），`check_portal_split.py --strict` 实测 PASS（rc=0）→ 升级为 strict：
    # 硬规则 + 建议项（冻结增长/新文件 >1200）全部阻断；缺脚本/执行异常 fail-closed 可见（不假绿）。
    if ($Suite -in @('fast', 'all')) {
        $budget = Join-Path $repo 'tools\check_portal_split.py'
        $blog = Join-Path $env:TEMP "test_gate_budget_$ts.log"
        if (Test-Path -LiteralPath $budget) {
            $bsw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $budget --strict *> $blog
            $brc = $LASTEXITCODE
            $bsw.Stop()
            $bsecs = [math]::Round($bsw.Elapsed.TotalSeconds, 1)
            $bpass = ($brc -eq 0) -and (Test-Path -LiteralPath $blog)
            if (-not $bpass) {
                $newFailures++
                Write-Host "[budget] PORTAL-SPLIT strict 违规/执行异常 → 判失败（rc=$brc，日志 $blog）" -ForegroundColor Red
                if (Test-Path -LiteralPath $blog) {
                    Get-Content -LiteralPath $blog -Encoding UTF8 | Select-Object -Last 10 | ForEach-Object { Write-Host "  | $_" }
                }
            } else {
                Write-Host "[budget] PASS（strict）：硬规则 0 违规、建议项 0 WARN [${bsecs}s]" -ForegroundColor Green
            }
            $summary += [pscustomobject]@{
                Suite = 'budget'; Rc = $brc; Failed = 0; Known = 0
                New = $(if ($bpass) { 0 } else { 1 }); Seconds = $bsecs; Log = $blog
            }
        } else {
            $newFailures++
            Write-Host "[budget] ✗ 缺 tools/check_portal_split.py → 判失败（fail-closed，不假绿）" -ForegroundColor Red
            $summary += [pscustomobject]@{
                Suite = 'budget'; Rc = -1; Failed = 0; Known = 0; New = 1; Seconds = 0; Log = ''
            }
        }
    }

    # ===== 2026-10-31 P132：导航统一门（阻断，分支感知 --root 由 devtask 门负责）=====
    if ($Suite -in @('fast', 'all')) {
        $navTool = Join-Path $repo 'tools\unify_nav.py'
        if (Test-Path -LiteralPath $navTool) {
            $nlog = Join-Path $env:TEMP "test_gate_nav_$ts.log"
            $nsw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $navTool --check *> $nlog
            $nrc = $LASTEXITCODE
            $nsw.Stop()
            $nsecs = [math]::Round($nsw.Elapsed.TotalSeconds, 1)
            if ($nrc -ne 0) {
                $newFailures++
                Write-Host "[nav] 导航统一检查失败 → 判失败（日志 $nlog）" -ForegroundColor Red
                if (Test-Path -LiteralPath $nlog) {
                    Get-Content -LiteralPath $nlog -Encoding UTF8 | Select-Object -Last 8 | ForEach-Object { Write-Host "  | $_" }
                }
            } else {
                Write-Host "[nav] PASS：导航统一 [${nsecs}s]" -ForegroundColor Green
            }
            $summary += [pscustomobject]@{
                Suite = 'nav'; Rc = $nrc; Failed = 0; Known = 0
                New = $(if ($nrc -ne 0) { 1 } else { 0 }); Seconds = $nsecs; Log = $nlog
            }
        } else {
            Write-Host "[nav] ⚠ 缺 tools/unify_nav.py（跳过，不阻断）" -ForegroundColor Yellow
        }
    }

    # ===== 2026-10-31 P130（WO-GUANYU-P130）：portal 路由清单门（阻断）=====
    # B0 契约：路由集合必须与 tools/portal_route_baseline.json 零 diff；
    # 新增/删除路由必须同提交 `python tools/portal_route_inventory.py --write-baseline` 重冻结，
    # 否则本条阻断合并（防 B4a-lite 期间"他线加路由未更基线→任意 portal_api 合并误红/漏检"重演）。
    if ($Suite -in @('fast', 'all')) {
        $routeTool = Join-Path $repo 'tools\portal_route_inventory.py'
        if (Test-Path -LiteralPath $routeTool) {
            $rlog = Join-Path $env:TEMP "test_gate_route_$ts.log"
            $rsw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $routeTool --check *> $rlog
            $rrc = $LASTEXITCODE
            $rsw.Stop()
            $rsecs = [math]::Round($rsw.Elapsed.TotalSeconds, 1)
            if ($rrc -ne 0) {
                $newFailures++
                Write-Host "[route] 路由清单与 B0 基线不一致 → 判失败（日志 $rlog）" -ForegroundColor Red
                Write-Host "        修法：确认路由变更属本单后，同提交运行 python tools\portal_route_inventory.py --write-baseline" -ForegroundColor Yellow
                if (Test-Path -LiteralPath $rlog) {
                    Get-Content -LiteralPath $rlog -Encoding UTF8 | Select-Object -Last 8 | ForEach-Object { Write-Host "  | $_" }
                }
            } else {
                Write-Host "[route] PASS：路由清单与基线零 diff [${rsecs}s]" -ForegroundColor Green
            }
            $summary += [pscustomobject]@{
                Suite = 'route'; Rc = $rrc; Failed = 0; Known = 0
                New = $(if ($rrc -ne 0) { 1 } else { 0 }); Seconds = $rsecs; Log = $rlog
            }
        } else {
            Write-Host "[route] ⚠ 缺 tools/portal_route_inventory.py（跳过，不阻断）" -ForegroundColor Yellow
        }
    }

    # ===== 2026-11-01 WO-GATE-WTCHECK-20261031：W2-3 worktree 校验门（阻断，fast/all）=====
    # 规则：doing/review 单必须有 .worktrees/<TID> 目录 + task/<TID> 分支。
    # 调用 `devtask.py audit-worktrees`：只读、内置 _resolve_main_repo（在任务 worktree
    # 内跑时自动经 git-common-dir 解析主仓并读主仓 tasks.json，不依赖调用 cwd）。
    # rc!=0（存在无 worktree/分支的 active 单）→ newFailures++ 阻断；缺脚本/执行异常
    # fail-closed（不假绿）。实测耗时 <0.5s。
    if ($Suite -in @('fast', 'all')) {
        $wtTool = Join-Path $repo 'tools\devtask.py'
        $wlog = Join-Path $env:TEMP "test_gate_wtcheck_$ts.log"
        if (Test-Path -LiteralPath $wtTool) {
            $wsw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $wtTool audit-worktrees *> $wlog
            $wrc = $LASTEXITCODE
            $wsw.Stop()
            $wsecs = [math]::Round($wsw.Elapsed.TotalSeconds, 2)
            $wpass = ($wrc -eq 0) -and (Test-Path -LiteralPath $wlog)
            $wsum = ''
            if (Test-Path -LiteralPath $wlog) {
                $wline = Get-Content -LiteralPath $wlog -Encoding UTF8 | Select-String -Pattern '汇总：' | Select-Object -Last 1
                if ($wline) { $wsum = ([string]$wline).Trim() }
            }
            if (-not $wpass) {
                $newFailures++
                Write-Host "[wtcheck] W2-3 违规/执行异常 → 判失败（rc=$wrc，日志 $wlog）" -ForegroundColor Red
                if (Test-Path -LiteralPath $wlog) {
                    Get-Content -LiteralPath $wlog -Encoding UTF8 | Select-Object -First 12 | ForEach-Object { Write-Host "  | $_" }
                }
            } else {
                if (-not $wsum) { $wsum = 'active 单 worktree/分支齐全' }
                Write-Host "[wtcheck] PASS：$wsum [${wsecs}s]" -ForegroundColor Green
            }
            $summary += [pscustomobject]@{
                Suite = 'wtcheck'; Rc = $wrc; Failed = 0; Known = 0
                New = $(if ($wpass) { 0 } else { 1 }); Seconds = $wsecs; Log = $wlog
            }
        } else {
            $newFailures++
            Write-Host "[wtcheck] ✗ 缺 tools/devtask.py → 判失败（fail-closed，不假绿）" -ForegroundColor Red
            $summary += [pscustomobject]@{
                Suite = 'wtcheck'; Rc = -1; Failed = 0; Known = 0; New = 1; Seconds = 0; Log = ''
            }
        }
    }

    # ===== 2026-10-31 K-ORG-20261031-ORGGATEWIRE：org 模型一致性 gate（warn-only）=====
    # 当前 fail=6/warn=10/info=42 是真实配置债（等 W4 决策后再 strict），故 **warn-only**：
    # 只调用 --json、记录 fail/warn/info 到汇总/日志，绝不 newFailures++（不阻断门禁）。
    # 缺脚本/执行失败/JSON 解析失败也只告警（汇总行仍可见，不让门禁假绿）。
    if ($Suite -in @('fast', 'all')) {
        $orgTool = Join-Path $repo 'tools\org_model_consistency.py'
        if (Test-Path -LiteralPath $orgTool) {
            $olog = Join-Path $env:TEMP "test_gate_org_$ts.log"
            $osw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $orgTool --json *> $olog
            $orc = $LASTEXITCODE
            $osw.Stop()
            $osecs = [math]::Round($osw.Elapsed.TotalSeconds, 2)
            $ofail = 0; $owarn = 0; $oinfo = 0; $oparse = $false
            if (Test-Path -LiteralPath $olog) {
                try {
                    $oj = Get-Content -LiteralPath $olog -Raw -Encoding UTF8 | ConvertFrom-Json
                    $ofail = [int]$oj.summary.fail
                    $owarn = [int]$oj.summary.warn
                    $oinfo = [int]$oj.summary.info
                    $oparse = $true
                } catch {
                    $oparse = $false
                }
            }
            if ($oparse) {
                Write-Host "[org] WARN-ONLY：fail=$ofail warn=$owarn info=$oinfo（不阻断；W4 决策后转 strict）[${osecs}s]" -ForegroundColor Yellow
            } else {
                Write-Host "[org] ⚠ WARN-ONLY：输出无法解析为 JSON（rc=$orc，不阻断；日志 $olog）" -ForegroundColor Yellow
            }
            $summary += [pscustomobject]@{
                Suite = 'org_gate'; Rc = $orc; Failed = $(if ($oparse) { $ofail } else { 0 })
                Known = $(if ($oparse) { $owarn } else { 0 }); New = 0; Seconds = $osecs; Log = $olog
            }
        } else {
            Write-Host "[org] ⚠ WARN-ONLY：缺 tools/org_model_consistency.py（只告警，不阻断）" -ForegroundColor Yellow
            $summary += [pscustomobject]@{
                Suite = 'org_gate'; Rc = -1; Failed = 0; Known = 0; New = 0; Seconds = 0; Log = ''
            }
        }
    }
} finally {
    Pop-Location
    $ErrorActionPreference = $origEAP
}

Write-Host ''
Write-Host '===== 汇总 ====='
$summary | Format-Table -AutoSize
if ($flaky.Count -gt 0) {
    Write-Host '已知 flaky(不计入新增失败, 需跟踪):'
    foreach ($k in $flaky.Keys) { foreach ($f in $flaky[$k]) { Write-Host "  - [$k] $f" } }
}
if ($baselineBroken) {
    Write-Host 'GATE FAIL: baseline 解析失败（fail-closed，不按已知失败放行）' -ForegroundColor Red
    exit 1
}
if ($newFailures -eq 0) {
    Write-Host 'GATE PASS: 无新增失败' -ForegroundColor Green
    exit 0
} else {
    Write-Host "GATE FAIL: 新增失败 $newFailures 项" -ForegroundColor Red
    exit 1
}

# OCR warn-gate validation marker 2026-10-27
