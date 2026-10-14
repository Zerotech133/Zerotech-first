#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试引导：把 tools/ 加入 sys.path 使 gatekeeper 可导入。

测试数据一律合成/临时目录，不读大文件（验收硬约束）。
"""

import os
import sys

TOOLS_DIR = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
