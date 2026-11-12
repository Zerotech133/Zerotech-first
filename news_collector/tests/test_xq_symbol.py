# -*- coding: utf-8 -*-
"""雪球 symbol 前缀映射回归测试（OPS-1113-01）。

病灶：`symbol = ("SH" if code.startswith(("6", "9")) else "SZ") + code`
把北交所 92xxxx 拼成 SH920870，雪球返回空列表且 HTTP 200 —— 静默空、
不进 deferred、SLA 之前完全不可见。本测试锁死映射表，防回退。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
NC = os.path.dirname(HERE)
if NC not in sys.path:
    sys.path.insert(0, NC)

# xq_kol 在模块级 import playwright，而本测试只验纯函数；
# 打桩顶替，使测试在未装 playwright 的解释器（如 3.13）上同样执行，
# 而不是被 skip 掉——跳过等于这条回归护栏形同虚设。
if "xq_kol" not in sys.modules:
    _stub = types.ModuleType("xq_kol")
    _stub.PROFILE_DIR = ""
    _stub.PROFILE_FALLBACK = ""
    sys.modules["xq_kol"] = _stub

import xq_stock_timeline as m  # noqa: E402


def _n(*digits):
    """按码点构造数字串（防输出/传输通道串味，见用户级铁律）。"""
    return "".join(chr(48 + d) for d in digits)


BJ = [_n(9, 2, 0, 8, 7, 0), _n(4, 3, 0, 0, 4, 7), _n(8, 3, 0, 7, 9, 9), _n(8, 7, 1, 9, 8, 1)]
SH = [_n(6, 0, 0, 5, 1, 9), _n(6, 8, 8, 9, 8, 1), _n(9, 0, 0, 9, 0, 1), _n(5, 1, 0, 3, 0, 0)]
SZ = [_n(0, 0, 0, 8, 5, 8), _n(3, 0, 0, 7, 5, 0), _n(2, 0, 0, 5, 9, 6)]


def test_beijing_codes_map_to_bj():
    """北交所旧段 43/83/87 与新段 92 一律 BJ（这是 OPS-1113-01 的正题）。"""
    for c in BJ:
        sym = m.to_symbol(c)
        assert sym == "BJ" + c, (c, sym)
        assert not sym.startswith("SH"), c


def test_shanghai_codes_map_to_sh():
    """6/9/5 仍为沪市——9 开头除 92 北交所外还有沪 B 900xxx，不能被误伤成 BJ。"""
    for c in SH:
        assert m.to_symbol(c) == "SH" + c, c


def test_shenzhen_codes_map_to_sz():
    for c in SZ:
        assert m.to_symbol(c) == "SZ" + c, c


def test_suffix_stripped_and_padded():
    """注册表里代码可能带 .SH/.BJ 后缀，也可能缺前导零。"""
    assert m.to_symbol(BJ[0] + ".BJ") == "BJ" + BJ[0]
    assert m.to_symbol("858") == "SZ" + "000858"


def test_no_bare_hash_hint_in_source():
    """防止有人把映射改回「6 或 9 即 SH」的一行写法。"""
    src_path = os.path.join(NC, "xq_stock_timeline.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    assert 'startswith(("6", "9")) else "SZ"' not in src
    assert "to_symbol(code)" in src
