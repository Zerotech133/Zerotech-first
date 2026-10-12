# -*- coding: utf-8 -*-
"""K-STOCK-233 全站风格一致化补丁：悬空 token 修复 + 全局字体单源
铁律：替换前断言 count==预期，替换后断言 old 消失 / new 出现；幂等（重复跑报幂等退出）。
"""
import io, sys

def patch(path, subs):
    with io.open(path, encoding='utf-8') as f:
        t = f.read()
    changed = False
    for old, new, expect in subs:
        n = t.count(old)
        if n == 0 and t.count(new) >= expect:
            print(f'  = {path}: [{old[:36]}] 已是新串（幂等跳过）')
            continue
        assert n == expect, f'{path}: [{old[:40]}] count={n} != 预期{expect}'
        t = t.replace(old, new)
        assert t.count(old) == 0 and t.count(new) >= expect, f'{path}: 替换后断言失败 {old[:40]}'
        changed = True
        print(f'  √ {path}: x{n} [{old[:36]}] -> [{new[:36]}]')
    if changed:
        with io.open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(t)
    return changed

ok = True

# ① tokens.css：补 --font-ui/--font-mono token + 全局 body 字体（单源，裸奔页自动继承）
ok &= patch('portal/static/tokens.css', [(
    "  --r-s: 6px; --r-m: 10px; --r-l: 14px; --r-pill: 16px;\n}",
    "  --r-s: 6px; --r-m: 10px; --r-l: 14px; --r-pill: 16px;\n"
    "\n"
    "  /* ── 字体（2026-10-12 补：此前无全局字体，market.html 等裸奔页退化为浏览器衬线字）── */\n"
    "  --font-ui:   'Segoe UI', 'Microsoft YaHei', system-ui, sans-serif;\n"
    "  --font-mono: Consolas, 'Courier New', monospace;\n"
    "}\n"
    "\n"
    "body { font-family: var(--font-ui); }\n"
    "code, pre, .mono { font-family: var(--font-mono); }\n",
    1)])

# ② market_strategy.html：悬空 --bg-card / --line → 站点 token（卡片才有着落和描边）
ok &= patch('portal/market_strategy.html', [
    ('var(--bg-card)', 'var(--bg-raised)', 5),
    ('var(--line)',    'var(--border)',    10),
])

# ③ paper.html：悬空 --color-down（tokens 正名是 --color-dn）→ 站点 token
ok &= patch('portal/paper.html', [
    ('var(--color-down)', 'var(--color-dn)', 3),
])

# ④ market.html：标题 24px → 全站标准 17px/700（层级一致：每页页题同规格）
ok &= patch('portal/market.html', [
    ('font-size:24px;margin:0 0 4px', 'font-size:17px;font-weight:700;margin:0 0 4px', 1),
])

print('PATCH', 'OK' if ok else 'FAIL')
sys.exit(0 if ok else 1)
