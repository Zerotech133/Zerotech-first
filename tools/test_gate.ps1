# test_gate.ps1 - 测试门禁脚本（GY-01，2026-10-27；W5 P1-6 2026-10-31 扩展）
#
# 用法（仓库任意目录）:
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\test_gate.ps1 -Suite fast
# -Suite: fast(portal+k_stock+ide+agent_key+quant_key+tools_key) | portal | kstock | ide |
#         agent_key | quant_key | tools_key | agent | quant | tools | all
# 退出码: 0=无新增失败(允许既存失败) | 1=有新增失败/收集错误 | 2=脚本错误
# 产物:  C:\Temp\test_gate_<suite>_<ts>.log
#
# 判据: 本次 FAILED/ERROR 减去 tools/test_baseline.json 的 known_failures（tools 套件供夜间/手动全量，不进 fast）
#       得到"新增失败"; 新增=0 才放行。
#
# 2026-10-31 W5 P1-6（docs/audit/2026-10-31_W5质量还债_v1.md）：
#   · fast 增 B1+B2 关键集三套件（agent_key/quant_key/tools_key，共 88 文件，每次独立
#     pytest；覆盖率 23.0%→30.9%→约 41.0%）。不同套件不能合并一次 pytest（sys.path 冲突）。
#   · B2 扩容（工单 K-ORG-20261031-GATEB2）：再 +31 文件（agent 9 / quant 14 / tools 8）。
#   · 新增 A5 portal 预算门（阶段1 **非 strict**：只拦硬规则，建议项 WARN 不阻断）。
#   · 文件列表套件的 baseline 查找支持 suiteKey（agent_platform/tests 等）回退。
# 2026-10-31 K-ORG-20261031-ORGGATEWIRE：新增 org 模型一致性检查（fast/all，warn-only；
#   当前 fail=6 为真实配置债，记录 fail/warn/info 到汇总但不断言失败，等 W4 决策后再 strict）。
param(
    [ValidateSet('fast', 'portal', 'kstock', 'ide', 'agent_key', 'quant_key', 'tools_key',
                 'agent', 'quant', 'tools', 'all')]
    [string]$Suite = 'fast',
    [int]$PytestTimeout = 120
)
$ErrorActionPreference = 'Continue'
$origEAP = $ErrorActionPreference
$baselineBroken = $false
if ($PSScriptRoot) { $repo = Split-Path -Parent $PSScriptRoot } else { $repo = 'C:\项目\老田的AI助手' }
$baselinePath = Join-Path $repo 'tools\test_baseline.json'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'

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

$suites = @()
switch ($Suite) {
    # 2026-10-30 P1：LDE HTTP 契约进 fast 门（单独文件，不拖入整个 agent 套件）
    # 2026-10-31 W5 P1-6 / K-ORG-20261031-GATEB2：B1+B2 关键集三套件进 fast（88 文件）
    'fast'    { $suites = @('portal', 'kstock', 'ide', 'agent_key', 'quant_key', 'tools_key') }
    'portal'  { $suites = @('portal') }
    'kstock'  { $suites = @('kstock') }
    'ide'     { $suites = @('ide') }
    'agent_key' { $suites = @('agent_key') }
    'quant_key' { $suites = @('quant_key') }
    'tools_key' { $suites = @('tools_key') }
    'agent'   { $suites = @('agent') }
    'quant'   { $suites = @('quant') }
    'tools'   { $suites = @('tools') }
    'all'     { $suites = @('portal', 'kstock', 'agent', 'quant', 'ide') }
    default   { Write-Host "[ERR] 未知 Suite: $Suite"; exit 2 }
}
$paths = @{
    'portal' = 'portal/tests'; 'kstock' = 'k_stock/tests'
    'agent'  = 'agent_platform/tests'; 'quant' = 'quant_backtest/tests'
    'tools'  = 'tools/tests'
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
        # W1（2026-10-31）：release/junction/live-version 信任链离线断言；
        # live 分支默认 skip（LEGION_LIVE_SMOKE=1 才跑），不拖慢门禁。
        'agent_platform/tests/test_release_live_smoke.py',
        # W5 P1-7b（WO-LEGION-W5-GATE-ART-20261031）：W1 release artifact 文件/manifest
        # sha256 对照 + /api/ide/version all_match live smoke（4 用例，约 +0.5s）；
        # W1 已合并（ef5e32b04）但此前未入 gate，属“关键 IDE 测试入 gate”的新缺口。
        'agent_platform/tests/test_ide_release_artifact.py'
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
        'tools/tests/test_merge_lock_gated.py'
    )
}

$newFailures = 0
$summary = @()
Push-Location -LiteralPath $repo
try {
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
            if ($line -match '^(FAILED|ERROR) ') {
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

    # ===== 2026-10-31 W5 P1-6 / A5：portal 拆分预算门 =====
    # 阶段1 非 strict：只拦硬规则（portal_api.py ≤5200 / api_*.py 存在可读）；
    # 建议项（冻结增长/新文件 >1200）只打印 WARN 不阻断——当前 5 项债清完后再切 --strict。
    if ($Suite -in @('fast', 'all')) {
        $budget = Join-Path $repo 'tools\check_portal_split.py'
        if (Test-Path -LiteralPath $budget) {
            $blog = Join-Path $env:TEMP "test_gate_budget_$ts.log"
            $bsw = [System.Diagnostics.Stopwatch]::StartNew()
            python -X utf8 $budget *> $blog
            $brc = $LASTEXITCODE
            $bsw.Stop()
            $bsecs = [math]::Round($bsw.Elapsed.TotalSeconds, 1)
            $bwarn = @()
            if (Test-Path -LiteralPath $blog) {
                $bwarn = @(Get-Content -LiteralPath $blog -Encoding UTF8 | Where-Object { $_ -match 'PORTAL-SPLIT WARN' })
            }
            if ($brc -ne 0) {
                $newFailures++
                Write-Host "[budget] PORTAL-SPLIT 硬违规 → 判失败（日志 $blog）" -ForegroundColor Red
                if (Test-Path -LiteralPath $blog) {
                    Get-Content -LiteralPath $blog -Encoding UTF8 | Select-Object -Last 8 | ForEach-Object { Write-Host "  | $_" }
                }
            } else {
                Write-Host "[budget] PASS：硬规则 0 违规；建议项 WARN $($bwarn.Count) 条（非 strict 不阻断）[${bsecs}s]" -ForegroundColor Green
                $bwarn | ForEach-Object { Write-Host "  $_" }
            }
            $summary += [pscustomobject]@{
                Suite = 'budget'; Rc = $brc; Failed = 0; Known = 0
                New = $(if ($brc -ne 0) { 1 } else { 0 }); Seconds = $bsecs; Log = $blog
            }
        } else {
            Write-Host "[budget] ⚠ 缺 tools/check_portal_split.py（跳过，不阻断）" -ForegroundColor Yellow
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
