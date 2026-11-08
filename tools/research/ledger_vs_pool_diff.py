# -*- coding: utf-8 -*-
"""诊断 v8：最终账目 —— 差异的完整加总。只读。"""
import json, os, glob
from collections import Counter, defaultdict

ROOT = r"C:\项目\老田的AI助手"
DATA = os.path.join(ROOT, "quant_backtest", "data")
PAPER = os.path.join(DATA, "chanlun_paper.json")
POOL = os.path.join(DATA, "chanlun_pool_ext_v2.jsonl")
ARCH = os.path.join(DATA, "chanlun_paper_archive")
BUY = ("1买", "2买")
SINCE = "20231009"


def n8(s):
    return str(s or "").replace("-", "")[:8]


# 台账全量 = paper.json + archive
d = json.load(open(PAPER, encoding="utf-8"))
led, seen = [], set()


def add(x, src):
    if x.get("type") not in BUY:
        return
    ed = n8(x.get("entry_date") or x.get("detect_date"))
    if not ed or not x.get("entry_px"):
        return
    k = (ed, x.get("code"), x.get("type"))
    if k in seen:
        return
    seen.add(k)
    led.append({"code": x["code"], "type": x["type"], "ed": ed, "src": src,
                "ret": x.get("ret"), "status": "open" if src == "open" else "closed"})


for tag in ("closed", "open"):
    for x in d.get(tag) or []:
        add(x, tag)
n_bad_arch = n_skip_arch = 0
for p in sorted(glob.glob(os.path.join(ARCH, "closed_CN_*.jsonl"))):
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                x = json.loads(line)
            except ValueError:
                n_bad_arch += 1
                continue
            # 归档目录 2026-11-08 起由 chanlun_scan._archive_closed 独占（台账实际入场，
            # paper 风格带 sig_dt）。回填结算集已搬到 data/chanlun_paper_backfill/。
            # 过滤保留作防御：历史文件曾被混写，若这里命中说明又混了。
            if "dt" in x or x.get("source") == "backfill" or "sig_dt" not in x:
                n_skip_arch += 1
                continue
            add(x, "archive")

pool = [json.loads(l) for l in open(POOL, encoding="utf-8") if l.strip()]
pb = [r for r in pool if r.get("type") in BUY]

print("=" * 78)
print("A. 三个基数的定义")
print("=" * 78)
print("池 1买/2买 全部                 : %d" % len(pb))
print("  └ closed & since %s        : %d   ← 15,347" % (SINCE, len(
    [r for r in pb if r.get("status") == "closed" and n8(r.get("entry_date")) >= SINCE])))
print("台账 paper.json 去重            : 1678   ← 1,679 的来源（受 CLOSED_CAP=5000 截断）")
print("台账 全量（paper + archive）    : %d" % len(led))

print()
print("=" * 78)
print("B. 池的分年 vs 台账的分年")
print("=" * 78)
py = Counter(n8(r.get("entry_date"))[:4] for r in pb)
ly = Counter(r["ed"][:4] for r in led)
print("%-6s %10s %10s" % ("年", "池", "台账全量"))
for y in sorted(set(py) | set(ly)):
    print("%-6s %10d %10d" % (y, py.get(y, 0), ly.get(y, 0)))
print("\n→ 台账在 2023/2024/2025 的笔数: %d" % sum(ly.get(y, 0) for y in ("2023", "2024", "2025")))

print()
print("=" * 78)
print("C. 差异加总（同 2026 年，按月）")
print("=" * 78)
pm = Counter(n8(r.get("entry_date"))[:6] for r in pb if n8(r.get("entry_date"))[:4] == "2026")
lm = Counter(r["ed"][:6] for r in led if r["ed"][:4] == "2026")
print("%-8s %8s %8s %10s" % ("月", "池", "台账", "差(台账-池)"))
gap15 = gap611 = 0
for m in sorted(set(pm) | set(lm)):
    if not m.startswith("2026"):
        continue
    a, b = pm.get(m, 0), lm.get(m, 0)
    print("%-8s %8d %8d %10d" % (m, a, b, b - a))
    if m <= "202605":
        gap15 += b - a
    else:
        gap611 += b - a
p15 = sum(v for k, v in pm.items() if k <= "202605")
l15 = sum(v for k, v in lm.items() if k <= "202605")
p611 = sum(v for k, v in pm.items() if k >= "202606")
l611 = sum(v for k, v in lm.items() if k >= "202606")
print("\n1-5 月 : 池 %d / 台账 %d  → 差 %+d  (台账覆盖 %.1f%%)" % (
    p15, l15, l15 - p15, 100.0 * l15 / p15 if p15 else 0))
print("6-11 月: 池 %d / 台账 %d  → 差 %+d  (台账覆盖 %.1f%%)" % (
    p611, l611, l611 - p611, 100.0 * l611 / p611 if p611 else 0))
print("2026 合计: 池 %d / 台账 %d → 差 %+d" % (p15 + p611, l15 + l611, (l15 + l611) - (p15 + p611)))

print()
print("=" * 78)
print("D. 三元组交集（code+type+entry_date 全同）")
print("=" * 78)
pool_cte = {(r["code"], r["type"], n8(r.get("entry_date"))) for r in pb}
inter = [r for r in led if (r["code"], r["type"], r["ed"]) in pool_cte]
print("台账 %d 笔中，池有同 (code,type,entry_date): %d (%.1f%%)" % (
    len(led), len(inter), 100.0 * len(inter) / len(led)))
print("池   %d 笔中，台账同三元组      : %d (%.1f%%)" % (
    len(pb), len(inter), 100.0 * len(inter) / len(pb)))

print()
print("=" * 78)
print("E. 收益：交集 vs 差集（已结算）")
print("=" * 78)
hit = [r for r in led if (r["code"], r["type"], r["ed"]) in pool_cte and r["ret"] is not None and r["status"] == "closed"]
ms = [r for r in led if (r["code"], r["type"], r["ed"]) not in pool_cte and r["ret"] is not None and r["status"] == "closed"]
for name, arr in (("交集", hit), ("台账独有", ms)):
    a = [r["ret"] for r in arr]
    if a:
        print("%-8s n=%4d 均值=%+7.3f%% 胜率=%5.1f%%" % (
            name, len(a), 100 * sum(a) / len(a), 100.0 * sum(1 for x in a if x > 0) / len(a)))

print()
print("=" * 78)
print("F. 若把窗口统一到 202606-202611，交集规模多少")
print("=" * 78)
led6 = [r for r in led if r["ed"] >= "20260601"]
inter6 = [r for r in led6 if (r["code"], r["type"], r["ed"]) in pool_cte]
pool6 = [r for r in pb if n8(r.get("entry_date")) >= "20260601"]
print("台账 202606+ : %d，其中池也有: %d (%.1f%%)" % (
    len(led6), len(inter6), 100.0 * len(inter6) / len(led6) if led6 else 0))
print("池   202606+ : %d，其中台账也有: %d (%.1f%%)" % (
    len(pool6), len(inter6), 100.0 * len(inter6) / len(pool6) if pool6 else 0))
