# -*- coding: utf-8 -*-
"""P66 报告：组合层仓位对比 + 逐月 alpha/beta 拆分 + env 分层。"""
import json

SRC = r"C:/项目/老田的AI助手/quant_backtest/reports/_p66_env_continuous.json"
d = json.load(open(SRC, encoding="utf-8"))


def pct(x, w=9):
    return ("%*.4f%%" % (w, x * 100)) if x is not None else "%*s" % (w, "—")


print("=" * 84)
print("一、组合层（总资金口径，信号集统一 gate_off，只换仓位系数）")
print("=" * 84)
for h in d["portfolio_ab"]["holds"]:
    print("\n持有 %d 日" % h["hold"])
    print("  %-16s %10s %10s %10s %9s %8s" % ("方案", "累计收益", "年化", "最大回撤", "Calmar", "均仓位"))
    for r in h["results"]:
        print("  %-16s %10s %10s %10s %9s %8s" % (
            r.get("label"), pct(r.get("total_ret")), pct(r.get("annual")),
            pct(r.get("max_dd")), r.get("calmar"), r.get("avg_w")))

rows = [x for x in d["gate_ab"]["monthly"]["rows"] if x["cfg"] == "base_w_cur_gate_on"]
rows.sort(key=lambda r: r["month"])

print()
print("=" * 84)
print("二、逐月：策略 vs 全市场基准（T+5），判断亏损是 alpha 还是 beta")
print("=" * 84)
print("%-8s %8s %10s %10s %9s" % ("月份", "笔数", "策略", "基准", "超额"))
sa, sb, se = [], [], []
for r in rows:
    t, b = r.get("t5"), r.get("bench_t5")
    if t is None or b is None:
        print("%-8s %8d %10s %10s %9s" % (r["month"], r["n_buy"], "—", "—", "—"))
        continue
    print("%-8s %8d %10s %10s %9s" % (r["month"], r["n_buy"], pct(t), pct(b), pct(t - b, 8)))
    sa.append(t); sb.append(b); se.append(t - b)

if sa:
    print("-" * 84)
    print("%-8s %8s %10s %10s %9s" % ("月均", "", pct(sum(sa) / len(sa)), pct(sum(sb) / len(sb)),
                                     pct(sum(se) / len(se))))
    print("%-8s %8s %10s %10s %9s" % ("正负", "", "%d正/%d负" % (sum(1 for x in sa if x > 0), sum(1 for x in sa if x <= 0)),
                                     "%d正/%d负" % (sum(1 for x in sb if x > 0), sum(1 for x in sb if x <= 0)),
                                     "%d正/%d负" % (sum(1 for x in se if x > 0), sum(1 for x in se if x <= 0))))

valid = [r for r in rows if r.get("t5") is not None and r.get("bench_t5") is not None]
if valid:
    half = len(valid) // 2
    for tag, seg in (("IS ", valid[:half]), ("OOS", valid[half:])):
        a = sum(r["t5"] for r in seg) / len(seg)
        b = sum(r["bench_t5"] for r in seg) / len(seg)
        print("  %s %s~%s  策略 %s  基准 %s  超额 %s（月 %d）" % (
            tag, seg[0]["month"], seg[-1]["month"], pct(a), pct(b), pct(a - b, 8), len(seg)))

es = d["env_split"]
print()
print("=" * 84)
print("三、env 分层（反事实：不含闸门的信号集）")
print("=" * 84)
print("%-10s %8s %8s %10s %10s %10s" % ("breadth", "笔数", "放行", "T+5", "T+10", "T+20"))
for k, v in es["breadth"].items():
    h = v["holds"]
    print("%-10s %8d %8d %10s %10s %10s" % (
        k, v["n_buy"], v["n_safe"], pct(h["5"]["mean"]), pct(h["10"]["mean"]), pct(h["20"]["mean"])))
print()
print("%-11s %8s %8s %10s %10s %10s" % ("med20", "笔数", "放行", "T+5", "T+10", "T+20"))
for k, v in es["med20"].items():
    h = v["holds"]
    print("%-11s %8d %8d %10s %10s %10s" % (
        k, v["n_buy"], v["n_safe"], pct(h["5"]["mean"]), pct(h["10"]["mean"]), pct(h["20"]["mean"])))

ge = es["gate_effect"]
print()
print("闸门效果（同一信号集拆分，T+5）")
for tag in ("passed", "blocked"):
    v = ge[tag]
    print("  %-4s 笔数 %7d  T+5 %s  T+10 %s" % (
        "放行" if tag == "passed" else "砍掉", v["n_buy"],
        pct(v["holds"]["5"]["mean"]), pct(v["holds"]["10"]["mean"])))
