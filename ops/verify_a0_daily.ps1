# A0 daily board verifier (ASCII only)
# Runs daily at 08:05 after 07:50 board; checks rollup.json truth.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Date = Get-Date -Format 'yyyy-MM-dd'
$File = Join-Path $Root ("deliverables\daily_briefs\" + $Date + "\rollup.json")
$Log = 'C:\Temp\a0_daily_verify.log'
$Out = 'C:\Temp\a0_daily_verify_' + $Date + '.json'
$ok = $false
for ($i = 0; $i -lt 6; $i++) {
    if (Test-Path $File) {
        try {
            $d = Get-Content $File -Raw -Encoding utf8 | ConvertFrom-Json
            $expected = [int]$d.expected
            $submitted = [int]$d.submitted
            $failed = @($d.failed).Count
            $res = [ordered]@{
                date = $Date; file = $File; ok = [bool]$d.ok
                expected = $expected; submitted = $submitted; failed = $failed
                checked_at = (Get-Date).ToString('o')
            }
            $res | ConvertTo-Json -Depth 4 | Out-File $Out -Encoding utf8
            ('[' + (Get-Date -Format s) + '] ' + ($res | ConvertTo-Json -Compress -Depth 4)) | Out-File $Log -Append -Encoding utf8
            if ($res.ok -and $expected -eq 17 -and $submitted -eq 17 -and $failed -eq 0) { $ok = $true }
            break
        } catch {
            ('[' + (Get-Date -Format s) + '] parse_error ' + $_.Exception.Message) | Out-File $Log -Append -Encoding utf8
        }
    }
    Start-Sleep -Seconds 30
}
if (-not $ok) {
    ('[' + (Get-Date -Format s) + '] VERIFY_FAIL file=' + $File) | Out-File $Log -Append -Encoding utf8
    exit 2
}
exit 0
