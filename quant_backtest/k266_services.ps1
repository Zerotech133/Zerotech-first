# K266 文档服务自愈脚本（计划任务 K266-DocServices 每 5 分钟触发）
# 职责：① 拉起 clip_watch 常驻（单实例检查） ② doc_ingest --once 幂等单扫
#       ③ M3 结构化（幂等跳过已判 doc，产出 structure_shadow tags → /kg/doc 词云）
$ErrorActionPreference = 'SilentlyContinue'
# 2026-10-16 换机修复：E: 盘不存在 → C:\aios junction（原硬编码 C:\项目\老田的AI助手）
$root = 'C:\aios'
$log = 'C:\Temp\k266_services.log'

function Log($msg) {
    "$(Get-Date -Format 'MM-dd HH:mm:ss')  $msg" | Out-File $log -Append -Encoding utf8
}

# --- 1) clip_watch 单实例拉起（pythonw 无窗口常驻） ---
$running = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'k266_clip_watch' }
if (-not $running) {
    Start-Process -FilePath 'C://Program Files\Python311\pythonw.exe' -ArgumentList '-X','utf8',"$root\quant_backtest\k266_clip_watch.py" `
        -WorkingDirectory "$root\quant_backtest" -WindowStyle Hidden
    Log "clip_watch: 拉起（先前不在岗）"
} else {
    Log "clip_watch: 在岗 PID=$($running.ProcessId -join ',')"
}

# --- 2) doc_ingest 幂等单扫（registry 追踪，无新文件时空转退出） ---
$env:PADDLE_PDX_CACHE_HOME = 'C:\Temp\paddlex'
$env:PYTHONPYCACHEPREFIX   = 'C:\Temp\pycache'
$env:MODELSCOPE_CACHE      = 'C:\Temp\modelscope'
$out = & 'C:\Program Files\Python311\python.exe' -X utf8 "$root\quant_backtest\k266_doc_ingest.py" --once 2>&1
$rc = $LASTEXITCODE
if ($out) { $out | Out-File 'C:\Temp\k266_ingest.log' -Append -Encoding utf8 }
Log "ingest --once rc=$rc $($out -join ' | ')"

# --- 3) M3 结构化（幂等：只判新增 doc；Ollama qwen3.5:9b → tags 入 structure_shadow） ---
# 仅当本扫有新增（输出含 "OK d"）才跑，空转不占 Ollama；单实例守卫防并发重判
if ("$out" -match 'OK d') {
    $m3run = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'k266_m3_structure' }
    if ($m3run) {
        Log "m3: 已在跑 PID=$($m3run.ProcessId -join ',')，本轮跳过"
    } else {
        # 异步拉起（同 clip_watch 模式）：M3 补积压可达数十分钟，同步等待会阻塞
        # 本 tick 的后续环节并吞掉 12:06-12:46 全部调度窗（12-08 实测 46 分钟）
        Start-Process -FilePath 'C://Program Files\Python311\python.exe' -ArgumentList '-X','utf8',"$root\quant_backtest\k266_m3_structure.py" `
            -WorkingDirectory "$root\quant_backtest" -WindowStyle Hidden `
            -RedirectStandardOutput 'C:\Temp\k266_m3.log' -RedirectStandardError 'C:\Temp\k266_m3_err.log'
        Log "m3: 异步拉起（进度见影子增长，结束见 k266_m3.log）"
    }
}
