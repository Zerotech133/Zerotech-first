# -*- coding: utf-8 -*-
"""事件缓存版本对照：同一窗口、两次扫描（不同数据 vintage）差在哪。

2026-11-10：data/ashare 全量刷新（11-09 16:05），其中 210 只丢了 pct_chg。
旧缓存（2026-10-13 扫）与新缓存（今日扫）比对，回答两件事：
  1) 事件数量/类型分布变了多少 → 数据刷新对信号面的影响量级
  2) 同一 (股票,类型,日) 上 zg（中枢上沿）漂移多少 → 直接影响「距上沿≤3%」这条门槛

用法：python bt10m_scan_diff.py [old_fp] [new_fp]
"""
import json
import os
import statistics
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
SIG = os.path.join(BASE, "data", "sig_events")


def load(fp):
    j = json.load(open(fp, encoding="utf-8"))
    return j.get("events", []), j.get("meta", {})


def main(argv):
    old = argv[1] if len(argv) > 1 else os.path.join(
        SIG, "sig_events_market_v3_20251013_20261012.json.bak-20261013scan")
    new = argv[2] if len(argv) > 2 else os.path.join(
        SIG, "sig_events_market_v3_20251013_20261012.json")
    eo, mo = load(old)
    en, mn = load(new)
    print("旧 %s\n  事件 %d  %s" % (os.path.basename(old), len(eo), mo.get("generated_at")))
    print("新 %s\n  事件 %d  %s" % (os.path.basename(new), len(en), mn.get("generated_at")))

    co, cn = Counter(e["type"] for e in eo), Counter(e["type"] for e in en)
    print("\n类型分布：")
    for tp in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
        a, b = co.get(tp, 0), cn.get(tp, 0)
        d = (b / a - 1) * 100 if a else 0.0
        print("  %s  旧 %5d  新 %5d  %+6.1f%%" % (tp, a, b, d))

    ko = {(e["day"], e["stock"], e["type"]) for e in eo}
    kn = {(e["day"], e["stock"], e["type"]) for e in en}
    inter = ko & kn
    print("\n事件级：旧独有 %d  新独有 %d  交集 %d（保留率 %.1f%%）"
          % (len(ko - kn), len(kn - ko), len(inter), len(inter) / len(ko) * 100 if ko else 0))

    om = {(e["day"], e["stock"], e["type"]): e for e in eo}
    nm = {(e["day"], e["stock"], e["type"]): e for e in en}
    drift, miss = [], 0
    for k in inter:
        a, b = om[k].get("zg"), nm[k].get("zg")
        if a and b:
            drift.append(abs(b / a - 1) * 100)
        else:
            miss += 1
    if drift:
        drift.sort()
        n = len(drift)
        print("\n同一事件 zg 漂移（%%）：中位 %.3f  均值 %.3f  P90 %.3f  最大 %.2f  样本 %d（无 zg 对照 %d）"
              % (drift[n // 2], statistics.mean(drift), drift[int(n * 0.9)],
                 drift[-1], n, miss))
        over3 = sum(1 for x in drift if x > 3.0)
        print("  漂移 >3%% 的事件 %d 条（%.2f%%）——这些会跨越「距上沿≤3%%」门槛"
              % (over3, over3 / n * 100))

    # 3买 单独看：交易口径只用这一类
    o3 = [e for e in eo if e["type"] == "3买"]
    n3 = [e for e in en if e["type"] == "3买"]
    do = Counter(e["day"] for e in o3)
    dn = Counter(e["day"] for e in n3)
    days = sorted(set(do) | set(dn))
    print("\n3买 日均：旧 %.2f 只  新 %.2f 只  覆盖 %d 日"
          % (sum(do.values()) / len(do) if do else 0,
             sum(dn.values()) / len(dn) if dn else 0, len(days)))


if __name__ == "__main__":
    main(sys.argv)
