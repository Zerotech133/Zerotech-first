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
collect_ignore = [
    "test_feishu_event_guard.py",
    "test_portal_key_rotation.py",
    "test_publish_allowlist.py",
]
