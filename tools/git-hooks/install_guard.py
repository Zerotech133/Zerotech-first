#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「共享主 worktree 直提守卫」装进 .git/hooks/pre-commit。

设计约束：
  1. 绝不能覆盖已有的 pre-commit。本仓库已有一个凭据扫描 hook（warn-only），
     所以这里只做「插入标记块」，原有内容一字不动。
  2. 幂等。重复执行只更新块内容，不会叠加多份。
  3. 守卫逻辑内联进 hook，不依赖当前工作区里是否存在 tools/git-hooks/*.sh
     ——否则切到老分支时守卫会静默失效。

用法：
  python tools/git-hooks/install_guard.py             # 安装 / 更新
  python tools/git-hooks/install_guard.py --status    # 看状态
  python tools/git-hooks/install_guard.py --uninstall # 卸载（只删标记块）
  python tools/git-hooks/install_guard.py --enforce   # 切到拦截模式
  python tools/git-hooks/install_guard.py --audit     # 切回只看不拦
"""
import argparse
import io
import os
import subprocess
import sys
import time

BEGIN = '# >>> [WB-GUARD] 共享主 worktree 直提守卫'
END = '# <<< [WB-GUARD]'
SRC_REL = 'tools/git-hooks/pre-commit-worktree-guard.sh'


def repo_paths():
    top = subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                         capture_output=True, text=True).stdout.strip()
    common = subprocess.run(['git', 'rev-parse', '--git-common-dir'],
                            capture_output=True, text=True).stdout.strip()
    if not os.path.isabs(common):
        common = os.path.join(top, common)
    return os.path.abspath(top), os.path.abspath(common)


def read(p):
    with io.open(p, 'r', encoding='utf-8', newline='') as f:
        return f.read()


def write(p, s):
    with io.open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(s)


def strip_block(text):
    """剔除已有守卫块（含两侧标记行）。"""
    if BEGIN not in text:
        return text, False
    out, skip = [], False
    for ln in text.split('\n'):
        if ln.startswith(BEGIN):
            skip = True
            continue
        if ln.startswith(END):
            skip = False
            continue
        if not skip:
            out.append(ln)
    return '\n'.join(out), True


def build_block(src_body, mode):
    """把守卫正文包进子 shell，exit 只影响子 shell，再由外层转成 hook 的退出码。"""
    # 兜底归一化：本机 core.autocrlf=true，工作区里的 .sh 可能带 CRLF；
    # 一旦 CRLF 被写进 hook，sh 解析会报 "\r: command not found"。
    src_body = src_body.replace('\r\n', '\n').replace('\r', '\n')
    body = src_body.replace('#!/bin/sh\n', '', 1)
    return '\n'.join([
        BEGIN + '（模式 %s · 安装/卸载：python tools/git-hooks/install_guard.py）' % mode,
        '# 由 install_guard.py 自动生成，手改会在下次安装时被覆盖。',
        '# 判定：主 worktree 的 git-dir == git-common-dir；链接 worktree 两者不同。',
        '(',
        body.rstrip('\n'),
        ')',
        '_wb_rc=$?',
        '[ $_wb_rc -eq 0 ] || exit $_wb_rc',
        END,
        '',
    ])


def insert_block(hook_text, block):
    """插到 shebang 之后、其余内容之前（守卫要先于凭据扫描，省一次全量扫描）。"""
    lines = hook_text.split('\n')
    if lines and lines[0].startswith('#!'):
        return lines[0] + '\n' + block + '\n'.join(lines[1:])
    return block + hook_text


def status(top, common):
    hook = os.path.join(common, 'hooks', 'pre-commit')
    if not os.path.exists(hook):
        print('pre-commit        : 不存在')
    else:
        t = read(hook)
        has = BEGIN in t
        print('pre-commit        : 存在（%d 字节）' % len(t.encode('utf-8')))
        print('守卫              : %s' % ('已安装' if has else '未安装'))
        if has:
            first = [ln for ln in t.split('\n') if ln.startswith(BEGIN)]
            print('  块信息          : %s' % (first[0][:100] if first else ''))
        # 原有逻辑是否还在
        print('原有内容          : %s' % ('保留' if '凭据' in t or 'scan_secrets' in t else '未检测到凭据扫描'))
    for flag, desc in (('WB_GUARD_ENFORCE', 'enforce（拒绝）'), ('WB_ALLOW_MAIN_COMMIT', '全局豁免')):
        p = os.path.join(common, flag)
        print('%-17s : %s' % (flag, ('存在 → ' + desc) if os.path.exists(p) else '不存在'))
    log = os.path.join(common, 'wb-main-wt-commits.log')
    if os.path.exists(log):
        n = sum(1 for _ in io.open(log, 'r', encoding='utf-8', errors='replace'))
        print('留痕日志          : %d 条' % n)
        lines = io.open(log, 'r', encoding='utf-8', errors='replace').read().strip().split('\n')
        for ln in lines[-3:]:
            print('  %s' % ln[:110])
    else:
        print('留痕日志          : 无')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--status', action='store_true')
    ap.add_argument('--uninstall', action='store_true')
    ap.add_argument('--enforce', action='store_true', help='建 WB_GUARD_ENFORCE 标记，切到拦截')
    ap.add_argument('--audit', action='store_true', help='删 WB_GUARD_ENFORCE 标记，切回只看不拦')
    a = ap.parse_args()

    top, common = repo_paths()
    hooks = os.path.join(common, 'hooks')
    hook = os.path.join(hooks, 'pre-commit')
    src = os.path.join(top, SRC_REL)

    if a.enforce or a.audit:
        flag = os.path.join(common, 'WB_GUARD_ENFORCE')
        if a.enforce:
            open(flag, 'w').close()
            print('已切到 enforce：主工作区提交将被拒绝')
            print('（豁免：WB_ALLOW_MAIN_WT=1 git commit ...）')
        else:
            if os.path.exists(flag):
                os.remove(flag)
            print('已切回 audit：只留痕、不阻断')
        status(top, common)
        return

    if a.status:
        status(top, common)
        return

    if a.uninstall:
        if not os.path.exists(hook):
            print('pre-commit 不存在，无需卸载')
            return
        t = read(hook)
        new, had = strip_block(t)
        if not had:
            print('未发现守卫块，未做改动')
            return
        write(hook, new)
        os.chmod(hook, 0o755)
        print('已卸载守卫块（原有内容保留，%d → %d 字节）' % (
            len(t.encode('utf-8')), len(new.encode('utf-8'))))
        status(top, common)
        return

    # 安装 / 更新
    if not os.path.exists(src):
        raise SystemExit('缺少守卫源文件：%s' % src)
    body = read(src)
    flag_mode = 'enforce' if os.path.exists(os.path.join(common, 'WB_GUARD_ENFORCE')) else 'audit'
    block = build_block(body, flag_mode)

    if not os.path.exists(hooks):
        os.makedirs(hooks)
    if os.path.exists(hook):
        old = read(hook)
        new, had = strip_block(old)
        if not had and len(old.encode('utf-8')) > 0:
            bak = hook + '.bak-' + time.strftime('%Y%m%d-%H%M%S')
            write(bak, old)
            print('已备份原 pre-commit → %s' % os.path.basename(bak))
        new = insert_block(new, block)
        action = '更新' if had else '插入'
    else:
        new = '#!/bin/sh\n' + block
        action = '新建'

    write(hook, new)
    os.chmod(hook, 0o755)
    print('%s守卫块（%d 字节，模式 %s）' % (action, len(new.encode('utf-8')), flag_mode))
    status(top, common)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
