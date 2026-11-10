# -*- coding: utf-8 -*-
"""P65 env 分层取证：被闸门砍掉的信号到底赚不赚。"""
import json

SRC = r"C:/项目/老田的AI助手/quant_backtest/reports/_p65_env_split.json"
d = json.load(open(SRC, encoding="utf-8"))
es = d["env_split"]


def fmt(h, key="mean"):
    v = h.get(key)
    return "—" if v is None else "%.4f%%" % (v * 100)


print("=" * 78)
print("A. 按 breadth 分档（反事实：不含 env 闸门的信号集）")
print("=" * 78)
print("%-10s %8s %8s %10s %10s %10s %9s" % ("档位", "笔数", "放行", "T+5", "T+10", "T+20", "胜率"))
tot5 = 0.0
for k, v in es["breadth"].items():
    h = v["holds"]
    n5 = h["5"]["n_trades"]
    m5 = h["5"]["mean"] or 0
    tot5 += n5 * m5
    print("%-10s %8d %8d %10s %10s %10s %9s" % (
        k, v["n_buy"], v["n_safe"], fmt(h["5"]), fmt(h["10"]), fmt(h["20"]),
        "—" if h["5"]["win_rate"] is None else "%.1f%%" % (h["5"]["win_rate"] * 100)))

print()
print("=" * 78)
print("B. 按 med20 分档")
print("=" * 78)
print("%-11s %8s %8s %10s %10s %10s %9s" % ("档位", "笔数", "放行", "T+5", "T+10", "T+20", "胜率"))
for k, v in es["med20"].items():
    h = v["holds"]
    print("%-11s %8d %8d %10s %10s %10s %9s" % (
        k, v["n_buy"], v["n_safe"], fmt(h["5"]), fmt(h["10"]), fmt(h["20"]),
        "—" if h["5"]["win_rate"] is None else "%.1f%%" % (h["5"]["win_rate"] * 100)))

print()
print("=" * 78)
print("C. 现行闸门实际砍掉了什么（同一信号集拆分）")
print("=" * 78)
ge = es["gate_effect"]
for tag in ("passed", "blocked"):
    v = ge[tag]
    h = v["holds"]
    print("%-8s 笔数 %7d   T+5 %10s  T+10 %10s  T+20 %10s  胜率 %s" % (
        "放行" if tag == "passed" else "砍掉", v["n_buy"],
        fmt(h["5"]), fmt(h["10"]), fmt(h["20"]),
        "—" if h["5"]["win_rate"] is None else "%.1f%%" % (h["5"]["win_rate"] * 100)))

p, b = ge["passed"]["holds"]["5"], ge["blocked"]["holds"]["5"]
if p["mean"] is not None and b["mean"] is not None:
    print()
    print("闸门价值（T+5，逐笔等额口径）: %.4f%% - (%.4f%%) = %+.4f pct"
          % (p["mean"] * 100, b["mean"] * 100, (p["mean"] - b["mean"]) * 100))
    print("砍掉信号的总盈亏当量: %d 笔 × %.4f%% = %.2f（资金当量为负则空仓是对的）"
          % (ge["blocked"]["n_buy"], b["mean"] * 100,
             ge["blocked"]["n_buy"] * b["mean"] * 100))
