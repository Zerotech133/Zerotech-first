# -*- coding: utf-8 -*-
"""扫全部已知计划任务的动作路径，找同一类"起不来"隐患（只读）。

判据（基于 2026-10-22 实测的两类病因）：
  JUNCTION : Command/Arguments/WorkingDirectory 里出现 C:\\aios 别名
  NONASCII : 动作相关字段里出现 C:\\项目… 以外的非 ASCII（多半是 UTF-8→GBK 乱码）
  OK       : 动作全部走物理路径且合法

任务名来源：
  1) portal/data/task_exit_audit/task_exit_ledger.jsonl（账本里的任务）
  2) 已知的几个额外名字（兜底）
读不到定义的任务单独列出——那本身也是信号（S4U/ACL 可能有问题）。
"""
import json
import re
import sys
from pathlib import Path

TASKS_DIR = Path(r"C:\Windows\System32\Tasks")
LEDGER = Path(r"C:\项目\老田的AI助手\portal\data\task_exit_audit\task_exit_ledger.jsonl")
PHYS_ROOT = "C:\\项目\\老田的AI助手"
EXTRA = ["AlertWatch", "QuantWatchdog", "KgChainWatchdog", "StrongEod",
         "XmapSync", "DeadlineWatch", "JevStack", "FeishuBotWatchdog"]

TAGS = ("Command", "Arguments", "WorkingDirectory")


def load_task_names() -> list[str]:
    names = set(EXTRA)
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = str(rec.get("name") or "").strip()
            if name:
                names.add(name.lstrip("\\"))
    return sorted(names)


def read_task(name: str) -> str | None:
    for cand in (TASKS_DIR / name, TASKS_DIR / "ZeroBound" / name):
        try:
            if cand.is_file():
                return cand.read_text(encoding="utf-16")
        except (OSError, UnicodeError):
            continue
    return None


def analyse(xml: str) -> dict:
    fields = {}
    for tag in TAGS:
        m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), xml)
        fields[tag] = m.group(1) if m else ""
    blob = " | ".join(v for v in fields.values() if v)
    has_alias = bool(re.search(r"C:\\aios(?![A-Za-z])", blob, re.IGNORECASE))
    # 物理根自身含中文，先把合法前缀剔掉再看有没有别的非 ASCII
    stripped = blob.replace(PHYS_ROOT, "")
    nonascii = sorted({c for c in stripped if ord(c) > 127})
    return {
        "fields": fields,
        "alias": has_alias,
        "nonascii": "".join(nonascii),
        "mojibake": bool(re.search(r"[\u4e00-\u9fff]{2,}", stripped)) and PHYS_ROOT not in blob,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    names = load_task_names()
    print("候选任务: %d 个（账本 + 已知兜底）" % len(names))
    print()

    unreadable, alias_hits, weird, ok = [], [], [], []
    for name in names:
        xml = read_task(name)
        if xml is None:
            unreadable.append(name)
            continue
        info = analyse(xml)
        if info["mojibake"]:
            weird.append((name, info))
        elif info["alias"]:
            alias_hits.append((name, info))
        elif info["nonascii"]:
            weird.append((name, info))
        else:
            ok.append(name)

    print("结果:")
    print("  正常（物理路径）      : %d" % len(ok))
    print("  用 junction 别名      : %d" % len(alias_hits))
    print("  路径疑似乱码/异常     : %d" % len(weird))
    print("  读不到定义            : %d" % len(unreadable))
    print()

    if alias_hits:
        print("=== 仍用 C:\\aios 别名的任务 ===")
        for name, info in alias_hits:
            print("  %-26s %s" % (name, (info["fields"].get("Arguments") or "")[:88]))
        print()
    if weird:
        print("=== 路径疑似乱码/异常（重点）===")
        for name, info in weird:
            print("  %-26s nonascii=%r" % (name, info["nonascii"][:20]))
            print("      cmd : %s" % (info["fields"].get("Command") or "")[:100])
            print("      args: %s" % (info["fields"].get("Arguments") or "")[:100])
        print()
    if unreadable:
        print("=== 读不到定义（本身是信号）===")
        for name in unreadable:
            print("  %s" % name)
        print()

    print("=== 已覆盖的 7 个修复对象是否都还在清单里 ===")
    fixed = {"QuantWatchdog", "JevStack", "KgChainWatchdog", "StrongEod",
             "XmapSync", "AlertWatch", "DeadlineWatch"}
    print("  ", ", ".join(sorted(fixed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
