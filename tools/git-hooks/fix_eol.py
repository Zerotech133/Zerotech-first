#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 .gitattributes 的 eol 声明修正工作区行尾。

为什么需要它
------------
`.gitattributes` 的 `text eol=xxx` **只在检出时生效**。对已经存在于工作区的文件，
属性变更不会让 git 重写它 —— 工作区会长期停在错的行尾上：

  - `git status` 恒脏（改没改都显示 M），`git diff` 带满屏行尾噪音；
  - 更隐蔽：本机测试红而云端绿。云端 checkout 会应用属性，本机不会。

判例（2026-11-08）：2642d3583 给 portal/nginx.conf 加 `text eol=crlf` 后，本机
round7 的 test_nginx_current_state_is_valid / test_absolute_redirect_is_server_level
恒红，而 CI 全绿；同文件还带着并行会话 49 行未提交改动，`git checkout --` 会丢改动。

用法
----
  python tools/git-hooks/fix_eol.py                 # dry-run：只列出不符的文件
  python tools/git-hooks/fix_eol.py --apply         # 全部修正
  python tools/git-hooks/fix_eol.py --apply a.conf  # 只修指定文件

安全性
------
只改行尾，不动内容：转换前后「去掉 \\r 再逐字节比对」必须相等，不相等即中止该文件。
含 NUL 的文件视为二进制直接跳过。
"""
import argparse
import os
import subprocess
import sys


def repo_root():
    r = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit('不在 git 仓库内')
    return r.stdout.strip()


def list_eol(root, paths):
    """返回 [(relpath, worktree_eol, want_eol)]，只含有 eol 声明的条目。"""
    cmd = ['git', 'ls-files', '--eol', '--'] + (list(paths) if paths else [])
    r = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if r.returncode != 0:
        raise SystemExit('git ls-files --eol 失败：%s' % r.stderr.strip())
    out = []
    for line in r.stdout.splitlines():
        if '\t' not in line:
            continue
        left, path = line.split('\t', 1)
        parts = left.split()
        if len(parts) < 3:
            continue
        w = parts[1][2:] if parts[1].startswith('w/') else ''
        attrs = ' '.join(parts[2:])
        want = None
        if 'eol=crlf' in attrs:
            want = 'crlf'
        elif 'eol=lf' in attrs:
            want = 'lf'
        if want:
            out.append((path, w, want))
    return out


def convert(data, want):
    """归一到目标行尾。先统一成 LF 再转，天然处理混合行尾。"""
    lf = data.replace(b'\r\n', b'\n')
    if want == 'crlf':
        return lf.replace(b'\n', b'\r\n')
    return lf


def main():
    ap = argparse.ArgumentParser(description='按 .gitattributes 修正工作区行尾')
    ap.add_argument('--apply', action='store_true', help='实际写入（默认只报告）')
    ap.add_argument('paths', nargs='*', help='只处理指定文件（默认全仓库）')
    a = ap.parse_args()

    root = repo_root()
    os.chdir(root)
    rows = list_eol(root, a.paths)

    bad, fixed, skipped = [], 0, 0
    for path, w, want in rows:
        if w in ('', 'none'):
            continue
        if w == want:
            continue
        bad.append((path, w, want))

    if not bad:
        print('行尾一致：%d 个受 .gitattributes 约束的文件全部合规' % len(rows))
        return 0

    print('行尾不符：%d 处（约束文件共 %d 个）' % (len(bad), len(rows)))
    for path, w, want in bad:
        tag = '[FIX]' if a.apply else '[DRY]'
        if not a.apply:
            print('%s %s  w/%s -> %s' % (tag, path, w, want))
            continue
        full = path
        if not os.path.isfile(full):
            print('%s %s  跳过（工作区无此文件）' % (tag, path))
            skipped += 1
            continue
        data = open(full, 'rb').read()
        if b'\x00' in data:
            print('%s %s  跳过（二进制）' % (tag, path))
            skipped += 1
            continue
        new = convert(data, want)
        # 内容零变化校验：去掉 CR 后必须逐字节相等
        if data.replace(b'\r\n', b'\n') != new.replace(b'\r\n', b'\n'):
            print('%s %s  跳过（内容校验不通过，拒绝写入）' % (tag, path))
            skipped += 1
            continue
        if new == data:
            continue
        open(full, 'wb').write(new)
        fixed += 1
        print('%s %s  w/%s -> %s（%d 字节）' % (tag, path, w, want, len(new)))

    if a.apply:
        print('\n已修正 %d 个文件%s' % (fixed, ('，跳过 %d 个' % skipped) if skipped else ''))
        left = [r for r in list_eol(root, a.paths)
                if r[1] not in ('', 'none') and r[1] != r[2]]
        print('复核：仍有 %d 处不符' % len(left))
        for path, w, want in left:
            print('  ! %s  w/%s 期望 %s' % (path, w, want))
        return 1 if left else 0
    print('\n（dry-run。加 --apply 实际写入）')
    return 2


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    sys.exit(main())
