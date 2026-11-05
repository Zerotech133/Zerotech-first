#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""悬空分支「净贡献」判定 v2 —— 逐文件三方比对。

对每个未合并、无 worktree 的分支 b：
  base = merge-base(master, b)
  files = diff --name-only base b      （分支自己改了什么）
  逐个文件比对三个版本：base / b / master
    ABSORBED   master 版本 == b 版本          → 改动已在 master（被后续提交吸收）
    UNMERGED   master 版本 == base 版本       → master 根本没动过这文件，分支改动未落
    BOTH       master 和 b 都是新东西         → 双方都改过，可能冲突/可叠加
    MISSING    b 有这文件，master 没有        → 整个文件在 master 不存在（真丢件）
    DELETED    b 删了这文件，master 还在      → 删除未生效

净贡献 = UNMERGED + BOTH + MISSING + DELETED 的文件数。
净贡献为 0 且无冲突 → 真的可删；否则就是「干完没合并」的活。
"""
import json
import os
import subprocess
import sys
import time

import os as _os
_REPO = (_os.environ.get('WB_REPO')
         or subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                           capture_output=True, text=True).stdout.strip())
_OUTDIR = _os.path.join(_REPO, '.git', 'wb-branch-audit')
_os.makedirs(_OUTDIR, exist_ok=True)
REPO = _REPO
OUT = _os.path.join(_OUTDIR, '_triage_v2.json')


def g(*args):
    r = subprocess.run(['git', '-C', REPO] + list(args),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout.strip(), r.returncode


def blob_eq(ref_a, path_a, ref_b, path_b):
    """比较两个 ref:path 的内容是否相同（用 git diff --quiet）。"""
    _, rc = g('diff', '--quiet', '%s:%s' % (ref_a, path_a), '%s:%s' % (ref_b, path_b))
    return rc == 0


def exists(ref, path):
    _, rc = g('cat-file', '-e', '%s:%s' % (ref, path))
    return rc == 0


def main():
    all_b = [x for x in g('branch', '--format=%(refname:short)')[0].split('\n') if x]
    wt = set()
    for line in g('worktree', 'list', '--porcelain')[0].split('\n'):
        if line.startswith('branch refs/heads/'):
            wt.add(line[len('branch refs/heads/'):])
    now = time.time()
    rows = []
    for b in all_b:
        if b in wt or b == 'master':
            continue
        if g('merge-base', '--is-ancestor', b, 'master')[1] == 0:
            continue
        ts, _ = g('log', '-1', '--format=%ct', b)
        try:
            ts = int(ts)
        except ValueError:
            continue
        age_h = (now - ts) / 3600.0
        base, _ = g('merge-base', 'master', b)
        subj, _ = g('log', '-1', '--format=%s', b)
        uniq = int(g('rev-list', '--count', 'master..%s' % b)[0] or 0)

        files_s, _ = g('diff', '--name-status', base, b)
        entries = []
        for ln in files_s.split('\n'):
            if not ln.strip():
                continue
            p = ln.split('\t')
            if len(p) >= 2:
                entries.append((p[0][:1], p[-1]))  # status 首字母, 路径

        stat = {'ABSORBED': [], 'UNMERGED': [], 'BOTH': [], 'MISSING': [], 'DELETED': []}
        numstat, _ = g('diff', '--numstat', base, b)
        add = dele = 0
        for ln in numstat.split('\n'):
            p = ln.split('\t')
            if len(p) >= 2:
                try:
                    add += int(p[0]) if p[0] != '-' else 0
                    dele += int(p[1]) if p[1] != '-' else 0
                except ValueError:
                    pass

        for st, f in entries:
            in_m = exists('master', f)
            in_b = exists(b, f)
            if st == 'D' or not in_b:
                stat['DELETED' if in_m else 'ABSORBED'].append(f)
                continue
            if not in_m:
                stat['MISSING'].append(f)
                continue
            if blob_eq('master', f, b, f):
                stat['ABSORBED'].append(f)
            elif exists(base, f) and blob_eq('master', f, base, f):
                stat['UNMERGED'].append(f)
            else:
                stat['BOTH'].append(f)

        net = len(stat['UNMERGED']) + len(stat['BOTH']) + len(stat['MISSING']) + len(stat['DELETED'])
        test_files = [f for f in stat['UNMERGED'] + stat['BOTH'] + stat['MISSING']
                      if os.path.basename(f).startswith('test_')]
        doc_files = [f for f in stat['UNMERGED'] + stat['BOTH'] + stat['MISSING'] if f.endswith('.md')]
        code_files = [f for f in stat['UNMERGED'] + stat['BOTH'] + stat['MISSING']
                      if f.endswith('.py') and f not in test_files]

        # 试合并冲突
        mo, mrc = g('merge-tree', '--write-tree', 'master', b)
        nconf = sum(1 for ln in mo.split('\n') if ln.startswith('CONFLICT'))

        if age_h < 2:
            v, why = 'W', '在途（%.1fh）' % age_h
        elif net == 0:
            v, why = 'A0', '净贡献 0：%d 个文件改动均已在 master' % len(stat['ABSORBED'])
        elif nconf == 0 and not code_files and not test_files:
            v, why = 'B1', '无冲突，仅文档/交付物 %d' % len(doc_files)
        elif nconf == 0 and test_files:
            v, why = 'B2', '无冲突，含测试 %d' % len(test_files)
        elif nconf == 0:
            v, why = 'B3', '无冲突，纯代码 %d 无测试' % len(code_files)
        else:
            v, why = 'C', '%d 处冲突，未落 %d 文件' % (nconf, net)

        rows.append({
            'branch': b, 'age_h': round(age_h, 1), 'uniq': uniq,
            'base': base[:9], 'add': add, 'del': dele,
            'net': net, 'conflicts': nconf,
            'cnt': {k: len(v2) for k, v2 in stat.items()},
            'net_files': (stat['UNMERGED'] + stat['BOTH'] + stat['MISSING'] + stat['DELETED'])[:25],
            'missing': stat['MISSING'][:10],
            'tests': test_files[:6], 'code': code_files[:6], 'docs': doc_files[:6],
            'verdict': v, 'why': why, 'subj': subj,
        })
        sys.stderr.write('.')
        sys.stderr.flush()

    sys.stderr.write('\n')
    order = {'A0': 0, 'B1': 1, 'B2': 2, 'B3': 3, 'C': 4, 'W': 5}
    rows.sort(key=lambda r: (order.get(r['verdict'], 9), -r['net']))
    json.dump(rows, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('%-44s %6s %5s %4s %5s %4s %-4s %s' % ('分支', '闲置h', '独有', '净文件', '冲突', '改动', '裁决', '说明'))
    print('-' * 145)
    for r in rows:
        print('%-44s %6.1f %5d %4d %5d %4s %-4s %s' % (
            r['branch'][:44], r['age_h'], r['uniq'], r['net'], r['conflicts'],
            '+%d/-%d' % (r['add'], r['del']), r['verdict'], r['why']))
    print('-' * 145)
    cnt = {}
    for r in rows:
        cnt[r['verdict']] = cnt.get(r['verdict'], 0) + 1
    print('分诊：' + ' / '.join('%s=%d' % (k, cnt[k]) for k in sorted(cnt)))
    print('分支 %d，独有提交 %d，净未落文件合计 %d' % (
        len(rows), sum(r['uniq'] for r in rows), sum(r['net'] for r in rows)))
    print('→', OUT)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
