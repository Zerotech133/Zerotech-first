# 一次性恢复脚本：把 preopen_daily 从 Disabled 恢复为 Ready。
#
# 存在理由：2026-11-13 因「不接受用滞后 2 日的共识池出票」手动禁用了 preopen_daily。
# 若忘记恢复，周一 08:10 会继续不出票，而任务计划程序不会给任何告警，
# 表现与「跑了但没票」完全不同 —— 这类静默失效比报错危险。
#
# 关键细节（不这么写就会白干）：preopen_daily 的 Settings.StartWhenAvailable = True。
# 直接 Enable-ScheduledTask 会让 Task Scheduler 判定「今天 08:10 那次错过」，
# 随即立即补跑 —— 等于用程序绕过了「今天不出票」的决定。
# 因此顺序必须是：关补跑 → Enable → 校验未补跑 → 恢复原值。
#
# 本脚本幂等：任务已是 Ready 时直接退出，不重复操作。
$ErrorActionPreference = 'Stop'

$TASK = 'preopen_daily'
$STAMP = Get-Date -Format 'yyyyMMdd-HHmmss'
$LOG = Join-Path $env:TEMP ('preopen_restore_' + $STAMP + '.log')

function Say($msg) {
    $line = ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    Write-Output $line
    Add-Content -Path $LOG -Value $line -Encoding utf8
}

try {
    $t = Get-ScheduledTask -TaskName $TASK -ErrorAction Stop
    $info0 = Get-ScheduledTaskInfo -TaskName $TASK
    Say ('BEFORE state={0} lastRun={1} nextRun={2} startWhenAvailable={3}' -f `
        $t.State, $info0.LastRunTime, $info0.NextRunTime, $t.Settings.StartWhenAvailable)

    if ($t.State -eq 'Ready') {
        Say 'ALREADY_READY 任务已是 Ready，无需恢复（幂等退出）'
        exit 0
    }

    $orig = $t.Settings.StartWhenAvailable

    # 1) 先关闭「错过补跑」，否则 Enable 会立即补跑今天已过的那次
    $s = $t.Settings
    $s.StartWhenAvailable = $false
    Set-ScheduledTask -TaskName $TASK -Settings $s | Out-Null
    Say 'STEP1 startWhenAvailable -> False'

    # 2) 启用
    Enable-ScheduledTask -TaskName $TASK | Out-Null
    Start-Sleep -Seconds 3
    $info1 = Get-ScheduledTaskInfo -TaskName $TASK
    Say ('STEP2 enabled, lastRun={0} nextRun={1}' -f $info1.LastRunTime, $info1.NextRunTime)

    # 3) 校验：LastRunTime 不应因这次 Enable 而推进
    $ran = ($info1.LastRunTime -ne $info0.LastRunTime)
    if ($ran) {
        Say 'ALERT 检测到补跑（lastRun 已推进）——这是本脚本要防的情况，需人工确认'
    } else {
        Say 'STEP3 OK 未发生补跑'
    }

    # 4) 恢复原值
    $s2 = (Get-ScheduledTask -TaskName $TASK).Settings
    $s2.StartWhenAvailable = $orig
    Set-ScheduledTask -TaskName $TASK -Settings $s2 | Out-Null
    Say ('STEP4 startWhenAvailable -> {0}' -f $orig)

    Start-Sleep -Seconds 2
    $info2 = Get-ScheduledTaskInfo -TaskName $TASK
    $t2 = Get-ScheduledTask -TaskName $TASK
    Say ('AFTER state={0} lastRun={1} nextRun={2} startWhenAvailable={3}' -f `
        $t2.State, $info2.LastRunTime, $info2.NextRunTime, $t2.Settings.StartWhenAvailable)

    # 退出码语义：给 Task Scheduler 的是「跑完了吗」，业务结论写日志。
    # 正常完成一律 0；只有异常才非 0。
    exit 0
}
catch {
    Say ('FATAL ' + $_.Exception.GetType().Name + ': ' + $_.Exception.Message)
    exit 1
}
