# RAG 40 题周度复评（strict_nocite 引用口径）
#
# 用法（手动/计划任务均可）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools/scheduled/rag_weekly_eval.ps1
#
# 输出（按日期归档，不覆盖 11-04 W17 基线报告）：
#   research/reports/bailian_eval_weekly_YYYYMMDD.{json,md}
#   C:\Temp\rag_weekly_eval.log（运行日志）
# 仅调用已部署模型 API，不写生产库。
# 注意：本文件必须存为 UTF-8 BOM，Windows PowerShell 5.1 才能正确解析中文路径。
$ErrorActionPreference = "Stop"
$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Py = "C:\Program Files\Python311\python.exe"
$BaseFile = "C:\项目\_vault\secrets\dashscope_base_url.txt"
$Log = "C:\Temp\rag_weekly_eval.log"
$Model = "qwen2.5-14b-instruct-a57a3521e4e0"

$base = (Get-Content $BaseFile -Raw).Trim()
$date = Get-Date -Format "yyyyMMdd"
$outJson = Join-Path $Repo "research\reports\bailian_eval_weekly_$date.json"
$outMd = Join-Path $Repo "research\reports\bailian_eval_weekly_$date.md"
$env:PYTHONIOENCODING = "utf-8"

& $Py -X utf8 (Join-Path $Repo "tools\finetune\bailian_eval.py") `
    --model $Model --base-url $base --prompt-profile strict_nocite --write `
    --out-json $outJson --out-md $outMd *>> $Log
if ($LASTEXITCODE -ne 0) { throw "bailian_eval failed rc=$LASTEXITCODE (see $Log)" }
Write-Output "weekly eval done: $outJson"

