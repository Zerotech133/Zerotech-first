# -*- coding: utf-8 -*-
"""逐个验证计划任务的动作目标文件是否真的存在（只读）。

今天（2026-10-22）的教训：`JevStack` 每 10 分钟跑一次、每次都返回 0xFFFD0000，
因为它的 `-File` 指向 `jev-eval\\start_jev_stack.ps1`——**这个脚本在 git 历史里
从未存在过**。任务计划程序不会告诉你"目标文件不存在"，它只会记录一个返回码，
于是这类任务可以空转几个月而没人发现。

本脚本把"目标是否存在"从【碰巧发现】变成【可重复检查】：
  1. 从任务定义里取 Command / Arguments / WorkingDirectory；
  2. 从 Arguments 里认出被执行的脚本/程序（.py/.ps1/.bat/.cmd/.exe）；
  3. 把 `C:\\aios` 别名映射到物理根后逐个 Test-Path；
  4. 输出"目标缺失"的任务清单。

只读：不启动、不修改、不注册任何任务。
"""
import json
import re
import sys
from pathlib import Path

TASKS_DIR = Path(r"C:\Windows\System32\Tasks")
LEDGER = Path(r"C:\项目\老田的AI助手\portal\data\task_exit_audit\task_exit_ledger.jsonl")
ALIAS = "C:\\aios"
PHYS = r"C:\项目\老田的AI助手"
EXTRA = ["AlertWatch", "QuantWatchdog", "KgChainWatchdog", "StrongEod", "XmapSync",
         "DeadlineWatch", "JevStack", "FeishuBotWatchdog", "SpaceAgentTask",
         "SpaceManagerTask"]
TARGET_EXT = (".py", ".ps1", ".bat", ".cmd", ".exe")
# 形如 C:\...\xxx.py / "C:\...\xxx.py" / xxx.py（相对当前目录的情况另计）
PATH_RE = re.compile(r'"?([A-Za-z]:\\[^"<>\r\n|]*?\.(?:py|ps1|bat|cmd|exe))"?', re.I)


def task_names() -> list[str]:
    names = set(EXTRA)
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n = str(rec.get("name") or "").strip().lstrip("\\")
            if n:
                names.add(n)
    return sorted(names)


def read_xml(name: str) -> str | None:
    for cand in (TASKS_DIR / name, TASKS_DIR / "ZeroBound" / name):
        try:
            if cand.is_file():
                return cand.read_text(encoding="utf-16")
        except (OSError, UnicodeError):
            continue
    return None


def field(xml: str, tag: str) -> str:
    m = re.search(r"<%s>([^<]*)</%s>" % (tag, tag), xml)
    return m.group(1) if m else ""


def to_phys(path: str) -> str:
    return re.sub(re.escape(ALIAS), PHYS, path, flags=re.I)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="逐个验证计划任务的动作目标文件是否存在（只读）")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出（便于其他脚本消费）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    missing, unreadable, no_target, ok = [], [], [], []
    for name in task_names():
        xml = read_xml(name)
        if xml is None:
            unreadable.append(name)
            continue
        cmd = field(xml, "Command")
        args = field(xml, "Arguments")
        cwd = field(xml, "WorkingDirectory")
        targets = [m.group(1) for m in PATH_RE.finditer(args)]
        # 只把**带盘符的完整路径**当目标：裸名（powershell.exe / cmd.exe）由 PATH 解析，
        # 不是文件缺失。2026-10-22 第一版把 cmd 也塞进来，误报 8 个任务"缺 powershell.exe"。
        if "\\" in cmd and cmd.lower().endswith(TARGET_EXT) and not cmd.lower().endswith("python.exe"):
            targets.insert(0, cmd)
        if not targets:
            no_target.append((name, args[:60]))
            continue
        absent = [t for t in targets if not Path(to_phys(t)).exists()]
        if absent:
            missing.append((name, absent, cwd))
        else:
            ok.append(name)

    if a.json:
        print(json.dumps({
            "total": len(ok) + len(missing) + len(no_target) + len(unreadable),
            "ok": sorted(ok),
            "missing": [{"task": n, "absent": abs_, "cwd": cwd}
                        for n, abs_, cwd in missing],
            "no_target": [{"task": n, "args": s} for n, s in no_target],
            "unreadable": sorted(unreadable),
        }, ensure_ascii=False, indent=2))
        return 1 if missing else 0

    print("任务总数: %d" % (len(ok) + len(missing) + len(no_target) + len(unreadable)))
    print("  目标存在        : %d" % len(ok))
    print("  **目标缺失**    : %d" % len(missing))
    print("  认不出文件目标  : %d（可能是 -m 模块 / 内嵌命令）" % len(no_target))
    print("  定义读不到      : %d" % len(unreadable))
    print()

    if missing:
        print("=== 目标缺失（这些任务每次运行都必然失败）===")
        for name, absent, cwd in missing:
            print("  %s" % name)
            for t in absent:
                print("      缺: %s" % t)
            if cwd:
                print("      cwd: %s%s" % (cwd, "" if Path(to_phys(cwd)).exists() else "  <- 目录也不存在"))
        print()

    if unreadable:
        print("=== 定义读不到（本身是信号：权限/归属异常）===")
        for name in unreadable:
            print("  %s" % name)
        print()

    if no_target:
        print("=== 认不出文件目标（人工瞄一眼）===")
        for name, args in no_target:
            print("  %-26s args=%s" % (name, args))
        print()

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
