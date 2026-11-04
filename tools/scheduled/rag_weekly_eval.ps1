# -*- coding: utf-8 -*-
"""RAG 40 题周度复评（strict 生产口径）。

用法（手动/计划任务均可）：
  powershell -NoProfile -ExecutionPolicy Bypass -File tools/scheduled/rag_weekly_eval.ps1

输出：
  - research/reports/bailian_eval_20261104.{json,md}（最新）
  - research/reports/bailian_eval_weekly_YYYYMMDD.{json,md}（按日归档）
  - C:\\Temp\\rag_weekly_eval.log（运行日志）
仅调用部署模型 API，不写生产库。
"""
$ErrorActionPreference = "Stop"
$Repo = "C:\项目\老田的AI助手"
$Py = "C:\项目\py312\python.exe"
$BaseFile = "C:\项目\_vault\secrets\dashscope_base_url.txt"
$Log = "C:\Temp\rag_weekly_eval.log"
$Model = "qwen2.5-14b-instruct-a57a3521e4e0"

$base = (Get-Content $BaseFile -Raw).Trim()
$date = Get-Date -Format "yyyyMMdd"
$env:PYTHONIOENCODING = "utf-8"

& $Py -X utf8 "$Repo\tools\finetune\bailian_eval.py" --model $Model --base-url $base --prompt-profile strict --write *>> $Log
if ($LASTEXITCODE -ne 0) { throw "bailian_eval failed rc=$LASTEXITCODE (see $Log)" }

Copy-Item "$Repo\research\reports\bailian_eval_20261104.json" "$Repo\research\reports\bailian_eval_weekly_$date.json" -Force
Copy-Item "$Repo\research\reports\bailian_eval_20261104.md" "$Repo\research\reports\bailian_eval_weekly_$date.md" -Force
Write-Output "weekly eval done: bailian_eval_weekly_$date"
