# -*- coding: utf-8 -*-
"""K-STOCK-285c: bt2_view.html 加缠论买点等级下拉（仅二买+三买默认·10-13 拍板）"""

p = r"C:/项目/老田的AI助手/portal/bt2_view.html"
s = open(p, encoding="utf-8").read()

old1 = '<div class="fld"><label>持有上限(日·0不限)</label><input name="holding_max_days" type="number" value="0" min="0" max="60"></div>'
new1 = (
    old1
    + '\n  <div class="fld"><label>缠论买点等级</label><select name="buy_filter"><option value="23">仅二买+三买（默认）</option><option value="2">仅二买</option><option value="3">仅三买</option><option value="all">一二三类全要</option></select></div>'
)
assert s.count(old1) == 1, "form anchor not unique"
s = s.replace(old1, new1)

old3 = '持有上限=${c.holding_max_days||"不限"} · 信号源='
assert s.count(old3) == 1, "meta anchor not unique"
s = s.replace(
    old3,
    '持有上限=${c.holding_max_days||"不限"} · 买点等级=${({23:"二买+三买",2:"仅二买",3:"仅三买",all:"全要"})[DATA.meta.buy_filter]||"-"} · 信号源=',
)

open(p, "w", encoding="utf-8").write(s)
print("bt2_view.html patched OK")
