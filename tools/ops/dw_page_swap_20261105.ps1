# 11-05 /dw.html 页面替换 wrapper（canonical 实现: dw_page_swap_20261105.py）
param(
    [switch]$Apply,
    [switch]$Rollback
)
$py = 'C:\项目\py312\python.exe'
$pyArgs = @('tools/ops/dw_page_swap_20261105.py')
if ($Rollback) { $pyArgs += '--rollback' }
elseif ($Apply) { $pyArgs += '--apply' }
& $py -X utf8 @pyArgs
exit $LASTEXITCODE
