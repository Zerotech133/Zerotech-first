# -*- coding: utf-8 -*-
"""pytest 收集排除：脚本式自检文件（K-STOCK-211 任务 4，2026-10-17）。

以下文件设计为 `python portal/<file>` 直跑（module 体执行 + 顶层 sys.exit），
pytest 收集会出问题：
- test_feishu_event_guard.py：module 级 `api_misc.subprocess.run = ...` patch
  不恢复，且 api_misc.subprocess 与全局 subprocess 模块同对象 → 收集即
  全进程污染（82 文件 AttributeError: SimpleNamespace has no stdout）；
- test_portal_key_rotation.py / test_publish_allowlist.py：顶层
  `sys.exit(1 if FAIL else 0)` → 收集即 INTERNALERROR。

排除收集不影响直跑；回归门禁 = `python -m pytest portal quant_backtest -q`。
"""

import os
import sys

# 2026-11-08 P1-6 分层落地（第 3 层）：修 sys.path，让根级 import 型测试可被收集。
#
# 背景：pytest.ini 用 `--import-mode=importlib`，pytest 只把 rootdir（仓库根）
# 放进 sys.path，`portal/` 自身不在其中。于是 portal 根级测试里那些
# `from watch_tower_linkage import ...` / `import api_common as api` 的裸 import
# 在「从仓库根收集」时必然 ModuleNotFoundError —— 表现为「收集即错」，
# 长期落在任何套件之外（P1-6 实测 4 个文件、28 红 8 绿，1,663 条从未跑过）。
#
# 用 append 而不是 insert(0)：append 只在「原本找不到」时才命中，
# 绝不会顶掉仓库根的同名包 —— 正是 P0-3 / P2-5 那种「根同名包遮蔽 import」
# 病的反面做法。重复加载 conftest 时判重，避免 sys.path 里堆重复项。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.append(_HERE)

collect_ignore = [
    "test_feishu_event_guard.py",
    "test_portal_key_rotation.py",
    "test_publish_allowlist.py",
]
