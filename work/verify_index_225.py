# -*- coding: utf-8 -*-
"""K-STOCK-225 · 门面方案B（核心侧栏驾驶舱）校验。

① getElementById 全部命中（死 JS 回归护栏）
② 等大铁律：.card 恰 6 张，每卡同构（1 ic + 1 desc + 1 foot），grid 行高锁死
③ tag href 路由合法 + 每卡入口 ≤3（more 占位不计）
④ 文本呈现码点带 FE0F
⑤ 标签平衡
⑥ 旧结构零残留（topnav/screen-banner/今日要务 t-* id）
"""
import io, re
from html.parser import HTMLParser

P = "portal/index.html"
t = io.open(P, encoding="utf-8").read()
errs = []

# ① getElementById 目标全部存在
js_ids = set(re.findall(r"getElementById\('([^']+)'\)", t))
html_ids = set(re.findall(r'id="([^"]+)"', t))
missing = js_ids - html_ids
if missing:
    errs.append("JS 引用不存在 id: %s" % sorted(missing))
print("JS 引用 id:", len(js_ids), "| HTML id:", len(html_ids))

# ② 等大同构（按卡片起始位置切片：卡内 tag 的 </a> 会截断正则，故不用非贪婪匹配）
n_card = len(re.findall(r'<a class="card" href=', t))
if n_card != 6:
    errs.append("卡数 %d != 6" % n_card)
pos_list = [m.start() for m in re.finditer(r'<a class="card" href="([^"]+)"', t)]
for idx, pos in enumerate(pos_list):
    end = pos_list[idx + 1] if idx + 1 < len(pos_list) else len(t)
    seg = t[pos:end]
    href = re.match(r'<a class="card" href="([^"]+)"', seg).group(1)
    for cls, want in (("ic", 1), ("desc", 1), ("foot", 1), ("row1", 1)):
        got = len(re.findall(r'class="%s"' % cls, seg))
        if got != want:
            errs.append("卡 %s: .%s x%d != %d" % (href, cls, got, want))
    n_tag = len(re.findall(r'class="tag[" ]', seg))
    if n_tag > 4:  # ≤3 入口 + 1 个 more 占位
        errs.append("卡 %s: tag %d > 4" % (href, n_tag))
if 'grid-auto-rows:' not in t:
    errs.append("缺 grid-auto-rows 等大锁死")
print("卡数:", n_card, "| grid-auto-rows 锁死:", 'grid-auto-rows:' in t)

# ③ tag href 白名单
VALID = {"/screen", "/premarket", "/paper", "/intel", "/bt-brief", "/kg",
         "/academy.html", "/dev.html", "/bt-data-health", "/devops",
         "/creator", "/gzh", "/vd", "/ops", "/sys-health", "/audit", "/quant"}
hrefs = re.findall(r'<a class="tag" href="([^"]+)"', t)
bad_h = [h for h in hrefs if h not in VALID]
if bad_h:
    errs.append("tag 非法路由: %s" % bad_h)
print("tag 入口:", len(hrefs), "个")

# ④ 文本呈现码点必须带 FE0F
for name, cp in (("gear", 0x2699), ("cabinet", 0x1F5C4), ("spy", 0x1F575)):
    bare = chr(cp)
    n_bare = t.count(bare) - t.count(bare + "\uFE0F")
    if n_bare:
        errs.append("%s 有 %d 处缺 FE0F" % (name, n_bare))

# ⑤ 标签平衡
class Chk(HTMLParser):
    VOID = {"meta", "link", "br", "img", "input", "hr"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)
    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            errs.append("标签交叉: </%s>" % tag)
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            errs.append("多余闭合: </%s>" % tag)
        # 注意：闭合作业对 </a> 内嵌 <a> 等异常由交叉分支暴露
c = Chk()
c.feed(t)
if c.stack:
    errs.append("未闭合标签: %s" % c.stack)

# ⑥ 旧结构零残留
for kw in ("topnav", "screen-banner", "今日要务", "t-q-verdict", "t-i-news",
           "loadToday", "loadStrategies", "statusbar"):
    if kw in t:
        errs.append("旧结构残留: %s" % kw)

if errs:
    print("\n".join("FAIL " + e for e in errs))
    raise SystemExit(1)
print("ALL PASS")
