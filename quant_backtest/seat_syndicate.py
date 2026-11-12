# -*- coding: utf-8 -*-
"""席位团伙网络（P3 第一块：教材"查团伙/查回路"的 ts_top_inst 实现）。

教材出处：《公安大数据挖掘与分析教程》理论篇 2.4 可视化七法——
  查找团伙："分析是否存在可能的群组，构成相对比较紧密的团伙；
            并计算其团伙强度"；
  社会网络分析（SNA）："计算中心地位、重要度、活跃度，
            发现权力的来源和分布"。
量化映射：同一只票同日多席位上榜 = 一次"共同行动"；近 N 日反复
共同行动的席位对 = 团伙边。无 FastRP/Leiden 也能算——共现矩阵的
连通分量即团伙（教材"网络拼图"的最小连接通路）。

产出（reports/seat_syndicate.json，举证型，无裁决）：
  edges     反复共现席位对（>=min_co 去重票日数）+ 共现票清单
  gangs     连通分量（>=3 席位），按内部边密度排序
  hubs      SNA 度中心性 top（"权力节点"：资金枢纽席位）
  audit     三字段（JUDGMENT_AUDIT 台账纪律）

审计声明：
  assumptions: ①同票同榜=共同行动（实为交易所披露规则筛选后的可见部分）；
               ②机构专用/沪深股通是通道名非单一主体，聚簇天然偏大；
  falsifier:  高换手热门票（连板妖股）人人有份——共现是票的属性非席位
              协同；对照组：随机席位对在同票池的共现率若与观测无差即证伪；
  cost_if_wrong: 把热门票的流动性共现误读为操纵团伙——输出仅供线索，
              不得作为"对倒/操纵"认定依据（认定权在监管与人）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "reports", "seat_syndicate.json")

CHANNEL_TAGS = ("机构专用", "股通专用")   # 通道名席位：参与网络但标注

# 非营业部主体（ts_top_inst 的"其他"披露类目，判例：首跑密度最高"团伙"
# 7 席位全是这批——中小投资者/自然人/机构投资者等聚合类目彼此高共现
# 是披露口径，不是行为协同）。团伙判定整体剔除，单独保留计数。
NON_BROKER = ("投资者", "自然人", "中小投资者", "其他", "^机构$",
              "沪股通", "深股通")


def _is_non_broker(s: str) -> bool:
    import re
    return any(re.search(p, s) for p in NON_BROKER)


def build(days: int = 30, min_co: int = 3, top: int = 50):
    import tdx_read as tdxr

    start = (dt.date.today() - dt.timedelta(days=days * 3)).strftime("%Y%m%d")
    ti = tdxr.read_range(
        "ts_top_inst", start=start,
        columns=["ts_code", "trade_date", "exalter", "net_buy"])
    ti = ti.dropna(subset=["exalter"]).copy()
    ti["exalter"] = ti["exalter"].astype(str).str.strip()
    ti["d"] = ti["trade_date"].astype(str).str[:8]
    win = sorted(ti["d"].unique())[-days:]
    w = ti[ti["d"].isin(set(win))]
    basis = win[-1]

    # 共现：席位对 -> {票日}
    co = defaultdict(set)
    for (_c, _d), g in w.groupby(["ts_code", "d"]):
        ss = sorted(set(g["exalter"]))
        for i in range(len(ss)):
            for j in range(i + 1, len(ss)):
                co[(ss[i], ss[j])].add((_c, _d))
    edges = [{"a": a, "b": b,
              "n": len(ts),
              "stocks": sorted({c for c, _ in ts})[:6]}
             for (a, b), ts in co.items() if len(ts) >= min_co]
    edges.sort(key=lambda e: -e["n"])

    # ── 枢纽校正（2026-11-13 首跑 falsifier 实证后加）──────────────
    # 首跑：447 席位连成一团——度>=100 的 10 个超级枢纽（东财拉萨系/
    # 国泰海通总部/机构股通通道）把全网焊通。这是热门票流动性的天然
    # 连接，不是席位协同（台账预设 falsifier 命中）。
    # 校正：团伙判定剔除枢纽席位（枢纽单独进 hubs 榜），只在非枢纽
    # 席位间找连通块——剩下的连接才可能是真协同。
    deg = defaultdict(int)
    for e in edges:
        deg[e["a"]] += 1
        deg[e["b"]] += 1
    HUB_DEG = 60          # 度中心性超此值=流动性枢纽（30日窗口经验值）
    hubs_set = {s for s, n in deg.items() if n >= HUB_DEG}
    non_broker = {s for s in deg if _is_non_broker(s)}

    adj = defaultdict(set)
    for e in edges:
        if e["a"] in hubs_set or e["b"] in hubs_set:
            continue
        if e["a"] in non_broker or e["b"] in non_broker:
            continue
        adj[e["a"]].add(e["b"])
        adj[e["b"]].add(e["a"])
    seen, gangs = set(), []
    for s in adj:
        if s in seen:
            continue
        comp, stack = set(), [s]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            stack.extend(adj[x] - comp)
        seen |= comp
        inner = sum(1 for e in edges if e["a"] in comp and e["b"] in comp)
        gangs.append({"seats": sorted(comp), "n_seats": len(comp),
                      "inner_edges": inner,
                      "density": round(inner / (len(comp) * (len(comp) - 1) / 2), 3)
                      if len(comp) > 1 else 1.0})
    gangs.sort(key=lambda g: (-g["density"], -g["n_seats"]))

    # SNA 度中心性（权力节点）
    hubs = sorted(deg.items(), key=lambda x: -x[1])[:top]

    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basis": basis, "window_days": days, "min_co": min_co,
        "hub_deg_cutoff": HUB_DEG, "n_hub_seats": len(hubs_set),
        "n_non_broker_excluded": len(non_broker),
        "n_edges": len(edges), "n_gangs": len(gangs),
        "edges_top": edges[:top],
        "gangs": [g for g in gangs if g["n_seats"] >= 3][:top],
        "hubs": [{"seat": s, "degree": n,
                  "channel": any(t in s for t in CHANNEL_TAGS)}
                 for s, n in hubs],
        "audit": {
            "assumptions": "同票同榜=共同行动；机构专用/股通为通道名"
                           "（非单一主体，聚簇天然偏大）；度>=60 的超级"
                           "枢纽（首跑实证 10 个）是流动性中枢非协同主体，"
                           "团伙判定已剔除",
            "falsifier": "热门票流动性共现对照：随机席位对同共现池共现率"
                         "与观测无差 → 共现是票的属性非席位协同",
            "cost_if_wrong": "流动性共现被误读为操纵团伙——仅线索，"
                             "不作为对倒/操纵认定依据",
        },
    }


def main():
    ap = argparse.ArgumentParser(description="席位团伙网络（P3·查团伙/查回路）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-co", type=int, default=3)
    ap.add_argument("--top", type=int, default=50)
    a = ap.parse_args()
    doc = build(a.days, a.min_co, a.top)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("seat_syndicate: 边 %d 团伙 %d（basis=%s %d日 min_co=%d）-> %s"
          % (doc["n_edges"], doc["n_gangs"], doc["basis"],
             a.days, a.min_co, OUT))
    print("最大团伙 %d 席位；hub 榜首 %s（度 %d）"
          % (max((g["n_seats"] for g in doc["gangs"]), default=0),
             doc["hubs"][0]["seat"][:24] if doc["hubs"] else "-",
             doc["hubs"][0]["degree"] if doc["hubs"] else 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
