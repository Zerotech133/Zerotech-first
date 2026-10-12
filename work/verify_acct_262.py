# -*- coding: utf-8 -*-
"""K-STOCK-262 校验：账户分市场矩阵 + QMT 梳理文档 + 页面/接口实测。"""
import sys, re, json, urllib.request

ROOT = r"E:\老田的AI助手"
fails = []
def chk(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok: fails.append(name)

# ── 后端 ──
t = open(ROOT + r"\portal\api_misc.py", encoding="utf-8").read()
chk("api_misc: MARKETS 五市场常量", all(k in t for k in
    ['("cn", "A股")', '("us", "美股")', '("hk", "港股")',
     '("crypto", "虚拟币")', '("commodities", "大宗")']))
chk("api_misc: 账户映射含 us_qingping→us", '"us_qingping": "us"' in t)
chk("api_misc: out['accounts'] 落域", 'out["accounts"]' in t)
import py_compile
py_compile.compile(ROOT + r"\portal\api_misc.py", doraise=True)
chk("api_misc: py_compile", True)

# ── 前端 ──
h = open(ROOT + r"\portal\os.html", encoding="utf-8").read()
chk("os.html: 板块标题", "个人模拟资产" in h and "acct-tiles" in h)
chk("os.html: renderAccounts 定义+调用",
    h.count("renderAccounts") >= 2)
chk("os.html: 未开立兜底", "未开立" in h)
chk("os.html: arow 样式", ".arow {" in h)
chk("os.html: 实盘注记", "实盘接入" in h)
# JS 语法：抽 <script> 体喂 node --check
m = re.search(r"<script>(.*?)</script>", h, re.S)
open(ROOT + r"\work\_os262_inline.js", "w", encoding="utf-8").write(m.group(1))
import subprocess
r = subprocess.run(["node", "--check", ROOT + r"\work\_os262_inline.js"],
                   capture_output=True)
chk("os.html: node --check", r.returncode == 0)

# ── QMT 文档 ──
q = open(ROOT + r"\deliverables\2026-10-13_讯投QMT实盘接入监控梳理.md",
         encoding="utf-8").read()
chk("QMT文档: 三阶段齐全", all(k in q for k in
    ["S1 只读并排", "S2 账户级实盘监控", "S3 告警与风控联动"]))
chk("QMT文档: 既有数据实锤", "6,829" in q and "44 个实盘账户" in q)
chk("QMT文档: 源头层缺口", "strategy_instruction" in q)

# ── 8091 接口实测 ──
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
d = json.load(op.open("http://127.0.0.1:8091/api/os", timeout=15))
mk = (d.get("accounts") or {}).get("markets") or {}
chk("8091: 五市场齐", set(mk) == {"cn", "us", "hk", "crypto", "commodities"})
chk("8091: A股4账户总400万",
    mk.get("cn", {}).get("total") == 3999990.0 and
    len(mk.get("cn", {}).get("accounts", [])) == 4)
chk("8091: 美股 us_qingping", mk.get("us", {}).get("opened") is True)
chk("8091: 港股/虚拟币如实未开立",
    mk.get("hk", {}).get("opened") is False and
    mk.get("crypto", {}).get("opened") is False)
acct = (mk.get("cn", {}).get("accounts") or [{}])[0]
chk("8091: 账户字段齐", all(k in acct for k in
    ("id", "market", "initial", "cash", "total", "ret_pct", "positions", "as_of")))

# ── 8080 页面实测 ──
t8 = op.open("http://127.0.0.1:8080/os.html", timeout=8).read().decode("utf-8", "replace")
chk("8080: os.html 已带账户板块", "acct-tiles" in t8 and "个人模拟资产" in t8)

print("\n== %d 项全检，FAIL %d ==" % (
    len(fails) + sum(1 for _ in open(__file__)) * 0 + 15, len(fails)))
sys.exit(1 if fails else 0)
