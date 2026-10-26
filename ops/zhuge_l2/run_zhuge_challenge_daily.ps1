# run_zhuge_challenge_daily.ps1 — C1 质询卡每日生成包装器（离线版）
# L2 批准：老田 2026-10-27（五要素包见 C:\项目\deliverables\2026-10-27_诸葛C3排程与C1生产接线_L2五要素待批包_v1.md）
# 任务：ZhugeChallengeDaily（每日 09:10）；读 .worktrees/tasks.json doing 快照→规则模板质询卡；不接 Kanban/不调模型/不写沙盘。
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\tools\zhuge_challenge.py'
$tasks = 'C:\aios\.worktrees\tasks.json'
$log = 'C:\Temp\zhuge_challenge_daily.log'
& $py -X utf8 $script emit --tasks $tasks --max-questions 12 --json *>> $log
exit $LASTEXITCODE
