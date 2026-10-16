# -*- coding: utf-8 -*-
"""pytest 路径引导：确保从任意目录（含 strategy_layer/ 本目录）运行时，
quant_backtest 都在 sys.path 前列，使 ``import strategy_layer`` 可用。"""
import os
import sys

_QB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _QB_ROOT not in sys.path:
    sys.path.insert(0, _QB_ROOT)
