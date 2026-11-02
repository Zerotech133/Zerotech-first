# shipinhao cards routine launcher (ASCII only)
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$log = 'C:\Temp\shipinhao_cards_task.log'
$py = 'C:\Program Files\Python311\python.exe'
$repo = 'C:\aios'
Set-Location $repo
$out = 'C:\Temp\shipinhao_cards_routine.out'
$err = 'C:\Temp\shipinhao_cards_routine.err'
& $py -X utf8 tools/kb_corpus/shipinhao_cards_routine.py --write 1> $out 2> $err
$rc = $LASTEXITCODE
('exit=' + $rc + ' ' + (Get-Date -Format s)) | Out-File $log -Append -Encoding utf8
if ($rc -ne 0) {
    & $py -X utf8 C:\aios\news_collector\alert_fail_wrap.py shipinhao_cards $rc $err 1>$null 2>$null
}
exit $rc
