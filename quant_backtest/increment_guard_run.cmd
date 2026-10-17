@echo off
rem K-STOCK-211 六表增量接管通道计划任务包装（每日 20:30，2026-10-17 用户裁决）
rem 日志滚动：单文件 append，超 1MB 由 increment_guard 侧报告 JSON 承载明细
cd /d C:\aios\quant_backtest
python -u increment_guard.py >> ..\work\increment_guard_schtask.log 2>&1
