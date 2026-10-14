# -*- coding: utf-8 -*-
"""K-STOCK-285c 验证：①事件缓存(all 还原) vs 277 账本缓存逐日一致 ②缓存命中秒级 ③一买剔除量"""
import json
import os
import sys
import time

BASE = r"E:/老田的AI助手"
QB = os.path.join(BASE, "quant_backtest")
sys.path.insert(0, QB)
import backtest_engine_v2 as eng

d0, d1 = "20251013", "20261012"

# ① 交叉校验
eng._ensure_loaded()
sig_b_all, sig_s_ev, _ = eng.calc_chanlun_signals(d0, d1, buy_filter="all")
j = json.load(open(eng.LEDGER, encoding="utf-8"))
led_b = {}
for day, d in j["days"].items():
    if d0 <= day <= d1:
        led_b[day] = {a["stock"] for a in d.get("actions", []) if a["type"] == "sig_buy"}
mismatch = 0
for day in sorted(set(led_b) | set(sig_b_all)):
    if led_b.get(day, set()) != sig_b_all.get(day, set()):
        mismatch += 1
        if mismatch <= 3:
            print("MISMATCH", day, led_b.get(day) ^ sig_b_all.get(day))
print("① 事件缓存(all) vs 277账本: 不一致日数 =", mismatch, "/", len(led_b), "=>",
      "一致 PASS" if mismatch == 0 else "FAIL")

# ② 事件分布
events = json.load(open(eng.SIG_EVENTS_CACHE % (d0, d1), encoding="utf-8"))["events"]
from collections import Counter
c = Counter(e["type"] for e in events)
print("② seen-ever fresh 事件类型分布:", dict(c), "→ 剔除一买 =", c.get("1买", 0), "笔信号")

# ③ 缓存命中耗时
t0 = time.time()
eng.calc_chanlun_signals(d0, d1, buy_filter="23")
print("③ 缓存命中耗时 = %.2fs" % (time.time() - t0))
