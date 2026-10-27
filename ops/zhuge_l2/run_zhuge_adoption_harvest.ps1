# run_zhuge_adoption_harvest.ps1 — C3 采纳率归集包装器
# L2 批准：老田 2026-10-27（五要素包见 C:\项目\deliverables\2026-10-27_诸葛C3排程与C1生产接线_L2五要素待批包_v1.md）
# 任务：ZhugeAdoptionHarvest（每 30 分钟）；只追加 portal/data/zhuge_adoption.jsonl，不改页面/不重启服务。
$py = 'C:\Program Files\Python311\python.exe'
$script = 'C:\aios\portal\harvest_adoption.py'
$log = 'C:\Temp\zhuge_adoption_harvest.log'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
# 显式 UTF-8 解码子进程输出（否则 Windows PowerShell 按本地码 cp936 解码 → 中文日志乱码）
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$out = & $py -X utf8 $script 2>&1
$rc = $LASTEXITCODE
$out | Out-File -FilePath $log -Append -Encoding utf8
exit $rc
