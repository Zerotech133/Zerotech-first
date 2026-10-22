# -*- coding: utf-8 -*-
"""为 7 个"动作起不来"的计划任务生成修正版 XML（只读线上定义，只写工作区）。

两类病因：
  A. 路径被 UTF-8→GBK 误编码（QuantWatchdog）：任务里的路径字面量就是乱码，
     文件自然找不到 —— 实测今天失败 710 次（0x80070002）。
  B. 动作/工作目录走 junction 别名 C:\\aios（6 个任务）：任务会话里解析不到，
     返回 0x80070001/2/3 家族。

修正方式：
  A. 把乱码前缀还原为物理路径。
  B. 把 C:\\aios 换成 C:\\项目\\老田的AI助手。

原文件一律留档为 <name>_original.xml，供 -Restore 回滚。
其它节点（触发器/主体/设置）逐字不动。
"""
import re
import sys
from pathlib import Path

TASKS_DIR = Path(r"C:\Windows\System32\Tasks")
OUT_DIR = Path(r"C:\项目\老田的AI助手\tools\schtasks_xml")
PHYS = r"C:\项目\老田的AI助手"
# QuantWatchdog 任务里的乱码前缀（UTF-8 字节按 GBK 解出的结果）
MOJIBAKE = "c:\\椤圭洰\\鑰佺敯鐨凙I鍔╂墜"

TASKS = ["QuantWatchdog", "JevStack", "KgChainWatchdog", "StrongEod",
         "XmapSync", "AlertWatch", "DeadlineWatch"]

# 只有这几个任务同时需要 PT30M -> PT5M（AlertWatch 的窗口死结）
FIVE_MIN = {"AlertWatch"}


def fix_text(text: str) -> tuple[str, list[str]]:
    changes = []
    out = text
    # A: 乱码前缀（大小写不敏感）
    for m in re.finditer(re.escape(MOJIBAKE), out, re.IGNORECASE):
        changes.append("mojibake-prefix")
        break
    out = re.sub(re.escape(MOJIBAKE), PHYS, out, flags=re.IGNORECASE)
    # B: junction 别名
    if re.search(r"C:\\aios(?!\w)", out, re.IGNORECASE):
        changes.append("junction-alias")
    out = re.sub(r"C:\\aios", PHYS, out, flags=re.IGNORECASE)
    return out, changes


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("输出目录:", OUT_DIR)
    print()
    for name in TASKS:
        src = TASKS_DIR / name
        if not src.is_file():
            print("  %-18s 读不到定义，跳过" % name)
            continue
        original = src.read_text(encoding="utf-16")
        fixed, changes = fix_text(original)
        if name in FIVE_MIN:
            fixed2 = fixed.replace("<Interval>PT30M</Interval>",
                                   "<Interval>PT5M</Interval>")
            if fixed2 != fixed:
                changes.append("interval PT30M->PT5M")
            fixed = fixed2

        cmd = re.search(r"<Command>([^<]+)</Command>", fixed)
        args = re.search(r"<Arguments>([^<]+)</Arguments>", fixed)
        cwd = re.search(r"<WorkingDirectory>([^<]+)</WorkingDirectory>", fixed)

        # 写修正版 + 原始版，均为 UTF-16LE with BOM（Task Scheduler 的原生格式）
        (OUT_DIR / ("%s_fixed.xml" % name)).write_text(fixed, encoding="utf-16")
        (OUT_DIR / ("%s_original.xml" % name)).write_text(original, encoding="utf-16")

        print("  %-18s 改动: %s" % (name, ", ".join(changes) or "(无需改动)"))
        if cmd:
            print("      cmd : %s" % cmd.group(1))
        if args:
            print("      args: %s" % args.group(1)[:100])
        if cwd:
            print("      cwd : %s" % cwd.group(1))
        print()
    print("完成。注册用 tools/apply_task_path_fix.ps1（需管理员）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
