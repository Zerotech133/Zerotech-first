# -*- coding: utf-8 -*-
"""K-STOCK-235 页面宽度单源化：--page-w token + 6 个偏差页归一
标准 = 首页 1080px（老田已拍板的最初风格基线）。
纪律：动态计数→替换→复断言，幂等。
"""
import io, sys, re

def unify(path):
    with io.open(path, encoding='utf-8') as f:
        t = f.read()
    if 'var(--page-w)' in t:
        print(f'  = {path}: 已归一（幂等跳过）')
        return True
    total = 0
    for pat in ['max-width:1180px', 'max-width: 1180px', 'max-width:1280px', 'max-width: 1280px']:
        n = t.count(pat)
        if n:
            new = pat.replace('1180px', 'var(--page-w)').replace('1280px', 'var(--page-w)')
            t = t.replace(pat, new)
            total += n
            print(f'  √ {path}: x{n} [{pat}] -> [max-width:var(--page-w)]')
    assert total > 0, f'{path}: 未找到偏差宽度（预期 1180/1280）'
    assert '1180px' not in t and '1280px' not in t, f'{path}: 仍有残留宽度'
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(t)
    return True

ok = True
# ① tokens.css：单源 token（缩进对齐字号段后）
with io.open('portal/static/tokens.css', encoding='utf-8') as f:
    tok = f.read()
if '--page-w' not in tok:
    anchor = '  --r-s: 6px; --r-m: 10px; --r-l: 14px; --r-pill: 16px;'
    assert tok.count(anchor) == 1
    tok = tok.replace(anchor, anchor + '\n  --page-w: 1080px;  /* 内容区统一宽度（=首页标准，2026-10-13）*/')
    with io.open('portal/static/tokens.css', 'w', encoding='utf-8', newline='') as f:
        f.write(tok)
    print('  √ tokens.css: +--page-w: 1080px')
else:
    print('  = tokens.css: --page-w 已存在')

# ② 六个偏差页归一（1080 页已是标准不动；screen.html 影院页全屏特例不动）
for p in ['portal/market.html', 'portal/ops.html', 'portal/picks.html',
          'portal/quant.html', 'portal/market_strategy.html', 'portal/paper.html']:
    ok &= unify(p)

print('PATCH', 'OK' if ok else 'FAIL')
sys.exit(0 if ok else 1)
