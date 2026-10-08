# K266 文档服务自愈脚本（计划任务 K266-DocServices 每 5 分钟触发）
# 职责：① 拉起 clip_watch 常驻（单实例检查） ② doc_ingest --once 幂等单扫
#       ③ M3 结构化（幂等跳过已判 doc，产出 structure_shadow tags → /kg/doc 词云）
$ErrorActionPreference = 'SilentlyContinue'
$root = 'E:\老田的AI助手'
$log = 'C:\Temp\k266_services.log'

function Log($msg) {
    "$(Get-Date -Format 'MM-dd HH:mm:ss')  $msg" | Out-File $log -Append -Encoding utf8
}

# --- 1) clip_watch 单实例拉起（pythonw 无窗口常驻） ---
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'k266_clip_watch' }
if (-not $running) {
    Start-Process -FilePath pythonw -ArgumentList '-X','utf8',"$root\quant_backtest\k266_clip_watch.py" `
        -WorkingDirectory "$root\quant_backtest" -WindowStyle Hidden
    Log "clip_watch: 拉起（先前不在岗）"
} else {
    Log "clip_watch: 在岗 PID=$($running.ProcessId -join ',')"
}

# --- 2) doc_ingest 幂等单扫（registry 追踪，无新文件时空转退出） ---
$env:PADDLE_PDX_CACHE_HOME = 'C:\Temp\paddlex'
$env:PYTHONPYCACHEPREFIX   = 'C:\Temp\pycache'
$env:MODELSCOPE_CACHE      = 'C:\Temp\modelscope'
$out = & python -X utf8 "$root\quant_backtest\k266_doc_ingest.py" --once 2>&1
$rc = $LASTEXITCODE
if ($out) { $out | Out-File 'C:\Temp\k266_ingest.log' -Append -Encoding utf8 }
Log "ingest --once rc=$rc $($out -join ' | ')"

# --- 3) M3 结构化（幂等：只判新增 doc；Ollama qwen3.5:9b → tags 入 structure_shadow） ---
# 仅当本扫有新增（输出含 "OK d"）才跑，空转不占 Ollama
if ("$out" -match 'OK d') {
    $m3 = & python -X utf8 "$root\quant_backtest\k266_m3_structure.py" 2>&1
    $rc3 = $LASTEXITCODE
    if ($m3) { $m3 | Out-File 'C:\Temp\k266_m3.log' -Append -Encoding utf8 }
    Log ("m3 rc=$rc3 last=" + (($m3 | Select-Object -Last 1)))
}
