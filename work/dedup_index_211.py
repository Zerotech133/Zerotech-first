# -*- coding: utf-8 -*-
"""index.html 门面去重（K-STOCK-211）：删除 board 2 中与 board 1 重复的
4 张域卡（/quant /intel /creator /dev.html）与「更多领域 预留位」卡。
判据：board 1 紧凑卡带 style="min-height:0"，board 2 重复卡无 style。
先断言 count==1 再替换（U9 纪律），逐卡核对删前删后数量。"""
import re

P = "portal/index.html"
src = open(P, encoding="utf-8").read()

# 匹配 <a class="dm" href="X"> ... </a>（非贪婪，跨行）
pat = re.compile(
    r'<a class="dm(?: soon)?" href="(?P<href>/quant|/intel|/creator|'
    r'/dev\.html|/dev\.html#more)">(?:[^<]|<(?!/a>))*?</a>\s*', re.S)

hits = list(pat.finditer(src))
print("候选块：")
for m in hits:
    style = 'style="min-height:0"' in m.group(0)
    print(f"  href={m.group('href'):16s} style紧凑卡={style} "
          f"span={m.start()}-{m.end()}")
targets = [m for m in hits if 'style="min-height:0"' not in m.group(0)]
# 期望恰好 5 张（4 重复 + 1 预留位）
assert len(targets) == 5, f"期望 5 张待删卡，实得 {len(targets)}，中止"
for m in targets:
    assert src.count(m.group(0)) == 1, "锚点不唯一，中止"
for m in sorted(targets, key=lambda x: x.start(), reverse=True):
    src = src[:m.start()] + src[m.end():]

# 复核：board 1 四张紧凑卡仍在，重复卡归零
assert src.count('href="/quant" style') == 1
assert src.count('href="/intel" style') == 1
assert src.count('href="/creator" style') == 1
assert src.count('href="/dev.html" style') == 1
assert src.count('href="/dev.html#more"') == 0
assert src.count('<a class="dm" href="/quant">') == 0
assert src.count('<a class="dm" href="/intel">') == 0
assert src.count('<a class="dm" href="/creator">') == 0
assert src.count('<a class="dm" href="/dev.html">') == 0
assert src.count('<a class="dm"') == 9, f"剩余卡数 {src.count('<a class=\"dm\"')}"

open(P, "w", encoding="utf-8", newline="").write(src)
print("删除 5 张卡完成；剩余 <a class=dm> 卡数:", src.count('<a class="dm"'))
