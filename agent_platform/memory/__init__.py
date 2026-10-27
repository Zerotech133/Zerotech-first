# -*- coding: utf-8 -*-
"""赵云 MCP 包记忆 v2 测试。"""


# 兼容层：旧消费方（quant_backtest/chanlun_learn、run_invest/run_shadow 等）使用
# ``from memory import MemoryStore``（agent_platform/engine/memory.py 的四级 GNS 实现）。
# 不依赖 sys.path 上的 ``engine`` 名字，避免与其它同名模块/包冲突；按文件路径加载。
def _load_engine_memory():
    import importlib.util
    from pathlib import Path as _Path
    path = _Path(__file__).resolve().parent.parent / "engine" / "memory.py"
    spec = importlib.util.spec_from_file_location("_aios_engine_memory", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_engine_memory = _load_engine_memory()
MemoryStore = _engine_memory.MemoryStore
PermissionDenied = _engine_memory.PermissionDenied

__all__ = ["MemoryStore", "PermissionDenied"]
