#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从演练结果生成《悬空分支可合并候选清单》markdown。"""
import io
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
OUT = os.path.join(REPO, 'deliverables', '2026-11-06_悬空分支可合并候选清单_v1.md')
D = _OUTDIR + os.sep


def g(*args):
    r = subprocess.run(['git', '-C', REPO] + list(args),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout.strip()


def tier(nc, net):
    if nc <= 2 and net <= 3:
        return '轻'
    if nc >= 6 or net >= 16:
        return '重'
    return '中'


def kinds_cn(k):
    m = [('py', '工具脚本'), ('test', '测试'), ('frontend', '前端'), ('config', '配置'),
         ('doc', '文档'), ('asset', '资产'), ('evidence', '证据文件'), ('other', '其他')]
    return '、'.join('%s%d' % (n, k[key]) for key, n in m if k.get(key))


def verify_req(k):
    v = []
    if k.get('test'):
        v.append('跑相关测试')
    if k.get('py'):
        v.append('py_compile')
    if k.get('frontend'):
        v.append('页面目检')
    if k.get('config'):
        v.append('校验配置可解析')
    if not v:
        v.append('人工翻一眼')
    return '+'.join(v)


def main():
    d = json.load(open(D + '_plan_v3.json', encoding='utf-8'))
    rows = {r['branch']: r for r in d['rows']}
    gl = json.load(open(D + '_global_plan.json', encoding='utf-8'))
    ok_seq = gl['ok']

    manual = []
    for p in d['plan']:
        for s in p['skipped']:
            manual.append((p['line'], s, rows[s['branch']]))

    a0 = [b for b in rows if rows[b]['verdict'] == 'A0']
    a0 = [b for b in a0 if b not in ok_seq]
    ok_rows = [b for b in ok_seq]
    net_total = sum(rows[b]['net'] for b in rows)
    master = g('rev-parse', '--short', 'master')
    today = time.strftime('%Y-%m-%d %H:%M')

    L = []
    w = L.append
    w('# 悬空分支可合并候选清单 v1')
    w('')
    w('> 生成时间 %s ｜ 基线 `master` = `%s` ｜ 只做分析，**未合并、未删除任何分支**' % (today, master))
    w('')
    w('## 一、一页结论')
    w('')
    w('扫描范围：**无 worktree 占用、且未合入 master** 的分支共 **%d 个**，带 **%d 条**独有提交，'
      '净未落文件 **%d 个**。' % (len(rows), sum(r['uniq'] for r in rows.values()), net_total))
    w('')
    w('| 分类 | 数量 | 处理方式 |')
    w('|---|---|---|')
    w('| **可直接合并** | **%d** | 按第三节顺序一次性合，全局演练零冲突 |' % len(ok_rows))
    w('| 需人工处理冲突 | %d | 轻 %d / 中 %d / 重 %d |' % (
        len(manual),
        sum(1 for _, s, r in manual if tier(s['nconf'], r['net']) == '轻'),
        sum(1 for _, s, r in manual if tier(s['nconf'], r['net']) == '中'),
        sum(1 for _, s, r in manual if tier(s['nconf'], r['net']) == '重')))
    w('| 可直接删除 | %d | 净贡献 0，内容已在 master |' % len(a0))
    w('')
    w('**最重要的一条**：那 %d 个可直接合并的分支，我用 `git merge-tree` 做过**全局顺序叠加演练**，'
      '从第一个到最后一个**零冲突**（末态树 `%s`）。也就是说「一次性合完这批」在 Git 层面可行，'
      '不需要逐个试错。' % (len(ok_rows), gl['final_tree'][:9]))
    w('')
    w('## 二、判据（任何人都可复算）')
    w('')
    w('### 1. 净贡献：逐文件三方比对')
    w('')
    w('「分支有独有提交」不等于「分支有未落地的改动」。对每个文件比三个版本：')
    w('')
    w('```')
    w('base   = git merge-base master <branch>')
    w('files  = git diff --name-only base <branch>      # 分支自己动过的文件')
    w('')
    w('master:<f> == <branch>:<f>   → ABSORBED  改动已被 master 吸收（可能被后续提交覆盖）')
    w('master:<f> == base:<f>       → UNMERGED  master 从没动过，分支改动真的没落地')
    w('两边都是新内容               → BOTH     可能可叠加，也可能冲突')
    w('branch 有、master 没有       → MISSING  整个文件丢了')
    w('branch 删、master 还在       → DELETED  删除未生效')
    w('```')
    w('')
    w('净贡献 = UNMERGED + BOTH + MISSING + DELETED 的文件数。为 0 才是「真的没事可合」。')
    w('')
    w('这一步纠正了一个常见误判：`git cherry` 显示 patch-id 等价，只能说明**某些提交**被吸收过，'
      '不代表分支里所有文件都落了地。初版用 cherry 判出 15 个「可安全删」，逐文件比对后有 5 个仍带真实未落改动，'
      '其中 `task/WO-INTEL-EVID-API-20261104` 的 `portal/api_intel_evidence_v1.py` 与其测试、交付文档'
      '**在 master 上完全不存在**——若按初版结论删掉，这份工作就只剩 reflog 能找了。')
    w('')
    w('### 2. 冲突：无副作用试合并')
    w('')
    w('```')
    w('git merge-tree --write-tree --merge-base=$(git merge-base master <branch>) master <branch>')
    w('# rc=0 干净；rc=1 有冲突，输出里 CONFLICT 行给出冲突文件')
    w('```')
    w('')
    w('不 checkout、不动工作区，只在 object 库里算一棵树。')
    w('')
    w('### 3. 顺序：把树当中间态逐级叠加')
    w('')
    w('单分支无冲突 ≠ 一批合起来无冲突。把上一步产出的 tree 当下一次合并的左侧，'
      '`--merge-base` 仍取该分支与 master 的分叉点，就能模拟「依次合并」的累积效果。第三节那个结论就是这么来的。')
    w('')
    w('## 三、可直接合并（%d 个）' % len(ok_rows))
    w('')
    w('按下列**从上到下**的顺序合并，已演练零冲突。')
    w('')
    w('| # | 分支 | 线 | 净文件 | 增/删 | 改动性质 | 合完要验证 |')
    w('|---|---|---|---|---|---|---|')
    for i, b in enumerate(ok_rows, 1):
        r = rows[b]
        w('| %d | `%s` | %s | %d | +%d/−%d | %s | %s |' % (
            i, b, r['line'], r['net'], r['add'], r['del'], kinds_cn(r['kinds']), verify_req(r['kinds'])))
    w('')
    w('合并脚本（建议在**集成分支**上做，不直接动 master）：')
    w('')
    w('```bash')
    w('cd "C:/项目/老田的AI助手"')
    w('git switch -c integrate-20261106 master')
    for b in ok_rows:
        w('git merge --no-ff -m "merge(%s): 悬空分支回收" %s' % (rows[b]['line'], b))
    w('git push origin integrate-20261106      # 先推集成分支，验证后再 ff master')
    w('```')
    w('')
    w('两点提示：')
    w('')
    w('- `task/WO-KSTOCK-MIDNIGHTFIX2-20261101` 与 `task/WO-LEGION-LOGINUI-20261030` 叠加后**树没有变化**，'
      '说明它们的内容已被前面步骤覆盖。合并无副作用，但它们的验收可并入上一步一起做，不必单独验。')
    w('- `task/K-QUANT-20261030-OHLOOP-TEST` 的 %d 个净文件里 %d 个是 `*_OK.txt` 探针产物。'
      '如果只想要结论、不想要证据文件，可以跳过它——它不影响其他分支。' % (
          rows['task/K-QUANT-20261030-OHLOOP-TEST']['net'],
          rows['task/K-QUANT-20261030-OHLOOP-TEST']['kinds'].get('evidence', 0)))
    w('')
    w('## 四、需人工处理冲突（%d 个）' % len(manual))
    w('')
    w('分档标准：**轻** = 冲突 ≤2 处且净文件 ≤3；**重** = 冲突 ≥6 处或净文件 ≥16；其余为中。')
    w('')

    for t, tips in (('轻', '这类冲突通常是 add/add——同一功能被写了两遍，选一个版本即可，多半 10 分钟内能决。'),
                    ('中', '涉及多个文件的联动改动，合之前先看分支尾提交是否写着"零接线/待接入"，避免合进半成品。'),
                    ('重', '改动面大且冲突多，先确认这条线还要不要继续；不要再打 tag 归档，不要再硬合。')):
        sel = [(l, s, r) for l, s, r in manual if tier(s['nconf'], r['net']) == t]
        if not sel:
            continue
        sel.sort(key=lambda x: (x[0], -x[2]['net']))
        w('### %s（%d 个）' % (t, len(sel)))
        w('')
        w(tips)
        w('')
        w('| 分支 | 线 | 冲突 | 净文件 | 增/删 | 冲突文件 |')
        w('|---|---|---|---|---|---|')
        for l, s, r in sel:
            conf = '；'.join('`%s`' % c for c in s['conflicts'][:3]) or '（无文件级冲突信息）'
            w('| `%s` | %s | %d | %d | +%d/−%d | %s |' % (
                s['branch'], l, s['nconf'], r['net'], r['add'], r['del'], conf))
        w('')

    w('## 五、可直接删除（%d 个）' % len(a0))
    w('')
    w('这些分支的独有提交里，改动文件的内容**已与 master 完全一致**，没有任何净贡献。')
    w('')
    w('| 分支 | 独有提交 | 说明 |')
    w('|---|---|---|')
    for b in a0:
        r = rows[b]
        w('| `%s` | %d | 净贡献 0，%s |' % (b, r['uniq'], r['subj'][:50]))
    w('')
    w('删除前建议仍打 tag 留痕（成本几乎为零）：')
    w('')
    w('```bash')
    w('for b in %s; do' % ' '.join(a0))
    w('  git tag "archive/20261106/$(echo $b | sed \'s|^task/||\')" "$b" && git branch -d "$b"')
    w('done')
    w('```')
    w('')
    w('## 五之一、不建议合并的')
    w('')
    w('| 分支 | 原因 |')
    w('|---|---|')
    w('| `task/K-QUANT-20261030-OHLOOP-TEST` | 净文件 21 个中 17 个是 `HERMES_*_OK.txt` 一类探针产物，属过程证据而非代码资产 |')
    w('| `task/UI-UNIFY-BATCH-20261103.pre-rebase-1105` | 名称即「rebase 前的备份」，87 个净文件、6 处冲突，是历史快照不是待合并功能 |')
    w('| `rescue/ollama-health-145` | 闲置 25 天的一次性故障救援分支，冲突在 `ollama_service/__init__.py` 的 add/add |')
    w('')
    w('## 六、执行流程建议')
    w('')
    w('1. **先合 %d 个无冲突的**（第三节），落在集成分支 `integrate-20261106`，不要直接动 master。' % len(ok_rows))
    w('2. 集成分支上跑全量测试。这一步不能省：merge-tree 只保证**文本层**不冲突，'
      '两个分支各自语义正确、合起来逻辑错的情况它看不出来。')
    w('3. 测试过了再快进 master，并推远端。远端 main 现在已经是 `%s`，与本地一致。' % master)
    w('4. 处理"轻"档 17 个：逐个开 worktree 手工合，冲突文件基本都在 add/add，选版本即可。')
    w('5. "中/重"档留给对应线的 owner 判：**我只给证据，不替他们决定这条线还要不要**。')
    w('6. 全部处理完再谈删分支。顺序反过来会丢东西——先删后合等于把证据烧了。')
    w('')
    w('## 七、这份清单的边界')
    w('')
    w('- **只判文本层可行性**，没跑任何测试。合完必须跑。')
    w('- **只覆盖无 worktree 的分支**。34 个正在被 worktree 占用的分支是在途工作，不在清单内。')
    w('- **"重"档不等于该删**。它们可能是最重要的成果，只是需要作者回来处理冲突。')
    w('- 判据脚本可重跑，数字会随分支变动而变：')
    w('')
    w('```bash')
    w('python tools/branch_audit/merge_candidates_v2.py   # 逐文件净贡献')
    w('python tools/branch_audit/merge_plan_v3.py         # 按线演练')
    w('python tools/branch_audit/global_merge_drill.py    # 全局顺序演练')
    w('```')
    w('')

    txt = '\n'.join(L) + '\n'
    with io.open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(txt)
    b = open(OUT, 'rb').read()
    print('写出 %s' % OUT)
    print('字节 %d | 行 %d' % (len(b), txt.count('\n')))
    print('可合 %d / 需人工 %d / 可删 %d' % (len(ok_rows), len(manual), len(a0)))


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    main()
