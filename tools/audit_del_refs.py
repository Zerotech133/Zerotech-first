# -*- coding: utf-8 -*-
"""删除前依赖扫描：53 个待提交删除是否仍被 代码/脚本/文档/计划任务 引用。

输出三类证据：
  A. 仓库内文本引用（.py/.ps1/.bat/.cmd/.md/.json/.yaml/.toml，排除 .git/.worktrees/大 jsonl）
  B. 计划任务 Actions 参数里出现的文件名
  C. 同目录 ps1 包装器引用

只读，不删不改。
"""
import io
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIR = {".git", ".worktrees", "node_modules", "pylibs", "__pycache__", ".venv"}
TEXT_EXT = {".py", ".ps1", ".bat", ".cmd", ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt"}
MAX_BYTES = 2 * 1024 * 1024


def deleted():
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return [l[3:] for l in out.splitlines() if l.startswith(" D")]


def main():
    dels = deleted()
    names = {os.path.basename(p): p for p in dels}
    stems = {os.path.splitext(n)[0]: n for n in names}
    print("待提交删除 %d 个" % len(dels))
    print()

    # A. 仓库内文本引用
    hits = {n: [] for n in names}
    scanned = 0
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIR]
        for f in fns:
            ext = os.path.splitext(f)[1].lower()
            if ext not in TEXT_EXT:
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) > MAX_BYTES:
                    continue
                t = io.open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            scanned += 1
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel in names.values():
                continue
            for n in names:
                if n in t:
                    hits[n].append(rel)

    print("A. 扫描文本文件 %d 个" % scanned)
    dangling = {n: v for n, v in hits.items() if v}
    if not dangling:
        print("   无任何仓库内引用")
    for n, v in sorted(dangling.items()):
        print("   %-34s <- %s" % (n, ", ".join(v[:4]) + (" ..." if len(v) > 4 else "")))

    # B. 计划任务
    print()
    print("B. 计划任务引用")
    ps = r"""
$out = @()
foreach ($t in Get-ScheduledTask) {
  foreach ($a in $t.Actions) {
    $s = "" + $a.Execute + " " + $a.Arguments
    if ($s.Trim()) { $out += [pscustomobject]@{ Task = ($t.TaskPath + $t.TaskName); Cmd = $s } }
  }
}
$out | ConvertTo-Json -Compress
"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120,
        )
        tasks = json.loads(r.stdout or "[]")
        if isinstance(tasks, dict):
            tasks = [tasks]
        found = False
        for t in tasks:
            for n in names:
                if n in t["Cmd"]:
                    print("   %-34s <- 任务 %s" % (n, t["Task"]))
                    found = True
        if not found:
            print("   无")
    except Exception as e:
        print("   计划任务查询失败: %s" % e)

    # C. stem 级引用（防止 .py 被写成不带后缀的逻辑判断）
    print()
    print("C. 同目录 ps1 里出现 stem（无后缀）")
    any_c = False
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIR]
        for f in fns:
            if not f.lower().endswith(".ps1"):
                continue
            p = os.path.join(dp, f)
            try:
                t = io.open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for st, n in stems.items():
                if st in t and n not in names:
                    pass
                if st in t:
                    print("   %-34s <- %s" % (st, os.path.relpath(p, ROOT)))
                    any_c = True
    if not any_c:
        print("   无")


if __name__ == "__main__":
    main()
