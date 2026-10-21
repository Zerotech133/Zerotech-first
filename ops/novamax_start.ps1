# NovaMax llama-server start v2 (ports 1234/1238)
# Exact same command lines as originally spawned by NovaStudio (captured 2026-10-18 16:27).
# Skips a port if it is already listening (idempotent). Removes guardian OFF switch first.
$offFile = 'C:\Users\t\AppData\Local\Programs\Ollama\nova_guard.off'
if (Test-Path $offFile) { Remove-Item $offFile -Force -Confirm:$false; Write-Host "OFF switch removed - nova_guard auto-start re-enabled." }
$exe  = 'C:\LingLong\NovaStudio\NovaMax\external\llamacpp\vulkan\vulkan_202609291528\llama-server.exe'
$dir  = 'C:\LingLong\NovaStudio\NovaMax\external\llamacpp\vulkan\vulkan_202609291528'
$m35  = 'C:\LingLong\NovaStudio\NovaMax\data\models_dir\llm\unsloth\Qwen3.5-35B-A3B-GGUF\Qwen3.5-35B-A3B-Q4_K_M.gguf'
$mm35 = 'C:\LingLong\NovaStudio\NovaMax\data\models_dir\llm\unsloth\Qwen3.5-35B-A3B-GGUF\mmproj-BF16.gguf'
$m30  = 'C:\LingLong\NovaStudio\NovaMax\data\models_dir\llm\unsloth\Qwen3-30B-A3B-Instruct-2507-GGUF\Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf'

$jobs = @(
    @{ Port = 1234; Args = @('-m', $m35, '--host','0.0.0.0','--alias','Qwen3.5-35B-A3B-Q4_K_M.gguf','--port','1234','--parallel','1','--load-mode','none','--temperature','0.7','--top-p','0.9','--top-k','40','--repeat-penalty','1.1','--reasoning','off','--n-gpu-layers','100','--mmproj',$mm35) },
    @{ Port = 1238; Args = @('-m', $m30, '--host','0.0.0.0','--alias','Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf','--port','1238','--parallel','1','--load-mode','none','--temperature','0.7','--top-p','0.9','--top-k','40','--repeat-penalty','1.1','--reasoning','off','--n-gpu-layers','100') }
)

foreach ($j in $jobs) {
    $busy = Get-NetTCPConnection -LocalPort $j.Port -State Listen -ErrorAction SilentlyContinue
    if ($busy) { Write-Host ("SKIP port {0}: already listening." -f $j.Port); continue }
    Start-Process -FilePath $exe -WorkingDirectory $dir -ArgumentList $j.Args -WindowStyle Minimized
    Write-Host ("LAUNCHED port {0}: model loading (Vulkan, may take 1-2 min)..." -f $j.Port)
}

# Health wait up to 150s
$deadline = (Get-Date).AddSeconds(150)
$ok = @()
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $ok = @()
    foreach ($j in $jobs) {
        try {
            $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/health" -f $j.Port) -UseBasicParsing -TimeoutSec 2
            if ($r.Content -match 'ok') { $ok += $j.Port }
        } catch { }
    }
    if ($ok.Count -eq $jobs.Count) { break }
}
Write-Host ""
if ($ok.Count -eq $jobs.Count) { Write-Host "OK: 1234 + 1238 both healthy." }
else { Write-Host ("PARTIAL: healthy ports = " + ($ok -join ',') + " (models still loading or failed - check nova window/logs).") }
