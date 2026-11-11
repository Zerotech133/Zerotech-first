# -*- coding: utf-8 -*-
"""2026-11-11 补齐 tbe24/mbei 事件字段（任务 #101，一买线验证前置）。

缺口（任务 #100 实测发现）：chanlun_signal 给 1买/1卖 生成
tbe24/tfv24（24课趋势背驰）与 mbei/mfv（MACD 背驰），但 market_scan
scan_stock 的 out[(code,type)] 字典只落 bei/bei2/bei24 → 事件缓存里
一买档这些字段全 None，趋势背驰前瞻验证不可行。

修复：out 字典补 tbe24/mbei 两键（sg.get 缺省 None，3买/3卖 天然 None）。
纯字段增补：不改评分（score 只在 3买 计算）、不改信号、不算口径变更。
锚点 count==1（CRLF 文件按字节替换）+ 读回断言。
"""
from pathlib import Path

TARGET = "market_scan.py"
NL = bytes([13, 10])

c1 = "# 2026-11-11 补齐（任务#100 发现）：chanlun_signal 给 1买/1卖 生成的"
c2 = "# tbe24（24课趋势背驰）/mbei（MACD背驰）此前未落事件缓存 → 一买档"
c3 = "# 趋势背驰前瞻验证不可行。纯字段增补，不改评分与信号。"

p = Path(TARGET)
b = p.read_bytes()
old = NL.join([
    b'                "bei24": sg.get("bei24"),',
    b'                "score": sc,',
])
new = NL.join([
    b'                "bei24": sg.get("bei24"),',
    b"                " + c1.encode("utf-8"),
    b"                " + c2.encode("utf-8"),
    b"                " + c3.encode("utf-8"),
    b'                "tbe24": sg.get("tbe24"),',
    b'                "mbei": sg.get("mbei"),',
    b'                "score": sc,',
])
if b'"tbe24": sg.get("tbe24")' in b:
    print("SKIP (already applied)")
else:
    cnt = b.count(old)
    assert cnt == 1, "anchor count=" + str(cnt)
    p.write_bytes(b.replace(old, new, 1))
    back = p.read_bytes()
    assert b'"tbe24": sg.get("tbe24")' in back and old not in back
    print("tbe24-mbei-fields OK")
