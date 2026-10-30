# -*- coding: utf-8 -*-
"""agent_platform 测试路径守卫（2026-10-30 P78）。

背景：部分模块用 `from tools.safe_urlopen import ...`（仓库根 tools 包/目录）。
pytest 默认 prepend 模式会把测试目录/agent_platform 插到 sys.path 前部，但仓库根
可能不在前部 → `tools` 解析到 agent_platform/tools（无 __init__ 的 namespace）导致
16 个模块收集错误（ModuleNotFoundError: 'tools' is not a package）。

本 conftest 只做两件事（测试期生效，不影响生产）：
1. 把仓库根插到 sys.path 最前；
2. 清掉之前可能误绑的 `tools` 相关 sys.modules 条目，让解析重新开始。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 仓库根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _name in [m for m in list(sys.modules) if m == "tools" or m.startswith("tools.")]:
    sys.modules.pop(_name, None)
