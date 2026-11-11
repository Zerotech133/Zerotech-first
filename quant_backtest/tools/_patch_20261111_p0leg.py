# -*- coding: utf-8 -*-
"""2026-11-11 把 P0 观察腿挂进 pool_daily（想法 1，任务 #103）。"""
from pathlib import Path

p = Path("pool_daily.py")
b = p.read_bytes()
print("CRLF =", b.count(bytes([13, 10])), "LF =", b.count(bytes([10])))
nl = bytes([10])  # 实测 LF=233 纯 LF 文件

anchor = nl.join([
    '    steps.append(("影子台账（shadow --daily）",'.encode("utf-8"),
    '                  ["shadow_ab.py", "--daily"], True))'.encode("utf-8"),
])
addition = nl.join([
    '    # P0 观察腿（2026-11-11 想法1）：外部组合 × 结构池每日对照。'.encode("utf-8"),
    '    # soft——data/external_portfolio.json 不存在时脚本自己跳过；'.encode("utf-8"),
    '    # 纯观察层不产信号，失败不挡流水线。'.encode("utf-8"),
    '    steps.append(("外部组合 P0 观察", ["portfolio_watch.py"], True))'.encode("utf-8"),
])
if b"portfolio_watch.py" in b:
    print("SKIP (already applied)")
else:
    cnt = b.count(anchor)
    assert cnt == 1, "anchor count=" + str(cnt)
    p.write_bytes(b.replace(anchor, anchor + nl + addition, 1))
    back = p.read_bytes()
    assert b"portfolio_watch.py" in back
    print("p0-leg hooked OK")
