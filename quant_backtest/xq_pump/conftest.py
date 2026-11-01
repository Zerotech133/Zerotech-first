# -*- coding: utf-8 -*-
"""pytest 配置：把 quant_backtest 加入路径，使 xq_pump 可作为包导入。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)
