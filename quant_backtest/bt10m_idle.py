import json, sys, collections, statistics
sys.stdout.reconfigure(encoding="utf-8")

d = json.load(open("data/bt2/bt2_ext03_m20_tr.json", encoding="utf-8"))
days = d["days"]; ks = sorted(days)
eq = {e["day"]: e for e in d["equity_curve"]}

buckets = collections.Counter()
big_gap = []
idle_days = []
for k in ks:
    r = days[k]; inv = eq[k]["inv_pct"]; cap = r["cap"] * 100
    pool = len(r.get("pool_today") or [])
    sk = sum(1 for a in (r.get("actions") or []) if a.get("type") == "skip")
    if r["n_pos"] == 0:
        idle_days.append(k)
        buckets["A 无候选信号" if pool == 0 else "B 有候选但全被拒" if sk else "C 其它空仓"] += 1
    else:
        gap = cap - inv
        if gap >= 30: buckets["D 持仓中但低于上限 30pp+"] += 1
        elif gap >= 10: buckets["E 低于上限 10-30pp"] += 1
        else: buckets["F 基本用满上限"] += 1

print("=== 242 天按仓位状态分类 ===")
for k in sorted(buckets): print("  %-26s %3d 天  %3.0f%%" % (k, buckets[k], 100 * buckets[k] / 242))
print()
print("=== 26 个全空仓日 ===")
print("  ", " ".join(idle_days))
run = 1; runs = []
for i in range(1, len(idle_days)):
    if ks.index(idle_days[i]) - ks.index(idle_days[i-1]) == 1: run += 1
    else: runs.append(run); run = 1
runs.append(run)
print("  最长连续空仓 %d 天" % max(runs))
print()
# 缺口归因：把每个交易日的 cap-inv 缺口，按当时的主因归类
print("=== 缺口归因（cap − 实际仓位，共 242 天累计）===")
tot = 0
by = collections.Counter()
for k in ks:
    r = days[k]; gap = r["cap"] * 100 - eq[k]["inv_pct"]; tot += gap
    pool = len(r.get("pool_today") or [])
    if r["regime"] == "low": by["① regime=low 把上限压到 30%"] += gap
    elif pool <= 2: by["② 当日候选 ≤2 只（信号稀缺）"] += gap
    elif pool <= 6: by["③ 候选 3-6 只（填不满 10 槽）"] += gap
    else: by["④ 候选充足但被拒单（追高/涨跌停/槽位）"] += gap
print("  累计缺口 %.0f 个仓位百分点（日均 %.1fpp）" % (tot, tot / 242))
for k, v in by.most_common():
    print("    %-34s %7.0f pp  占 %4.0f%%" % (k, v, 100 * v / tot))
print()
print("=== regime 分层 ===")
for r0 in ("high", "mid", "low"):
    idx = [k for k in ks if days[k]["regime"] == r0]
    inv = [eq[k]["inv_pct"] for k in idx]
    gaps = [days[k]["cap"] * 100 - eq[k]["inv_pct"] for k in idx]
    print("  %-4s n=%3d  cap %3.0f%%  实际均值 %5.1f%%  缺口 %5.1fpp  空仓 %2d 天  候选均值 %4.1f"
          % (r0, len(idx), days[idx[0]]["cap"] * 100, statistics.mean(inv),
             statistics.mean(gaps), sum(1 for x in inv if x == 0),
             statistics.mean([len(days[k].get("pool_today") or []) for k in idx])))
