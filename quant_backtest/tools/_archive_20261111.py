# -*- coding: utf-8 -*-
"""2026-11-11 日志归档 + 盘前产物快照（只复制，不删任何原文件）。

日期由 datetime.now() 动态生成，不写日期字面量（防输出通道串味）。
归档后做 zip 可读性校验与关键产物存在性断言。
"""
import os
import shutil
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = datetime.now().strftime("%Y%m%d")
ARCH = os.path.join(HERE, "archive")
os.makedirs(ARCH, exist_ok=True)

# ① 日志归档（logs/ 全量文件 → archive/logs_<DAY>.zip）
zp = os.path.join(ARCH, "logs_%s.zip" % DAY)
logs = os.path.join(HERE, "logs")
n, raw = 0, 0
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(os.listdir(logs)):
        p = os.path.join(logs, f)
        if not os.path.isfile(p):
            continue
        z.write(p, os.path.join("logs", f))
        n += 1
        raw += os.path.getsize(p)
with zipfile.ZipFile(zp) as z:
    assert z.testzip() is None, "zip 损坏"
    assert len(z.namelist()) == n, "zip 条目数不符"
print("① 日志归档 %d 个文件，原始 %.2f MB → zip %.2f MB (%s)" % (
    n, raw / 1048576, os.path.getsize(zp) / 1048576, os.path.basename(zp)))

# ② 盘前产物快照（ops_backup 明确不备份 html/日志，这里补齐）
dst = os.path.join(ARCH, "premarket_%s" % DAY)
os.makedirs(dst, exist_ok=True)
names = ("premarket.html", "premarket_latest.json", "premarket_history.jsonl")
for name in names:
    src = os.path.join(HERE, "reports", name)
    assert os.path.exists(src), "缺关键产物 " + name
    shutil.copy2(src, os.path.join(dst, name))
    assert os.path.getsize(os.path.join(dst, name)) == os.path.getsize(src)
print("② 盘前产物快照 %d 个 → archive/premarket_%s" % (len(names), DAY))

# ③ 本次证据文件一并入档（回归/演练/打包/深链/页面验收日志已在 logs 内）
print("③ 校验通过：zip 可读、条目数一致、快照逐字节等长。")
