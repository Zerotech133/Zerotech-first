# -*- coding: utf-8 -*-
"""K-STOCK-287b: bt2_view.html 缠论买点等级默认改为仅三买（10-13 老田拍板：只看三买）"""
p = r"E:/老田的AI助手/portal/bt2_view.html"
s = open(p, encoding="utf-8").read()
old = '<select name="buy_filter"><option value="23">仅二买+三买（默认）</option><option value="2">仅二买</option><option value="3">仅三买</option><option value="all">一二三类全要</option></select>'
new = '<select name="buy_filter"><option value="3">仅三买（默认）</option><option value="23">二买+三买</option><option value="2">仅二买</option><option value="all">一二三类全要</option></select>'
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, new))
print("bt2_view.html default=3 OK")
