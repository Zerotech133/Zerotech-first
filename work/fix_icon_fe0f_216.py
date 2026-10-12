# -*- coding: utf-8 -*-
"""K-STOCK-216 · index.html 图标风格一致化：文本呈现码点补 U+FE0F 变体符。

⚙(2699)/🕵(1F575)/🗄(1F5C4) 默认文本呈现，Windows 上渲染为灰色细线字形，
与页面其余彩色 emoji 割裂；补 FE0F 强制 emoji 呈现。幂等：已带 FE0F 不再补。
"""
import io

P = "portal/index.html"
t = io.open(P, encoding="utf-8").read()

FIXES = [("\U0001F575", 2), ("\u2699", 2), ("\U0001F5C4", 1)]  # (字符, 期望处数)

for ch, expect in FIXES:
    bare = ch + "\uFE0F"
    assert bare not in t, "already fixed: %r" % ch          # 幂等护栏
    n = t.count(ch)
    assert n == expect, "count(%r)=%d != %d" % (ch, n, expect)
    t = t.replace(ch, bare)

io.open(P, "w", encoding="utf-8", newline="").write(t)

# 替换后只断言 new in text（追加式替换，「旧串消失」断言不适用）
back = io.open(P, encoding="utf-8").read()
for ch, expect in FIXES:
    assert back.count(ch + "\uFE0F") == expect, "post-check fail: %r" % ch
print("OK: FE0F added ->", ", ".join("%s x%d" % (hex(ord(c)), e) for c, e in FIXES))
