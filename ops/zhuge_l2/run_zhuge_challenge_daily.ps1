# run_zhuge_challenge_daily.ps1 — C1 质询卡每日生成包装器（离线版，W5 快照优先）
# L2 批准：老田 2026-10-27（五要素包见 C:\项目\deliverables\2026-10-27_诸葛C3排程与C1生产接线_L2五要素待批包_v1.md）
# 任务：ZhugeChallengeDaily（每日 09:10）；优先读沙盘仓 plans/ 最新快照，无可用回落 .worktrees/tasks.json；
# 仅生成规则模板质询卡；不接 Kanban/不调模型/不写沙盘。
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\tools\zhuge_challenge.py'
$log = 'C:\Temp\zhuge_challenge_daily.log'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
# 显式 UTF-8 解码子进程输出（否则 Windows PowerShell 按本地码 cp936 解码 → 中文日志乱码）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$out = & $py -X utf8 $script emit --auto-snapshot --max-questions 12 --json 2>&1
$rc = $LASTEXITCODE
$out | Out-File -FilePath $log -Append -Encoding utf8
exit $rc
