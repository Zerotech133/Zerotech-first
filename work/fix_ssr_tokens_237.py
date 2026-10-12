# -*- coding: utf-8 -*-
# K-STOCK-237: SSR 动态页统一 tokens.css + 宽度归一 + 静态页底边距归一
# 纪律：替换前断言 count(old)==期望值，替换后断言 new in text 且 old 消失（替换式）
import py_compile, sys

LINK = "<link rel='stylesheet' href='/static/tokens.css'>"
ANCHOR = "<head><meta charset='utf-8'>"
NEW_HEAD = ANCHOR + LINK

def patch(path, pairs):
    t = open(path, encoding='utf-8').read()
    for old, new, want in pairs:
        n = t.count(old)
        assert n == want, f"{path}: {old!r} 计数 {n} != 期望 {want}"
        t = t.replace(old, new)
        assert t.count(new) >= want, f"{path}: 替换后 new 计数不足 {old!r}"
    open(path, 'w', encoding='utf-8', newline='').write(t)
    print(f"OK {path}")

# ① 六个未挂 tokens.css 的 SSR 页：head 注入（锚点计数已实测）
patch('portal/api_bridge.py',        [(ANCHOR, NEW_HEAD, 3)])
patch('portal/api_chanlun_pages.py', [(ANCHOR, NEW_HEAD, 2)])
patch('portal/api_data.py',          [(ANCHOR, NEW_HEAD, 1)])
patch('portal/api_kg.py',            [(ANCHOR, NEW_HEAD, 15)])
patch('portal/api_portfolio.py',     [(ANCHOR, NEW_HEAD, 4)])
patch('portal/api_screen.py',        [(ANCHOR, NEW_HEAD, 2)])

# ② SSR 页容器宽度归一 var(--page-w)（kg×3 / backtest×1 / misc×1）
patch('portal/api_kg.py',      [('max-width:1180px', 'max-width:var(--page-w)', 3)])
patch('portal/api_backtest.py',[('max-width:1180px', 'max-width:var(--page-w)', 1)])
patch('portal/api_misc.py',    [('.sb-wrap{max-width:1100px', '.sb-wrap{max-width:var(--page-w)', 1)])

# ③ 静态页 main 底边距归一（index/academy 48→56、移动端 36→40，取全站多数档）
for f in ('portal/index.html', 'portal/academy.html'):
    patch(f, [('0 26px 48px', '0 26px 56px', 1), ('0 14px 36px', '0 14px 40px', 1)])

# ④ 语法校验：所有被改的 .py 必须 py_compile 通过
for f in ('portal/api_bridge.py', 'portal/api_chanlun_pages.py', 'portal/api_data.py',
          'portal/api_kg.py', 'portal/api_portfolio.py', 'portal/api_screen.py',
          'portal/api_backtest.py', 'portal/api_misc.py'):
    py_compile.compile(f, doraise=True)
print("py_compile 8/8 OK")
