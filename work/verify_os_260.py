# -*- coding: utf-8 -*-
"""K-STOCK-260 验证：os.html 大屏页 + index 下钻卡 + 8080/8091 链路 + JS 语法。"""
import re, subprocess, sys, urllib.request, json

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails = []
def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not cond:
        fails.append(name)

# 1. 8091 回归：重启后各端点健康
for ep in ("/health", "/api/os", "/api/brief", "/api/home", "/api/strategies", "/api/paper_status"):
    try:
        r = op.open("http://127.0.0.1:8091" + ep, timeout=45)
        chk("8091 " + ep, r.status == 200, "HTTP %d" % r.status)
    except Exception as e:
        chk("8091 " + ep, False, str(e)[:80])

# 2. 8080 os.html 关键内容
t = op.open("http://127.0.0.1:8080/os.html", timeout=8).read().decode("utf-8", "replace")
for k in ["个人 OS 指挥大屏", "今日采集", "Agent 动向", "量化产出", "入库与分析",
          "/api/os", "/api/home", "/api/overview", "loadOS", "regime", "cainiao.js",
          "返回首页", "--page-w"]:
    chk("os.html " + k, k in t)

# 3. 8080 index.html 下钻卡
idx = op.open("http://127.0.0.1:8080/", timeout=8).read().decode("utf-8", "replace")
for k in ["/os.html", "指挥大屏", "拉起大屏", "个人 OS · 双助手"]:
    chk("index " + k, k in idx)

# 4. os.html 内联 JS 语法（node --check）
m = re.search(r"<script>\n(.*?)</script>", t, re.S)
js = m.group(1)
open(r"E:\老田的AI助手\work\verify_260_inline.js", "w", encoding="utf-8").write(js)
r = subprocess.run([r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe",
                    "--check", r"E:\老田的AI助手\work\verify_260_inline.js"],
                   capture_output=True, text=True)
chk("os.html node --check", r.returncode == 0, (r.stderr or "")[:200])

# 5. os.html id 引用零死链（页面内 getElementById/set 全命中静态 id）
static_ids = set(re.findall(r'id="([^"]+)"', t))
refs = set(re.findall(r"getElementById\('([^']+)'\)", t)) | set(re.findall(r"set\('([a-z0-9-]+)'", t))
missing = {x for x in refs if x not in static_ids}
chk("os.html id 零死链", not missing, "缺失: " + ",".join(sorted(missing)) if missing else "")

# 6. /api/os 数据域齐全
d = json.load(op.open("http://127.0.0.1:8091/api/os", timeout=10))
for k in ("collect", "agents", "paper", "qd", "strategies", "warehouse"):
    chk("api/os 域 " + k, k in d)

print("\n== %s ==" % ("全部通过" if not fails else "失败 %d 项: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
