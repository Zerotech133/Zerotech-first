#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并演练 v3：按「线」分组 → 组内按时间序 → 用 merge-tree 逐级叠加模拟合并。

目的：回答「这批悬空分支到底能不能合、按什么顺序合」。
不修改任何 ref、不 checkout、不碰工作区（merge-tree --write-tree 只写 object）。

对每组：
  cur = master 的 tree
  依次把组内分支叠加上去，base 取 merge-base(master, 该分支)
  叠加成功 → cur 前移；冲突 → 记录冲突文件，跳过该分支继续（尽力序）
输出每组的可行序列、被跳过的分支与冲突文件。
"""
import json
import os
import re
import subprocess
import sys

import os as _os
_REPO = (_os.environ.get('WB_REPO')
         or subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                           capture_output=True, text=True).stdout.strip())
_OUTDIR = _os.path.join(_REPO, '.git', 'wb-branch-audit')
_os.makedirs(_OUTDIR, exist_ok=True)
REPO = _REPO
OUT = _os.path.join(_OUTDIR, '_plan_v3.json')

# 线的识别：按前缀，长前缀在前
LINES = [
    ('WO-IFS', ('task/WO-IFS-',)),
    ('UI-UNIFY', ('task/UI-UNIFY-',)),
    ('WO-ACAD1', ('task/WO-ACAD1-',)),
    ('WO-D1', ('task/WO-D1-',)),
    ('LEGION', ('task/WO-DEV-LEGION-', 'task/WO-LEGION-', 'task/LEGION-')),
    ('INTEL', ('task/WO-INTEL-', 'task/SEC-INTEL-')),
    ('K-ORG', ('task/K-ORG-',)),
    ('K-QUANT', ('task/K-QUANT-',)),
    ('WO-ROLE', ('task/WO-ROLE-',)),
    ('WO-BANDIT', ('task/WO-BANDIT-',)),
    ('WO-ZHICE', ('task/WO-ZHICE-',)),
    ('WO-STRAT', ('task/WO-STRAT-',)),
    ('WO-NAV', ('task/WO-NAV-',)),
    ('WO-KSTOCK', ('task/WO-KSTOCK-', 'task/K-STOCK-')),
    ('WO-SEAT', ('task/WO-SEAT-',)),
    ('WO-HZ', ('task/WO-HZ-', 'task/K-ORG-20261031-HZ')),
    ('WO-KB', ('task/WO-KB-',)),
    ('TICKET', ('task/TICKET-',)),
    ('WO-M4', ('task/WO-M4-',)),
    ('WO-L2', ('task/WO-L2-',)),
    ('WO-W1', ('task/WO-W1-',)),
    ('WO-EVID', ('task/WO-EVID-',)),
    ('WO-DATA', ('task/WO-DATA-',)),
    ('WO-COMP', ('task/WO-COMP-',)),
    ('RESCUE', ('rescue/',)),
]


def g(*args):
    r = subprocess.run(['git', '-C', REPO] + list(args),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout.strip(), r.returncode


def line_of(branch):
    for name, prefixes in LINES:
        for p in prefixes:
            if branch.startswith(p):
                return name
    return 'OTHER'


def classify(files):
    """净文件按性质分类，前端/配置都算「要验证」的东西。"""
    c = {'doc': [], 'test': [], 'py': [], 'frontend': [], 'config': [],
         'asset': [], 'evidence': [], 'other': []}
    for f in files:
        base = os.path.basename(f)
        ext = os.path.splitext(f)[1].lower()
        if ext == '.md':
            c['doc'].append(f)
        elif base.startswith('test_') and ext in ('.py', '.js', '.ts'):
            c['test'].append(f)
        elif ext in ('.py',):
            c['py'].append(f)
        elif ext in ('.html', '.css', '.js', '.ts', '.tsx', '.jsx', '.vue'):
            c['frontend'].append(f)
        elif ext in ('.yaml', '.yml', '.json', '.toml', '.ini', '.conf', '.sh', '.ps1'):
            c['config'].append(f)
        elif ext in ('.png', '.jpg', '.jpeg', '.svg', '.webp', '.gif'):
            c['asset'].append(f)
        elif ext in ('.txt', '.log', '.csv'):
            c['evidence'].append(f)
        else:
            c['other'].append(f)
    return c


def main():
    rows = json.load(open(_os.path.join(_OUTDIR, '_triage_v2.json'), encoding='utf-8'))
    # 富化：净文件分类 + 所属线
    for r in rows:
        r['kinds'] = {k: len(v) for k, v in classify(r['net_files']).items()}
        r['line'] = line_of(r['branch'])

    master_tree, _ = g('rev-parse', 'master^{tree}')

    # 按线分组
    groups = {}
    for r in rows:
        groups.setdefault(r['line'], []).append(r)

    plan = []
    for line, items in sorted(groups.items()):
        items.sort(key=lambda r: (r['age_h'] * -1))  # 越久之前做的越先做 → age 大的先
        cur = master_tree
        seq_ok, skipped = [], []
        for r in items:
            b = r['branch']
            base, _ = g('merge-base', 'master', b)
            out, rc = g('merge-tree', '--write-tree', '--merge-base=%s' % base, cur, b)
            if rc == 0:
                cur = out.split('\n')[0].strip()
                seq_ok.append(b)
            else:
                conf = []
                for ln in out.split('\n'):
                    # 冲突类型可能是 content / add/add / modify/delete / rename/rename 等
                    m = re.search(r'CONFLICT \(([^)]+)\): (?:Merge conflict in )?(.+)$', ln)
                    if m:
                        conf.append(m.group(1).strip())
                skipped.append({'branch': b, 'conflicts': conf[:8], 'nconf': len(conf)})
        plan.append({
            'line': line, 'n': len(items),
            'net': sum(r['net'] for r in items),
            'ok': seq_ok, 'skipped': skipped,
            'items': [{'branch': r['branch'], 'verdict': r['verdict'], 'net': r['net'],
                       'age_h': r['age_h'], 'uniq': r['uniq'], 'kinds': r['kinds'],
                       'subj': r['subj'], 'net_files': r['net_files'][:10],
                       'conflicts': r['conflicts']} for r in items],
        })

    json.dump({'plan': plan, 'rows': rows}, open(OUT, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    print('%-10s %3s %5s %4s %4s  %s' % ('线', '数', '净文件', '可合', '跳过', '说明'))
    print('-' * 110)
    tot_ok = tot_skip = 0
    for p in plan:
        tot_ok += len(p['ok'])
        tot_skip += len(p['skipped'])
        note = ''
        if p['skipped']:
            note = '首个冲突：%s（%d 处）' % (p['skipped'][0]['branch'][:34], p['skipped'][0]['nconf'])
        print('%-10s %3d %5d %4d %4d  %s' % (p['line'], p['n'], p['net'], len(p['ok']), len(p['skipped']), note))
    print('-' * 110)
    print('按线依次合并演练：可干净叠加 %d 个分支，需人工 %d 个' % (tot_ok, tot_skip))
    print('→', OUT)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
