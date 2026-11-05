#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全局顺序演练：把「可干净叠加」的分支按低耦合优先排序，一次性叠加验证。

顺序策略：工具/测试类 → 配置/规范类 → 前端/资产类（改动面越大越靠后）。
如果整串都能叠加成功，说明可以一次性批量合并；某步失败则该分支需单独处理。
"""
import json
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
J = _os.path.join(_OUTDIR, '_plan_v3.json')

# 低耦合优先的手工顺序（基于改动性质）
ORDER = [
    'task/WO-ZHICE-API-8105-20261031',
    'task/WO-ZHICE-V2-PROBE-20261101',
    'task/WO-KSTOCK-MIDNIGHTFIX2-20261101',
    'task/WO-W1-CONFIRM-20261104',
    'task/WO-L2-A6-PREP-20261104',
    'task/WO-EVID-CONTRACT-20261104',
    'task/WO-ROLE-CHECK-20261104',
    'task/WO-ROLE-WORKSPACES-20261104',
    'task/WO-DATA-REG-20261104',
    'task/WO-ACAD1-FREEZE-READY-20261104',
    'task/UI-UNIFY-TOOLFIX-20261103',
    'task/SEC-INTEL-AUTH-20261104',
    'task/K-QUANT-20261030-OHLOOP-TEST',
    'task/WO-ROLE-HOME-20261104',
    'task/UI-UNIFY-VISUAL-20261103',
    'task/WO-LEGION-LOGINUI-20261030',
    'task/WO-LEGION-AUDIT-LOGINLINK-20261030',
]


def g(*args):
    r = subprocess.run(['git', '-C', REPO] + list(args),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout.strip(), r.returncode


def main():
    d = json.load(open(J, encoding='utf-8'))
    rows = {r['branch']: r for r in d['rows']}
    usable = set()
    for p in d['plan']:
        for b in p['ok']:
            if rows[b]['verdict'] != 'A0':
                usable.add(b)
    missing = [b for b in usable if b not in ORDER]
    if missing:
        print('!! 未排入顺序的可用分支：', missing)
    order = [b for b in ORDER if b in usable]

    cur, _ = g('rev-parse', 'master^{tree}')
    ok, fail = [], []
    for b in order:
        base, _ = g('merge-base', 'master', b)
        out, rc = g('merge-tree', '--write-tree', '--merge-base=%s' % base, cur, b)
        if rc == 0:
            cur = out.split('\n')[0].strip()
            ok.append(b)
            print('  OK   %-46s → tree %s' % (b[:46], cur[:9]))
        else:
            conf = [m.group(2).strip() for m in
                    (re.search(r'CONFLICT \(([^)]+)\): (?:Merge conflict in )?(.+)$', ln) for ln in out.split('\n'))
                    if m]
            fail.append((b, conf))
            print('  FAIL %-46s %d 冲突：%s' % (b[:46], len(conf), '; '.join(conf[:2])))
    print('\n全局演练：%d 个依次叠加成功，%d 个失败（共 %d 个）' % (len(ok), len(fail), len(order)))
    json.dump({'ok': ok, 'fail': fail, 'final_tree': cur},
              open(_os.path.join(_OUTDIR, '_global_plan.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
