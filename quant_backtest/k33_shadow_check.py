# -*- coding: utf-8 -*-
"""k33_shadow_check.py —— K33 警报池影子战绩监测（checklist B 流程）

依据：nine_dim_k33_checklist.md §B（每周例行）。
- B1 样本量核对：入池行数 / pool_type 分布 / thinned 等效 n
- B2 前置门槛：thinned n < 30 → 仅记录不评判（本节通过）
- B3/B4（n≥30 才激活）：r20 × 超额 双判据
数据源：data/nine_dim_history.jsonl + data/ashare 本地行情（复用 nine_dim._bars）。
用法：python k33_shadow_check.py
"""
import json
import os

import nine_dim as nd

HIST = nd.HIST
BENCH = "000300.SH"
N_TH = 30          # B2 前置门槛（thinned）
FWD = 20           # 成熟窗口（交易日）


def main():
    if not os.path.exists(HIST):
        print("B1: history 不存在——警报池尚无入池记录")
        print("B2: n=0 < %d → 仅记录不评判，B 节通过（影子观察启动状态正常）" % N_TH)
        return 0
    with open(HIST, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]

    from collections import Counter
    pt = Counter(r.get("pool_type") or "buy_legacy" for r in rows)
    print("B1: history 总 %d 行；pool_type 分布 %s"
          % (len(rows), dict(pt)))

    alerts = [r for r in rows if r.get("pool_type") == "alert"]
    if not alerts:
        print("B1: alert 行 0（K33 定稿 %s 起累计）" % "2026-10-02")
        print("B2: n=0 < %d → 仅记录不评判，B 节通过" % N_TH)
        return 0

    # thinned：同票相邻入池在交易日历上 <FWD 视为同一观察
    bars_cache = {}
    thinned = []
    last_pos = {}
    for r in sorted(alerts, key=lambda x: x["dt"]):
        c = r["code"]
        if c not in bars_cache:
            bars_cache[c] = nd._bars(c)
        idx = {b["dt"]: i for i, b in enumerate(bars_cache[c])}
        p = idx.get(r["dt"])
        if p is None:
            continue
        if p - last_pos.get(c, -10 ** 9) >= FWD:
            thinned.append(r)
            last_pos[c] = p
    n = len(thinned)
    print("B1: alert 原始 %d 行 → thinned 等效 n=%d" % (len(alerts), n))

    if n < N_TH:
        print("B2: n=%d < %d → 仅记录不评判，B 节通过（低频累积属预期，"
              "勿因入池少调整 ALERT_TH）" % (n, N_TH))
        return 0

    # B3/B4：成熟样本 r20 × 超额
    bench_bars = nd._bars(BENCH)
    bidx = {b["dt"]: i for i, b in enumerate(bench_bars)}
    r20s, excs = [], []
    for r in thinned:
        c = r["code"]
        bars = bars_cache[c]
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        ei = idx.get(r["dt"])
        if ei is None or ei + FWD >= len(bars) or ei + FWD >= len(bench_bars):
            continue
        px = r.get("entry_px") or bars[ei]["c"]
        r20 = bars[ei + FWD]["c"] / px - 1
        bi = bidx.get(r["dt"])
        b20 = (bench_bars[bi + FWD]["c"] / bench_bars[bi]["c"] - 1
               if bi is not None else None)
        r20s.append(r20)
        if b20 is not None:
            excs.append(r20 - b20)
    m = len(r20s)
    avg = sum(r20s) / m if m else 0.0
    exc = sum(excs) / len(excs) if excs else 0.0
    win = sum(1 for x in r20s if x > 0) * 100.0 / m if m else 0.0
    print("B3/B4: 成熟 %d/%d，r20 均值 %+.1f%% 胜率 %.0f%% 超额均值 %+.1f%%"
          % (m, n, avg * 100, win, exc * 100))
    if m >= N_TH:
        if avg < 0 and exc < 0:
            print("B3: 判据通过——预期负收益=警报有效，继续观察")
            return 0
        if avg > 0 and exc > 0:
            print("B4: 失效！r20 与超额双正→触发复评，按 checklist A 全链重跑")
            return 2
        print("B3/B4: 混合信号（均值与超额不同向）→ 记录并人工分析，勿自动改规则")
        return 1
    print("（成熟样本 %d < %d，判据暂不激活）" % (m, N_TH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
