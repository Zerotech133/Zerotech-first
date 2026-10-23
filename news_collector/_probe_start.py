# -*- coding: utf-8 -*-
"""计划任务启动探针：只打印并 exit 3，不碰任何生产逻辑。

用途：2026-10-22 诊断 AlertWatch 的 0x80070003（路径未找到）——
所有路径都真实存在，而结构相同的 QuantWatchdog 能跑。
把本文件放到不同目录、用不同参数创建任务，可定位到底是"目录"还是"参数"的问题。
"""
import os
import sys

print("PROBE ok")
print("cwd =", os.getcwd())
print("argv =", sys.argv)
sys.exit(3)
