# -*- coding: utf-8 -*-
"""K-STOCK-219 · index.html 主框架重构校验。

① getElementById 全部命中（死 JS 回归护栏）
② .ic 图标尺寸同一层级内统一（无内联尺寸漂移）
③ chip href 路由合法
④ 文本呈现码点带 FE0F
⑤ HTML 标签平衡（html.parser 宽松解析不抛错）
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
unused = html_ids - js_ids - {"clock"}  # clock 由 tick() 引用已在 js_ids
print("JS 引用 id 数:", len(js_ids), "| HTML id 数:", len(html_ids))

# ② .ic 内联尺寸检查：仅允许今日要务条统一 36px（width:36px;height:36px;font-size:17px）
IC_INLINE = re.compile(r'class="ic" style="([^"]+)"')
inline = IC_INLINE.findall(t)
bad = [s for s in inline if s != "width:36px;height:36px;font-size:17px;"]
if bad:
    errs.append(".ic 内联尺寸漂移: %s" % bad)
print(".ic 内联尺寸:", len(inline), "处（今日要务条，应全同）| 板域卡默认 46px")

# ③ chip href 合法性（对既有路由白名单）
VALID = {"/screen", "/premarket", "/afterhours", "/bt", "/paper", "/strong.html",
         "/fin", "/intel", "/bt-brief", "/kg", "/bt-jev", "/academy.html",
         "/dev.html", "/bt-data-health", "/devops", "/creator", "/gzh", "/vd",
         "/ops", "/quant", "/"}
hrefs = re.findall(r'<a class="chip" href="([^"]+)"', t)
bad_h = [h for h in hrefs if h not in VALID]
if bad_h:
    errs.append("chip 非法路由: %s" % bad_h)
print("chip 入口:", len(hrefs), "个 | todo 占位:",
      len(re.findall(r'class="chip todo"', t)), "个")

# ④ 文本呈现码点必须带 FE0F
for name, cp in (("gear", 0x2699), ("spy", 0x1F575), ("cabinet", 0x1F5C4)):
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
            errs.append("标签交叉: </%s> 栈顶 %s" % (tag, self.stack[-1]))
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            errs.append("多余闭合: </%s>" % tag)
c = Chk()
c.feed(t)
if c.stack:
    errs.append("未闭合标签: %s" % c.stack)

if errs:
    print("\n".join("FAIL " + e for e in errs))
    raise SystemExit(1)
print("ALL PASS")
