# -*- coding: utf-8 -*-
"""pytest 路径注入：确保 intel_collector 包根可导入（core/adapters/bridges/registry/store）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
