# -*- coding: utf-8 -*-
"""真相源 v2 · C4/H2+H3 历史计划归档执行（2026-11-10）。

依据 docs/plans/2026-11-10_统一真相源_能力账本+执行计划_v2.md：
- 当前目录只留本文（真相源 v2），其余全部迁入 _archive_20261110/
- 迁移是移动不是删除（同盘 move，git 历史可溯）
- 生成归档清单 _archive_20261110/INDEX_归档清单.md
- 整合版（v1 附录）与旧 INDEX 一并归档，清单中标注角色
"""
import os
import re
import shutil
import time

PLANS = r"C:/项目/老田的AI助手/docs/plans"
KEEP = "2026-11-10_统一真相源_能力账本+执行计划_v2.md"
ARCH = os.path.join(PLANS, "_archive_20261110")
SPECIAL_ROLE = {
    "2026-11-10_零界全仓审计与新研发计划_整合版.md": "详细附录（被真相源 v2 取代，细节仍引用）",
    "INDEX_计划全集索引.md": "旧索引（被本归档清单取代）",
}

os.makedirs(ARCH, exist_ok=True)
moved, entries = [], []
for name in sorted(os.listdir(PLANS)):
    if name == KEEP or name == "_archive_20261110":
        continue
    src = os.path.join(PLANS, name)
    dst = os.path.join(ARCH, name)
    assert not os.path.exists(dst), "dst exists: %s" % dst
    mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(src)))
    # 抽标题：md 首个 # 标题；否则用文件名
    title, first_line = "", ""
    if os.path.isfile(src) and name.endswith(".md"):
        try:
            with open(src, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    s = line.strip()
                    if not s:
                        continue
                    if not title and s.startswith("#"):
                        title = s.lstrip("#").strip()
                    if not first_line and not s.startswith("#"):
                        first_line = s[:120]
                    if title and (first_line or i > 40):
                        break
        except OSError:
            pass
    shutil.move(src, dst)
    moved.append(name)
    entries.append((name, title, first_line, mtime))

# 归档清单
lines = [
    "# 历史计划归档清单（2026-11-10）",
    "",
    "> 依据《2026-11-10_统一真相源_能力账本+执行计划_v2.md》C4/H3 生成，脚本扫描，状态字段待人工校对。",
    "> 共 **%d** 项（文件+附件目录）。git 复现点：tag `archive/plans-20261110`。" % len(entries),
    "> 状态：archived = 已归档只读；除非真相源 v2 显式引用，不再作为执行依据。",
    "",
    "| # | 文件/目录 | 标题 | 首行摘要 | 修改日期 | 角色 |",
    "|---|---|---|---|---|---|",
]
for i, (name, title, first_line, mdate) in enumerate(entries, 1):
    role = SPECIAL_ROLE.get(name, "archived")
    lines.append(
        "| %d | %s | %s | %s | %s | %s |"
        % (i, name, (title or "-").replace("|", "\\|"), (first_line or "-").replace("|", "\\|"), mdate, role)
    )
with open(os.path.join(ARCH, "INDEX_归档清单.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("moved:", len(moved))
print("kept in plans/:", [x for x in os.listdir(PLANS) if x != "_archive_20261110"])
print("index entries:", len(entries))
