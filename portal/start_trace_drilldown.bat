@echo off
rem A 方案 trace+血缘 下钻看板 —— 一键只读启动
rem 绑定 127.0.0.1:8106（仅本机）；停止：Ctrl+C 或任务管理器结束 python
cd /d "%~dp0"
"C:\Program Files\Python311\python.exe" -X utf8 api_trace_drilldown.py --port 8106
