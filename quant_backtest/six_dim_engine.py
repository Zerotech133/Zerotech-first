# -*- coding: utf-8 -*-
"""六维研判引擎（Phase 1，教材账单六维的领域无关实现）。

出处对照（《公安大数据挖掘与分析教程》实践篇第五章"账单一维和二维分析"）：
  DIM 1  single_large   单笔大额（5.3.2-1）  单笔净额超阈值 → 大资金动作
  DIM 2  cumulative     累积大额（5.3.2-2）  主端-对端对的总额排序
  DIM 3  frequent       频繁转账（5.3.2-3）  主端-对端对的次数排序（对倒/做市特征）
  DIM 4  odd_time       非正常时间（5.3.2-4）龙虎榜无时间戳 → 上榜理由代理
                          （涨幅类=情绪追高，跌幅类=出逃，换手类=对倒嫌疑）
  DIM 5  net_flow       资金净和（5.3.3-1）  收入-支出（席位净买入，SUMIFS 教学版）
  DIM 6  rhythm         交易规律（5.3.4）    星期分布/连续上榜节奏

设计纪律（JUDGMENT_AUDIT.md）：
  · 每维带 assumptions/falsifier/cost_if_wrong 三字段，与台账一一对应；
  · 引擎只做"举证"：输出带证据的维度向量，不输出"这是坏人"的裁决；
  · 领域无关：列名映射通过 SCHEMA 适配，第一个应用=龙虎榜席位，
    公安侧（话单/账单）换适配器即用。

用法：
  python six_dim_engine.py                 # 跑龙虎榜席位画像（近30交易日）
  python six_dim_engine.py --days 60 --top 100
输出：reports/six_dim_seats.json（原子写）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "reports", "six_dim_seats.json")

# ── 维度契约：教材六维 + 审计三字段 ────────────────────────────────────
DIMS = {
    "single_large": {
        "cn": "单笔大额",
        "src": "教材5.3.2-1",
        "assumptions": "单笔净额是当日真实主动资金；单位万元→亿元折算正确",
        "falsifier": "同席位同票多笔可拆单（算法拆单使单笔口径低估真实规模）",
        "cost_if_wrong": "大资金席位被漏标，画像低估其影响力",
    },
    "cumulative": {
        "cn": "累积大额",
        "src": "教材5.3.2-2",
        "assumptions": "席位-股票对的金额可跨日加总（价格变动不影响'规模'语义）",
        "falsifier": "长期反复上榜的对（做市型）累积额虚高，非'单次决心'而是'日常行为'",
        "cost_if_wrong": "把常态化席位误判为重仓进攻",
    },
    "frequent": {
        "cn": "频繁交易",
        "src": "教材5.3.2-3",
        "assumptions": "同席位-股票对反复上榜≈持续运作（对倒/护盘/波段）",
        "falsifier": "高换手票天然多席位反复上榜，频次是票的属性不是席位的",
        "cost_if_wrong": "对倒嫌疑标签误伤波段席位",
    },
    "odd_time": {
        "cn": "非常规上榜",
        "src": "教材5.3.2-4（时间戳缺失的代理维）",
        "assumptions": "龙虎榜无成交时间戳，用上榜理由代理：跌幅类上榜≈出逃/砸盘侧，"
                       "换手类≈对倒嫌疑，涨幅类≈情绪追高",
        "falsifier": "上榜理由是交易所规则触发，非席位意图；同一席位买卖双侧共用一条理由",
        "cost_if_wrong": "风格标签系统性偏差（这是六维中最弱的一维，仅作参考权重）",
    },
    "net_flow": {
        "cn": "资金净和",
        "src": "教材5.3.3-1（SUMIFS 教学版）",
        "assumptions": "net_buy=buy-sell 为正即净流入方；万元单位",
        "falsifier": "龙虎榜只披露前5席位，未上榜交易不计入——净和是'可见部分'的净和",
        "cost_if_wrong": "低估隐蔽建仓席位（拆单到多席位规避披露的完全测不到）",
    },
    "rhythm": {
        "cn": "节奏规律",
        "src": "教材5.3.4",
        "assumptions": "星期几分布偏离均匀=行为节奏（游资生物钟/周末消息面习惯）",
        "falsifier": "上榜机会本身有星期效应（节假日调休、发审节奏），非席位选择",
        "cost_if_wrong": "把市场日历效应当成席位规律",
    },
}


def _reason_tag(reason: str) -> str:
    """上榜理由 → 风格代理标签（DIM 4 的领域适配）。"""
    if not reason:
        return "其他"
    r = str(reason)
    if "跌幅" in r:
        return "出逃侧"
    if "换手" in r or "振幅" in r:
        return "对倒嫌疑侧"
    if "涨幅" in r:
        return "情绪追高侧"
    return "其他"


def build_seat_profile(days: int = 30, top: int = 100):
    """近 N 个有数据交易日 → 每席位六维画像。"""
    import pandas as pd
    import tdx_read as tdxr

    start = (dt.date.today() - dt.timedelta(days=days * 3)).strftime("%Y%m%d")
    ti = tdxr.read_range(
        "ts_top_inst", start=start,
        columns=["ts_code", "trade_date", "exalter", "side", "buy", "sell", "net_buy"])
    if ti is None or ti.empty:
        raise SystemExit("ts_top_inst 无数据")
    ti = ti.dropna(subset=["exalter"]).copy()
    ti["exalter"] = ti["exalter"].astype(str).str.strip()
    ti["d"] = ti["trade_date"].astype(str).str[:8]
    alld = sorted(ti["d"].unique())
    win = set(alld[-days:])
    basis = alld[-1]
    ti = ti[ti["d"].isin(win)]

    tl = tdxr.read_range("ts_top_list", start=start,
                         columns=["ts_code", "trade_date", "reason"])
    rmap = {}
    if tl is not None and not tl.empty:
        tl = tl.dropna(subset=["reason"])
        tl["d"] = tl["trade_date"].astype(str).str[:8]
        rmap = {(r.ts_code, r.d): _reason_tag(r.reason)
                for r in tl.itertuples(index=False)}

    # 星期映射（节奏维）
    def _wday(d8):
        try:
            return dt.datetime.strptime(d8, "%Y%m%d").weekday()
        except Exception:
            return -1

    rows = []
    for seat, g in ti.groupby("exalter"):
        buy = float(g["buy"].fillna(0).sum()) / 1e8          # 元→亿（实测 buy=1238005.09 元，判例：首版误按万元折算虚高 1 万倍）
        sell = float(g["sell"].fillna(0).sum()) / 1e8
        net = float(g["net_buy"].fillna(0).sum()) / 1e8
        # DIM1 单笔大额：该席位单日单票净额 top
        g2 = g.copy()
        g2["ev"] = g2["net_buy"].fillna(0)
        single = g2.groupby(["ts_code", "d"])["ev"].sum()
        single_max = float(single.max()) / 1e8 if len(single) else 0.0
        # DIM2/3 累积与频次：席位-股票对
        nb = g["net_buy"].fillna(0)
        pair_sum = g.assign(_nb=nb).groupby("ts_code")["_nb"].sum() / 1e8
        pair_cnt = g.groupby("ts_code").size()
        top_cum_pair = str(pair_sum.abs().sort_values(ascending=False).index[0]) \
            if len(pair_sum) else ""
        top_freq_pair = str(pair_cnt.sort_values(ascending=False).index[0]) \
            if len(pair_cnt) else ""
        max_cum = float(pair_sum.abs().max()) if len(pair_sum) else 0.0
        max_freq = int(pair_cnt.max()) if len(pair_cnt) else 0
        # DIM4 理由代理
        tags = {}
        for r in g.itertuples(index=False):
            t = rmap.get((r.ts_code, r.d), "其他")
            tags[t] = tags.get(t, 0) + 1
        dom_tag = max(tags, key=tags.get) if tags else "其他"
        # DIM5 净和 = net
        # DIM6 星期分布
        wd = g["d"].map(_wday)
        wd = wd[wd >= 0]
        wk = [0] * 5
        for w, n in wd.value_counts().items():
            if 0 <= int(w) < 5:
                wk[int(w)] = int(n)
        tot = sum(wk) or 1
        rhythm_bias = max(wk) / tot if tot else 0.0     # 峰值日占比（0.2=均匀）
        rows.append({
            "seat": seat,
            "window": days, "basis": basis,
            "n_days": int(g["d"].nunique()),
            "n_stocks": int(g["ts_code"].nunique()),
            # 六维
            "d1_single_max_yi": round(single_max, 2),
            "d2_cum_max_yi": round(max_cum, 2), "d2_top_pair": top_cum_pair,
            "d3_freq_max": max_freq, "d3_top_pair": top_freq_pair,
            "d4_reason_tag": dom_tag, "d4_tag_mix": tags,
            "d5_net_yi": round(net, 2), "d5_buy_yi": round(buy, 2),
            "d5_sell_yi": round(sell, 2),
            "d6_week_hist": wk, "d6_peak_ratio": round(rhythm_bias, 3),
        })
    rows.sort(key=lambda r: -r["n_days"])
    return rows[:top], basis, len(win)


def main():
    ap = argparse.ArgumentParser(description="六维研判引擎·席位画像（Phase 1）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows, basis, n_win = build_seat_profile(a.days, a.top)
    doc = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basis": basis, "n_days_window": n_win, "n_seats": len(rows),
        "engine": "six_dim_engine v0.1（教材账单六维·席位适配）",
        "dim_contracts": DIMS,
        "audit": "维度口径三字段与 JUDGMENT_AUDIT.md 台账对应；"
                 "输出为举证型画像，不含裁决结论",
        "seats": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("six_dim_seats: %d 席位（basis=%s 窗口=%d 交易日）-> %s"
          % (len(rows), basis, n_win, OUT))
    if a.json:
        print(json.dumps(rows[:5], ensure_ascii=False, indent=1)[:1200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
