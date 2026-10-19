# -*- coding: utf-8 -*-
"""K-STOCK-087 冒烟：/algo 页渲染 + 卡密激活闭环 + 主页统计口径。"""
import io, json, os, sys, types

sys.path.insert(0, r"C:/项目/老田的AI助手/portal")
os.chdir(r"C:/项目/老田的AI助手/portal")
import portal_api as pa

h = object.__new__(pa.Handler) if hasattr(pa, "Handler") else None
if h is None:
    # 找 handler 类名
    for n in dir(pa):
        o = getattr(pa, n)
        if isinstance(o, type) and "BaseHTTPRequestHandler" in [
                c.__name__ for c in o.__mro__[1:-1]] or (
                isinstance(o, type) and hasattr(o, "_html")):
            h = object.__new__(o)
            print("handler =", n)
            break
assert h is not None, "handler class not found"

buf = {}
h._html = lambda body, code=200: buf.setdefault("body", body)

# 1) /algo 页渲染
h._algo_market_page({})
body = buf["body"]
for expect in ("算法商品市场", "量子双刀", "试看结果", "卡密激活",
               "LTSO", "试看模式"):
    assert expect in body, "missing: " + expect
print("1) /algo render OK, len=%d" % len(body))

# 2) 初始状态：全部试看（activated_keys 为空）
assert h._license_state("quant_dual") is False
# 3) 坏卡拒绝
ok, msg = h._algo_activate("LTSO-FAKE-0000")
assert not ok, msg
# 4) 真卡激活 → 解锁 → 复原
import shutil
shutil.copy("licenses.json", "licenses.json.bak")
ok, msg = h._algo_activate("ltso-neiq8-2026a")
assert ok, msg
assert h._license_state("quant_dual") is True
assert h._license_state("jev_sentiment") is True
# "all" 卡语义=全解锁（未知 id 亦放行，属设计内）
assert h._license_state("no_such_algo") is True
# 幂等重复激活
ok2, msg2 = h._algo_activate("LTSO-NEIQ8-2026A")
assert ok2 and "已激活" in msg2
# 还原（测试不留激活痕迹）
bak = json.load(open("licenses.json.bak", encoding="utf-8"))
json.dump(bak, open("licenses.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
os.remove("licenses.json.bak")
assert h._license_state("quant_dual") is False
print("2-4) license activate/close-loop OK")

# 5) /algo?key= 带卡渲染（toast 分支）
buf.clear()
shutil.copy("licenses.json", "licenses.json.bak")
h._algo_market_page({"key": ["LTSO-NEIQ8-2026A"]})
assert "激活成功" in buf["body"]
bak = json.load(open("licenses.json.bak", encoding="utf-8"))
json.dump(bak, open("licenses.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
os.remove("licenses.json.bak")
print("5) toast branch OK")

# 6) 主页 zone 统计口径不炸（只验证 algo_market.json 读取）
mkt = json.load(open("algo_market.json", encoding="utf-8"))
assert len(mkt["algos"]) == 6
print("6) algo_market.json 6 products OK")
print("ALL SMOKE PASS")
