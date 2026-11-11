# -*- coding: utf-8 -*-
"""2026-11-11 BUG 修复：backtest_engine_v2 指数净值缺失时的硬崩。

全量回归（2469 passed / 20 failed）暴露：五档 regime_tier_of 上线后，
合成契约夹具与指数净值加载失败路径都会因 idx_nav=None 抛 AttributeError。
regime_of 有同样隐患。两处统一 soft 降级到中性档（mid / tier 2），
生产环境 idx_nav 正常时该分支不触发，回测结果不变。

按 CRLF 字节替换（原文件 2683 行全 CRLF），锚点 count==1 前置断言，
替换后读回断言新串存在、旧串消失。
"""
from pathlib import Path

CRLF = chr(13) + chr(10)
TARGET = "backtest_engine_v2.py"


def patch(old_lines, new_lines, label):
    p = Path(TARGET)
    b = p.read_bytes()
    old = CRLF.join(old_lines).encode("utf-8")
    new = CRLF.join(new_lines).encode("utf-8")
    if new in b:  # 幂等：首次执行已写入的分支直接跳过
        print(label, "SKIP (already applied)")
        return
    cnt = b.count(old)
    assert cnt == 1, label + " anchor count=" + str(cnt)
    p.write_bytes(b.replace(old, new, 1))
    back = p.read_bytes()
    # 注意：new 本身包含 old 作为尾部，不能用 "old not in back" 判定；
    # 改判 new 存在 + guard 行全文只出现一次（防重复插入）。
    assert new in back, label + " verify failed"
    guard = new_lines[0].encode("utf-8")
    assert back.count(guard) == 1, label + " guard duplicated"
    print(label, "OK")


patch(
    [
        "    i = idx_nav.index.get_loc(day)",
        "    if i == 0:",
        '        return "mid", "首日默认"',
    ],
    [
        "    if idx_nav is None:  # 11-11 回归：合成夹具/装载失败不硬崩",
        '        return "mid", "指数净值缺失·中性档（soft 降级）"',
        "    i = idx_nav.index.get_loc(day)",
        "    if i == 0:",
        '        return "mid", "首日默认"',
    ],
    "regime_of-none-guard",
)

patch(
    [
        "    i = idx_nav.index.get_loc(day)",
        "    if i == 0:",
        '        return 2, "首日默认"',
    ],
    [
        "    if idx_nav is None:  # 11-11 回归：五档与三档同口径降级",
        '        return 2, "指数净值缺失·中性档（soft 降级）"',
        "    i = idx_nav.index.get_loc(day)",
        "    if i == 0:",
        '        return 2, "首日默认"',
    ],
    "regime_tier_of-none-guard",
)
