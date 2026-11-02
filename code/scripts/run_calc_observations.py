# -*- coding: utf-8 -*-
"""观察计算任务入口（供 Windows 计划任务直接调用，避免中文路径 .ps1 编码问题）。"""
import os
import runpy
import sys
from pathlib import Path


CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "src"))
runpy.run_path(str(CODE / "scripts/calc_observations.py"), run_name="__main__")
