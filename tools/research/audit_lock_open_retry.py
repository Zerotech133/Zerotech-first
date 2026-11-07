#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""同构锁审计：找出所有"加锁有等待语义、但打开锁文件没有"的位置。

审计口径（一句话）：**锁文件的 open 必须与锁的等待语义同级**。
  · 若加锁失败会重试/deadline，则 open 的瞬时 EACCES 也必须重试；
  · 若加锁是"立即失败"（nonblock），则 open 直接抛是对的，不算缺口。

Windows 上并发创建/打开同一文件会瞬时 Errno 13（共享模式竞争 / 安全软件持有
扫描句柄），本仓已在 inbox_gateway 实测约 16%（K-ORG-20261107-INBOXLOCK）。
同一类缺口的表现是：调用方看到"业务失败"，无法与真故障区分。

做法：
  1) 正则找 msvcrt.locking / fcntl.flock / LockFileEx 站点；
  2) AST 找每个站点所属函数，把函数源码整段取出；
  3) 对函数源码做结构性判定：
       open_form      打开方式（open()/os.open()/Path.open()/模块级 helper/无）
       lock_wait      加锁等待语义（unbounded / deadline / nonblock / unknown）
       open_guarded   open 是否在同一函数内有重试包裹（naive：while+sleep+try）
  4) 输出 markdown 表 + 每处函数源码，人工逐条定性。

用法：
  python tools/research/audit_lock_open_retry.py            # 表 + 摘要
  python tools/research/audit_lock_open_retry.py --md out.md # 只写报告文件
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SITE_RX = re.compile(r"msvcrt\.locking|fcntl\.flock|LockFileEx")
# 交付件/骨架/测试里的锁是快照或验证用，不算生产面
SKIP_DIRS = ("deliverables/", "docs/", ".worktrees/", "node_modules/")

# 打开锁文件的形态
OPEN_PATTERNS = [
    ("os.open", re.compile(r"os\.open\(")),
    ("Path.open", re.compile(r"\.open\(\s*[\"'][rwab+]{1,4}[\"']")),
    ("builtin open", re.compile(r"(?<![\w.])open\(\s*[^)\n]*[\"'][rwab+]{1,4}[\"']")),
]


def iter_files():
    """os.walk + 就地剪枝。rglob 会把 .worktrees / node_modules / 数据目录全扫一遍，
    在本仓会跑到被 SIGTERM。"""
    import os

    skip = {".git", ".worktrees", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".mypy_cache", ".pytest_cache", "data", "logs"}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".idea")]
        for name in files:
            if not name.endswith(".py"):
                continue
            p = Path(root) / name
            rel = p.relative_to(REPO).as_posix()
            if any(rel.startswith(d) for d in SKIP_DIRS):
                continue
            if "/tests/" in rel or rel.endswith("_test.py"):
                continue
            yield rel, p


def enclosing_funcs(tree):
    """返回 [(func_node, start, end)]，取最内层 def。"""
    out = []

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((child, stack + [child]))
                walk(child, stack + [child])
            else:
                walk(child, stack)

    walk(tree, [])
    return out


def classify(src: str) -> dict:
    forms = [name for name, rx in OPEN_PATTERNS if rx.search(src)]
    # 加锁等待语义
    if re.search(r"LK_NBLCK|LOCK_NB|LK_LOCK", src) and not re.search(r"while\s+True|while\s+1", src):
        wait = "nonblock"
    elif re.search(r"deadline", src):
        wait = "deadline"
    elif re.search(r"while\s+True|while\s+1|for _\s*in\s*range", src):
        wait = "unbounded"
    else:
        wait = "none"
    open_retry = bool(
        re.search(r"while\s+True|for _\s*in\s*range", src)
        and re.search(r"except\s+PermissionError|except\s+OSError|except\s+Exception", src)
        and re.search(r"time\.sleep", src)
    )
    degrades = bool(re.search(r"回退|fallback|except\s+ImportError", src))
    return {"open_form": forms, "lock_wait": wait,
            "open_retry": open_retry, "degrades": degrades}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default="")
    args = ap.parse_args()

    rows, body = [], []
    for rel, path in iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not SITE_RX.search(text):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            rows.append((rel, "?", 0, "PARSE-ERROR %s" % exc, "", "", ""))
            continue
        funcs = enclosing_funcs(tree)
        seen = set()
        for m in SITE_RX.finditer(text):
            lineno = text[:m.start()].count("\n") + 1
            host = None
            for fn, stack in funcs:
                if fn.lineno <= lineno <= (fn.end_lineno or lineno):
                    if host is None or fn.lineno > host.lineno:
                        host = fn
            if host is None:
                rows.append((rel, "<module>", lineno, "module-level", "", "", ""))
                continue
            key = (rel, host.name, host.lineno)
            if key in seen:
                continue
            seen.add(key)
            src = ast.get_source_segment(text, host) or ""
            c = classify(src)
            rows.append((rel, host.name, lineno,
                         "/".join(c["open_form"]) or "无显式 open",
                         c["lock_wait"], "Y" if c["open_retry"] else "N",
                         "Y" if c["degrades"] else "-"))
            body.append("### %s :: %s (L%d)\n\n```python\n%s\n```\n" % (rel, host.name, host.lineno, src))

    lines = ["# 同构锁审计：加锁有等待、open 无重试", "",
             "| 文件 | 函数 | 行 | 打开方式 | 加锁等待 | open 重试 | 降级 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| `%s` | `%s` | %d | %s | %s | %s | %s |" % r)
    lines.append("")
    lines.append("合计 %d 处（含同一函数内多站点去重）。" % len(rows))
    report = "\n".join(lines) + "\n\n" + "\n".join(body)

    if args.md:
        Path(args.md).write_text(report, encoding="utf-8")
        print("已写 %s（%d 处）" % (args.md, len(rows)))
        for r in rows:
            print("%-52s %-26s L%-5d open=%-14s wait=%-9s retry=%s" %
                  (r[0][-52:], r[1][:26], r[2], r[3], r[4], r[5]))
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
