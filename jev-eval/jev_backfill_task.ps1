# VERSION: jev_backfill_task v2 (2026-10-11, traceback-full-log fix)
$ErrorActionPreference = 'Stop'
$log = 'C:\jev_backfill_task.log'
function W([string]$m) {
    ("[{0}] {1}" -f (Get-Date -Format 'MM-dd HH:mm:ss'), $m) |
        Out-File -LiteralPath $log -Append -Encoding utf8
}
try {
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
    W 'P0 start'
    # locate Chinese-path news_collector via marker file (ASCII cmdline rule)
    $dir = $null
    foreach ($drv in @('E:\', 'D:\', 'C:\')) {
        if (-not (Test-Path -LiteralPath $drv)) { continue }
        $hit = Get-ChildItem -LiteralPath $drv -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                $sub = Join-Path $_.FullName 'news_collector'
                if (Test-Path -LiteralPath (Join-Path $sub 'jev_backfill.py')) { $sub }
            } | Select-Object -First 1
        if ($hit) { $dir = $hit; break }
    }
    if (-not $dir) { throw 'news_collector dir not found on E:/D:/C:' }
    W ('P1 dir=' + $dir)
    # python resolution
    $py = 'C:\Program Files\Python311\python.exe'
    if (-not (Test-Path -LiteralPath $py)) {
        $py = (Get-Command python.exe -ErrorAction Stop).Source
    }
    # bounded batch; Ollama down -> judged=0, harmless idle (exit 0)
    # v2 fix(2026-10-11): Stop 偏好下 stderr 任意一行会变 ErrorRecord 直接 throw,
    #   traceback 只剩首行——native 段局部改 Continue, 全文由 P2 落盘
    $ErrorActionPreference = 'Continue'
    $out = & $py -X utf8 (Join-Path $dir 'jev_backfill.py') --batch 500 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    W ('P2 exit=' + $code + ' out=' + ($out -join ' | '))
    # log cap ~400KB: keep tail
    if ((Get-Item -LiteralPath $log).Length -gt 400KB) {
        $tail = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8
        Set-Content -LiteralPath $log -Value $tail -Encoding UTF8
        W 'P3 log rotated'
    }
    exit $code
} catch {
    try { W ('ERR ' + $_.Exception.Message) } catch {}
    exit 1
}
