# -*- coding: utf-8 -*-
"""给 tools/test_gate.ps1 注入日志目录覆盖能力（K-CI-20261108-LOGDIR）。"""
from pathlib import Path

p = Path(r"C:\项目\老田的AI助手\tools\test_gate.ps1")
txt = p.read_bytes().decode("utf-8-sig")

old_ts = "$ts = Get-Date -Format 'yyyyMMdd_HHmmss'"
assert txt.count(old_ts) == 1, txt.count(old_ts)

block = "\r\n".join([
    old_ts,
    "# 2026-11-08（K-CI-20261108-LOGDIR）：日志目录可注入。云端 runner 的 $env:TEMP 是",
    r"# C:\Users\RUNNER~1\AppData\Local\Temp，而 upload-artifact 的 ${{ runner.temp }} 是",
    r"# D:\a\_temp —— 两者不同，artifact 恒空（if-no-files-found: ignore 还把它吞了），",
    "# 几十项失败明细随 runner 一起销毁，只能靠猜补登记。CI 侧传 TEST_GATE_LOG_DIR 对齐。",
    "$logDir = $env:TEMP",
    "if ($env:TEST_GATE_LOG_DIR) { $logDir = $env:TEST_GATE_LOG_DIR }",
])
txt = txt.replace(old_ts, block)

n = txt.count('Join-Path $env:TEMP "test_gate_')
assert n == 8, n
txt = txt.replace('Join-Path $env:TEMP "test_gate_', 'Join-Path $logDir "test_gate_')
assert '$env:TEMP "test_gate_' not in txt

p.write_bytes(b"\xef\xbb\xbf" + txt.encode("utf-8"))
print("ok; logDir refs:", txt.count('Join-Path $logDir "test_gate_'))
