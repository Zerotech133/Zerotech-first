# -*- coding: utf-8 -*-
"""2026-11-11 代码审计修复：清理盘前/影子链的死代码与未用导入。

约束（老田铁律）：
  * 补丁前断言锚点 count == 1，替换后读回断言新串存在、旧串消失；
  * 按字节替换，分别保留 pipeline_health.py(LF) / shadow_ab.py(CRLF) 原有换行，
    避免全文件换行翻转造成大 diff；
  * 涉及数字/编号的断言一律以 ord 码点复核，不信打印层。
"""
from pathlib import Path

LF = chr(10)
CRLF = chr(13) + chr(10)


def rep(fname, nl, old_lines, new_lines, label):
    p = Path(fname)
    b = p.read_bytes()
    old = nl.join(old_lines).encode("utf-8")
    new = nl.join(new_lines).encode("utf-8")
    cnt = b.count(old)
    assert cnt == 1, label + " anchor count=" + str(cnt)
    out = b.replace(old, new, 1)
    p.write_bytes(out)
    back = p.read_bytes()
    assert new in back and old not in back, label + " verify failed"
    digits = "".join(ch for ch in nl.join(new_lines) if ch.isdigit())
    print(label, "OK", "digit_codepoints=" + str([ord(c) for c in digits]))


# ① pipeline_health：未用 import re
rep(
    "pipeline_health.py", LF,
    ["import glob", "import json", "import os", "import re", "import sys"],
    ["import glob", "import json", "import os", "import sys"],
    "drop-unused-re",
)

# ② pipeline_health：today_lines 恒真全量构造且从未使用（or True 死代码）
rep(
    "pipeline_health.py", LF,
    [
        "        today_lines = [l for l in txt.splitlines()",
        '                       if l.startswith("[%s" % now.strftime("%H")) or True]',
        "        # 简化：看今天日期相关的 fail/abort（日志只有时分秒，按当次进程）",
    ],
    [
        "        # 只看最后 200 行（日志只有时分秒，按当次进程判定）；",
        "        # 原 today_lines 恒真全量构造后从未使用，已删除（11-11 审计）。",
    ],
    "drop-dead-today-lines",
)

# ③ shadow_ab：未用 import math
rep(
    "shadow_ab.py", CRLF,
    ["import argparse", "import json", "import math", "import os", "import statistics"],
    ["import argparse", "import json", "import os", "import statistics"],
    "drop-unused-math",
)

# ④ shadow_ab：dev_slip3 声明后从未使用（固定滑点已由 slip3_bp_avg 常量替代）
rep(
    "shadow_ab.py", CRLF,
    ["        dev_close, dev_slip3, gap_ok, fell_back, no_gap_fell = [], 0, 0, 0, 0"],
    ["        dev_close, gap_ok, fell_back, no_gap_fell = [], 0, 0, 0"],
    "drop-unused-dev-slip3",
)

# ⑤ shadow_ab：bt10m_sweep 导入无使用、无顶层副作用（已核实），去掉可缩短启动链
rep(
    "shadow_ab.py", CRLF,
    ["import bt10m_sweep as SW  # noqa: E402", "import bt10m_verify as V  # noqa: E402"],
    ["import bt10m_verify as V  # noqa: E402"],
    "drop-unused-bt10m-sweep",
)
