# -*- coding: utf-8 -*-
"""K-STOCK-258 验证：大屏横幅撤除 + 双助手板块 + 个人应用改名 + 大屏迁移。"""
import re, subprocess, sys

P = r"E:\老田的AI助手\portal\index.html"
t = open(P, encoding="utf-8").read()
fails = []

def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not cond:
        fails.append(name)

# 1. 横幅彻底清零
chk("screen-banner 清零", "screen-banner" not in t)
chk("量化策略数据大屏置顶卡清零", "量化策略数据大屏" not in t)

# 2. 双助手板块
for anchor in ["个人 OS · 双助手", "平行空间的自己", "参谋长 A0",
               'id="a0-dept"', 'id="a0-live"', 'id="a0-note"', 'id="a0-dot"',
               "caiSummon", "/api/home"]:
    chk("双助手锚点 " + anchor, anchor in t)

# 3. 个人应用改名（用户可见文案不再出现「常用应用」；注释除外）
body = t[t.index("<body>"):]
chk("可见文案无「常用应用」", "常用应用" not in body)
chk("divider 个人应用", "🧭 个人应用" in t)
chk("添加应用", "添加应用" in t)

# 4. 大屏迁移：favList 默认与迁移逻辑
chk("大屏迁移 unshift", "out.unshift('/screen.html')" in t)
chk("新装默认含大屏", "return ['/screen.html'].concat(FAV_DEFAULT)" in t)
chk("工具池仍有大屏条目", "'/screen.html'" in t and "数据大屏" in t)

# 5. getElementById 死引用：HTML 静态 id 全集 + 动态创建白名单
static_ids = set(re.findall(r'id="([^"]+)"', t))
dyn_ids = {"ai-orb"}  # cainiao.js 注入，代码里已 if 守卫
refs = set(re.findall(r"getElementById\('([^']+)'\)", t)) | set(re.findall(r'set\(\'([a-z0-9-]+)\'', t))
missing = {r for r in refs if r not in static_ids and r not in dyn_ids}
chk("id 引用零死链", not missing, "缺失: " + ",".join(sorted(missing)) if missing else "")

# 6. 括号平衡粗检（script 块）
m = re.search(r"<script>\n(.*?)</script>", t, re.S)
js = m.group(1)
for a, b, nm in [("(", ")", "圆括号"), ("{", "}", "花括号"), ("[", "]", "方括号")]:
    chk(nm + "平衡", js.count(a) == js.count(b), "%d vs %d" % (js.count(a), js.count(b)))

# 7. node --check 语法
open(r"E:\老田的AI助手\work\verify_259_inline.js", "w", encoding="utf-8").write(js)
r = subprocess.run([r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe",
                    "--check", r"E:\老田的AI助手\work\verify_259_inline.js"],
                   capture_output=True, text=True)
chk("node --check", r.returncode == 0, (r.stderr or "")[:200])

print("\n== %s ==" % ("全部通过" if not fails else "失败 %d 项: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
