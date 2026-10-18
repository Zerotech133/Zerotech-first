#!/usr/bin/env python3
"""防泄漏只读凭据扫描器（scan_secrets）。

用途：在 git 操作频繁的日子里守门，扫描指定文件/目录或 git 工作区
未跟踪+已修改清单，报告疑似明文凭据的行。全程只读，绝不修改、删除、
移动任何被扫描文件。

用法：
    python tools/scan_secrets.py                     # 默认 --git-status 模式
    python tools/scan_secrets.py --git-status        # 同上，显式声明
    python tools/scan_secrets.py --paths a.py dir/   # 指定文件或目录
    python tools/scan_secrets.py --strict            # 命中即退出码非 0（供 pre-commit）

退出码：0=正常（含有命中但未开 --strict）；1=--strict 且有命中；2=环境/参数错误。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

MAX_FILE_BYTES = 1024 * 1024  # 超过 1MB 的文件跳过

# 各模式：类别名 -> 正则。注意：本文件源码自身必须零命中（见自扫描测试），
# 因此所有正则字面量中的元字符序列不会被自己的模式匹配。
PATTERN_GROUP = [
    ("KEYWORD_ASSIGN", r"(?i)(api[_-]?key|secret|token|password|passwd|credential)\s*[=:]\s*[\"'][^\"']{8,}"),
    # 左边界 (?<![A-Za-z-])：排除紧跟在字母或连字符之后的 sk-（如 Windows 计划
    # 任务名 "FooTask-sk-S-1-5-..."、"...Task-sk-12800{GUID}" 的跨词误报）。
    # 行首/引号/等号/空格等前置的真 key 不受影响；SK_KEY_SEP 目标形态 sk-xxx-yyy
    # 的连字符在匹配体内部，同样不受影响。
    ("SK_KEY", r"(?<![A-Za-z-])sk-[A-Za-z0-9]{16,}"),
    ("SK_KEY_SEP", r"(?<![A-Za-z-])sk-[A-Za-z0-9][A-Za-z0-9._-]{15,}"),
    ("AWS_AKID", r"AKIA[0-9A-Z]{16}"),
    ("PRIVATE_KEY", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("BEARER_TOKEN", r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
]


def build_patterns():
    return [(name, re.compile(exp)) for name, exp in PATTERN_GROUP]


def mask_fragment(frag: str) -> str:
    """脱敏：只保留前 4 后 2 字符，中间以三个星号替代。"""
    if len(frag) <= 6:
        return "***"
    return frag[:4] + "***" + frag[-2:]


def is_probably_binary(data: bytes) -> bool:
    return b"\x00" in data


def scan_file(path: str, patterns):
    """扫描单个文件，返回 (hits, skip_reason)。

    hits 为 (行号, 类别, 脱敏片段) 列表；skip_reason 非空表示跳过原因
    （binary / too_large / not_found / read_error）。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], "not_found"
    if size > MAX_FILE_BYTES:
        return [], "too_large"
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError:
        return [], "read_error"
    if is_probably_binary(data):
        return [], "binary"
    text = data.decode("utf-8", errors="replace")
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        seen_spans = set()  # 同一 span 只计一次：SK_KEY 与 SK_KEY_SEP 会重叠命中同一形态
        for name, exp in patterns:
            for m in exp.finditer(line):
                if (m.start(), m.end()) in seen_spans:
                    continue
                seen_spans.add((m.start(), m.end()))
                hits.append((lineno, name, mask_fragment(m.group(0))))
    return hits, None


def iter_files_under(root: str):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fn in sorted(filenames):
            yield os.path.join(dirpath, fn)


def git_repo_root(cwd: str):
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def git_status_files(repo_root: str):
    """从 git status --porcelain 取 untracked+modified 文件清单（相对仓根）。

    跳过已删除（D）条目；rename/copy 取新路径并吞掉紧跟的旧路径字段。
    """
    proc = subprocess.run(
        ["git", "-C", repo_root, "status", "--porcelain", "-z", "--untracked-files=all"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    entries = proc.stdout.split(b"\0")
    files = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if not entry:
            continue
        status = entry[:2].decode("ascii", errors="replace")
        path = entry[3:].decode("utf-8", errors="replace")
        if "R" in status or "C" in status:
            i += 1  # rename/copy -z 格式下一条是旧路径
        if "D" in status:
            continue  # 已删除，无可扫内容
        files.append(path)
    return files


def collect_targets(args, from_cwd: str):
    """根据参数收集待扫描路径。返回 (targets, error_message)。"""
    if args.paths:
        targets = []
        found_any = False
        for p in args.paths:
            if os.path.isfile(p):
                found_any = True
                targets.append(p)
            elif os.path.isdir(p):
                found_any = True
                targets.extend(iter_files_under(p))
            else:
                print(f"[警告] 路径不存在，跳过: {p}", file=sys.stderr)
        if not found_any:
            return [], "没有可扫描的有效路径（--paths 全部缺失）。"
        return targets, None

    # 默认 --git-status 模式：必须位于 git 仓内
    repo_root = git_repo_root(from_cwd)
    if repo_root is None:
        return [], (
            "当前目录不在任何 git 仓库内，无法执行默认的 --git-status 扫描。"
            "请进入仓库目录后重试，或用 --paths 显式指定要扫描的文件/目录。"
        )
    rel_files = git_status_files(repo_root)
    if rel_files is None:
        return [], "git status 执行失败，无法取得扫描清单。"
    targets = [os.path.join(repo_root, rel) for rel in rel_files]
    if not targets:
        return [], None
    return targets, None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="防泄漏只读凭据扫描器（绝不修改被扫描文件）"
    )
    parser.add_argument(
        "--paths", nargs="*", default=None,
        help="要扫描的文件或目录（可多个）；缺省时等价于 --git-status",
    )
    parser.add_argument(
        "--git-status", action="store_true",
        help="从 git status --porcelain 取 untracked+modified 清单（默认行为）",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="命中即以退出码 1 结束（供 pre-commit 等门禁场景）",
    )
    args = parser.parse_args(argv)

    cwd = os.getcwd()
    try:
        targets, err = collect_targets(args, cwd)
    except Exception as exc:  # 环境异常一律拒绝运行而非带病扫描
        print(f"[错误] 收集扫描目标失败: {exc}", file=sys.stderr)
        return 2
    if err:
        print(f"[错误] {err}", file=sys.stderr)
        return 2

    patterns = build_patterns()
    hits, scanned, skipped = [], 0, []
    for path in targets:
        file_hits, skip = scan_file(path, patterns)
        if skip:
            skipped.append((path, skip))
            continue
        scanned += 1
        for lineno, cat, frag in file_hits:
            hits.append((path, lineno, cat, frag))

    shown = hits if args.paths else [
        (os.path.relpath(p, git_repo_root(cwd) or cwd).replace("\\", "/"), ln, c, f)
        for (p, ln, c, f) in hits
    ]
    for path, lineno, cat, frag in shown:
        print(f"{path}:{lineno} | {cat} | {frag}")
    print(f"[汇总] 扫描 {scanned} 个文件，命中 {len(hits)} 处；跳过 {len(skipped)} 个"
          f"（二进制/超 1MB/不可读）。")
    for path, reason in skipped:
        print(f"[跳过] {reason}: {path}")

    if args.strict and hits:
        print("[strict] 存在疑似凭据命中，拒绝通过。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
