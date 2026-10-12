# -*- coding: utf-8 -*-
"""K-STOCK-261 验证：指挥大屏 v2 全 OS 一图流（七域 + 今日一览六数）。"""
import re, subprocess, sys, urllib.request, json

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails = []
def chk(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not cond:
        fails.append(name)

# 1. /api/os 七域
d = json.load(op.open("http://127.0.0.1:8091/api/os", timeout=15))
for k in ("collect", "agents", "paper", "qd", "strategies", "warehouse", "output", "coding", "todo"):
    chk("api/os 域 " + k, k in d)
chk("coding 今日提交>0", (d.get("coding") or {}).get("commits_today", 0) > 0,
    str(d.get("coding")))
chk("output.picks=765", (d["output"]["picks"]["total"]) == 765)
chk("output.verdicts=766", d["output"]["verdicts"] == 766)
chk("todo.health=5", d["todo"]["health"] == 5)

# 2. 8091 回归
for ep in ("/health", "/api/brief", "/api/home", "/api/overview", "/api/strategies", "/api/paper_status"):
    try:
        r = op.open("http://127.0.0.1:8091" + ep, timeout=45)
        chk("8091 " + ep, r.status == 200)
    except Exception as e:
        chk("8091 " + ep, False, str(e)[:60])

# 3. 8080 os.html v2 内容
t = op.open("http://127.0.0.1:8080/os.html", timeout=8).read().decode("utf-8", "replace")
for k in ["今日一览", "六个数", "情报整理", "今日产出", "AI 晨报", "盘前研判", "影子日报",
          "A0 点评", "AI 复盘", "今日选股池", "卖点复盘判定", "板块强弱报告",
          "AI 研发", "今日 git 提交", "复核与裁决", "模拟仓对账", "待办与提醒",
          "告警收件箱", "商务节点提醒", "在研文稿", "量化交易", "入库与基建",
          "/api/os", "cainiao.js", "--page-w"]:
    chk("os.html " + k, k in t)
chk("旧版残留清零", "Agent 动向" not in t)  # v2 改版后旧分区名不再出现

# 4. JS 语法 + id 死链
m = re.search(r"<script>\n(.*?)</script>", t, re.S)
js = m.group(1)
open(r"E:\老田的AI助手\work\verify_261_inline.js", "w", encoding="utf-8").write(js)
r = subprocess.run([r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe",
                    "--check", r"E:\老田的AI助手\work\verify_261_inline.js"],
                   capture_output=True, text=True)
chk("node --check", r.returncode == 0, (r.stderr or "")[:200])
static_ids = set(re.findall(r'id="([^"]+)"', t))
refs = set(re.findall(r"getElementById\('([^']+)'\)", t)) | set(re.findall(r"set\('([a-z0-9-]+)'", t))
missing = {x for x in refs if x not in static_ids}
chk("id 零死链", not missing, "缺失: " + ",".join(sorted(missing)) if missing else "")

# 5. 首页下钻卡仍在
idx = op.open("http://127.0.0.1:8080/", timeout=8).read().decode("utf-8", "replace")
chk("index 下钻卡", "/os.html" in idx and "拉起大屏" in idx)

print("\n== %s ==" % ("全部通过" if not fails else "失败 %d 项: %s" % (len(fails), fails)))
sys.exit(1 if fails else 0)
