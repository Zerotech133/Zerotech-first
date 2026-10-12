# -*- coding: utf-8 -*-
"""K-STOCK-241 缠论池月度信号账本·严格截断重做版（更正 239 前视账本）
对窗口内每个交易日跑 pit_select（截断双pass），产出「当天收盘时真正可见的新确认信号」账本。
"""
import json, os, sys
import pandas as pd
from collections import Counter

BASE = r"E:/老田的AI助手"
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_pit_select import pit_select, load_universe   # noqa: E402

W0, W1 = "20260912", "20261012"
OUT = os.path.join(BASE, "quant_backtest", "chanlun_month_replay_pit_result.json")

# 交易日历：用任一标的的交易日序列
df0 = pd.read_parquet(os.path.join(BASE, "quant_backtest", "data", "ashare", "000001_SZ.parquet"))
days = [d for d in df0.trade_date.tolist() if W0 <= d <= W1]
print("窗口交易日:", len(days), days[0], "->", days[-1], flush=True)

all_rows = []
daily = []
for d in days:
    rows, meta = pit_select(d, "pool", 250)
    for r in rows:
        r["select_day"] = d
    all_rows.extend(rows)
    daily.append({"day": d, "n": len(rows),
                  "types": dict(Counter(r["type"] for r in rows))})
    print(f"  {d}: 新确认 {len(rows)}", flush=True)

tdist = Counter(r["type"] for r in all_rows)
dfw = pd.DataFrame(all_rows)
fwd = {}
if len(dfw):
    for h in (5, 10, 20):
        col = f"d{h}"
        seg = dfw[col].dropna()
        if len(seg):
            fwd[col] = {"n": int(len(seg)), "mean": round(float(seg.mean()), 4),
                        "win_rate": round(float((seg > 0).mean()), 4)}
    dfw["dir"] = dfw["type"].map(lambda t: "buy" if "买" in str(t) else "sell")
    byd = {}
    for dd, seg in dfw.groupby("dir"):
        byd[dd] = {"n": int(len(seg)),
                   **{f"d{h}_mean": round(float(seg[f"d{h}"].dropna().mean()), 4)
                      for h in (5, 10, 20) if seg[f"d{h}"].dropna().size}}
    fwd["by_direction"] = byd

result = {
    "meta": {"window": [W0, W1], "universe": "pool(76)", "method": "严格截断双pass（每交易日 run(≤D)−run(≤D-1)）",
             "correction": "更正 K-STOCK-239：原账本全历史喂入属前视（dt==D 信号 10/10 在 D 收盘不可见），以本账本为准"},
    "daily": daily, "ledger": all_rows, "type_dist": dict(tdist), "fwd_stats": fwd,
}
json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n===== 月度账本·严格截断版 =====")
print("总信号:", len(all_rows), "| 类型:", dict(tdist))
print("前瞻:", json.dumps(fwd, ensure_ascii=False))
print("JSON:", OUT)
