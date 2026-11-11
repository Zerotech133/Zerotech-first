# -*- coding: utf-8 -*-
"""2026-11-11 osleg 契约文档测试修复：跟上 11-10 大归档的路径迁移。

事实链（git 提交佐证）：
  * 契约文档 v1.0 于 10-25 由 8df8da00d 首次入库 docs/plans/（受控路径）；
  * 10-26 7ab34edcf（W4SIGN 签认落地）等提交持续更新该文件；
  * 11-10 10:01 的 8aa5a827a 把 372 份历史计划整体迁入
    docs/plans/_archive_20261110/（含本契约，冻结只读）；
  * 测试（10-26 7dae0e249 引入）仍断言旧路径 docs/plans/<原名>，
    自 11-10 起失败。

文档本体健在：HEAD 归档版含「v1.0」「签认」标记（签认时间 2026-10-26
12:20，老田代 A0）。这是路径迁移后的测试滞后，不是文档丢失；改测试
指向归档路径即恢复契约本意（防「只写实现不落契约」）。
"""
from pathlib import Path

TARGET = "tests/test_osleg_contract_skeleton.py"
NAME = "2026-10-25_OSLINK-W4_" + "司马懿到田丰归口契约v0.1_草案"
NL = bytes([13, 10])  # 目标文件为 CRLF（已核实）

c1 = "# 2026-11-10 大归档（8aa5a827a）把 372 份历史计划迁入 _archive_20261110/（含本契约，冻结只读）；"
c2 = "# 测试同步改指归档路径，契约本体与签认标记未动（11-11 修复）。"

p = Path(TARGET)
b = p.read_bytes()
old = (
    b'    doc = root / "docs" / "plans" / "'
    + NAME.encode("utf-8")
    + b'.md"'
)
new = (
    b"    " + c1.encode("utf-8") + NL
    + b"    " + c2.encode("utf-8") + NL
    + b'    doc = (root / "docs" / "plans" / "_archive_20261110" / "'
    + NAME.encode("utf-8")
    + b'.md")'
)

if b'"_archive_20261110"' in b:
    print("SKIP (already applied)")
else:
    cnt = b.count(old)
    assert cnt == 1, "anchor count=" + str(cnt)
    p.write_bytes(b.replace(old, new, 1))
    back = p.read_bytes()
    assert b'"_archive_20261110"' in back and old not in back
    print("osleg-doc-path OK")
