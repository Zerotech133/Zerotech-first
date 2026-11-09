# -*- coding: utf-8 -*-
"""C 类归档前置守卫：xueqiu_common 在治理外壳 4 件缺席时必须可导入且优雅降级。

背景（2026-11-01 归档决策 C 类核实）：
  xq_proxy / xq_browser / xq_cookie_patch / xq_tokens 被指为"硬依赖不可归档"。
  实测发现 xueqiu_common / xueqiu_sync 对它们的引用**全部已是 try/except 可选降级**
  （失败可见：logging.warning，非静默假绿）。即：4 件缺席时模块仍可导入，
  代理/token 池/浏览器 cookie 功能优雅关闭，匿名直连链仍可用。

本测试用 meta_path 钩子模拟 4 件模块缺席，钉死该性质：
  若有人把可选降级改成硬 import（导致缺席即崩），本测试立即变红。

注意：本测试不触网——只验证 import 安全与降级路径的结构性，
不调用 warm_session（其第 4 级会发真实首页请求）。
"""
import sys
from pathlib import Path

import pytest

QB_ROOT = Path(__file__).resolve().parent.parent
if str(QB_ROOT) not in sys.path:
    sys.path.insert(0, str(QB_ROOT))

_ABSENT = ("xq_proxy", "xq_browser", "xq_cookie_patch", "xq_tokens")


class _BlockModules:
    """meta_path 钩子：令指定模块'缺席'（find_spec 抛 ImportError）。"""

    def find_spec(self, name, path=None, target=None):
        if name in _ABSENT:
            raise ImportError("simulated absence: " + name)
        return None


def test_xueqiu_common_importable_without_c_class():
    """4 件治理外壳缺席时 xueqiu_common 必须可导入（可选降级性质）。"""
    import importlib
    import xueqiu_common  # noqa: F401  先确保正常态可导入
    block = _BlockModules()
    sys.meta_path.insert(0, block)
    try:
        # 强制重新加载：在缺席钩子生效下导入必须成功
        mod = importlib.reload(xueqiu_common)
        assert mod is not None
        # 关键函数仍存在（降级实现，不因缺席丢符号）
        for fn in ("warm_session", "xq_get", "jitter_sleep", "norm_symbol"):
            assert hasattr(mod, fn), f"缺席态丢失符号 {fn}"
    finally:
        sys.meta_path.remove(block)
        importlib.reload(xueqiu_common)  # 恢复正常态，不污染其它测试


def test_absent_modules_really_blocked():
    """钩子自检：缺席钩子确实拦截（防止测试假绿）。

    2026-11-08 修隔离缺陷：Python 的 import 语句**先查 sys.modules**，命中即
    直接返回，根本不走 meta_path。xq_proxy 在盘上真实存在（16.8KB），全量跑
    时前序任何一次导入都会把它缓存进 sys.modules，本自检就恒 DID NOT RAISE
    而假红（单独跑本文件时无人缓存，所以是绿的——典型的顺序依赖）。
    修法：断言前把 4 件从 sys.modules 摘掉，结束后连同前序状态一起还原。
    """
    saved = {m: sys.modules.pop(m, None) for m in _ABSENT}
    block = _BlockModules()
    sys.meta_path.insert(0, block)
    try:
        with pytest.raises(ImportError):
            import xq_proxy  # noqa: F401
    finally:
        sys.meta_path.remove(block)
        # 清理可能半导入的模块状态，再把前序测试留下的缓存放回去（不污染下游）
        for m in _ABSENT:
            sys.modules.pop(m, None)
        for m, mod in saved.items():
            if mod is not None:
                sys.modules[m] = mod
