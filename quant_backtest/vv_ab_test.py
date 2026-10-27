# -*- coding: utf-8 -*-
"""vv (缩量共振) A/B 对照回测 —— 只读分析脚本（零依赖改动）。

对照 docs/plans/2026-10-10_选股策略审计修复排期_077e.md 排期①B 实证。
样本：data/chanlun_pool.jsonl（A 股 1d 缠论信号池，含 vv 影子标注）。
口径：检出日收盘买入，后验 5/10/20 日收益，自算于 data/ashare/*.parquet。
另附 chanlun_paper.json 模拟盘可匹配样本的口径作参考。
"""
import json
import os
from collections import Counter

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(BASE, "data", "chanlun_pool.jsonl")
PAPER = os.path.join(BASE, "data", "chanlun_paper.json")
ASHARE = os.path.join(BASE, "data", "ashare")

WINDOWS = [5, 10, 20]


def load_pool():
    sigs, dup = [], 0
    bad = [0]
    seen = set()
    with open(POOL, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                d = json.loads(line)
                key = (d["code"], d["type"], d["dt"])
            except (json.JSONDecodeError, KeyError):
                # 审查 P1-1（077l）：坏行/BOM 容错，计数留痕不中断
                bad[0] += 1
                continue
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            sigs.append(d)
    if bad[0]:
        print(" ! pool 坏行 %d 条跳过" % bad[0])
    return sigs, dup


def fwd_returns(code, detect_date, closes):
    """检出日收盘买入后 N 日收益；detect_date 取 >= 该日首个交易日。"""
    if code not in closes:
        return None
    idx = closes[code].index
    pos = idx.searchsorted(pd.Timestamp(detect_date))
    if pos >= len(idx) or pos >= len(idx) - 1:
        return {n: None for n in WINDOWS}
    base = closes[code].iloc[pos]
    out = {}
    for n in WINDOWS:
        j = pos + n
        out[n] = (closes[code].iloc[j] / base - 1) if j < len(idx) else None
    return out


def group_stats(rows, label):
    n = len(rows)
    out = {"组别": label, "信号数": n}
    for w in WINDOWS:
        vals = [r[w] for r in rows if r.get(w) is not None]
        if vals:
            out[f"{w}日均收益"] = f"{sum(vals)/len(vals)*100:+.2f}%"
            out[f"{w}日胜率"] = f"{sum(v > 0 for v in vals)/len(vals)*100:.1f}%"
        else:
            out[f"{w}日均收益"] = out[f"{w}日胜率"] = "n/a"
    return out


def main():
    sigs, dup = load_pool()
    print(f"池内信号（按 code+type+dt 去重后）: {len(sigs)}，重复行 {dup}")
    vv_dist = Counter(str(s.get("vv")) for s in sigs)
    print("vv 分布:", dict(vv_dist))

    # 加载行情收盘价
    codes = sorted({s["code"] for s in sigs})
    closes = {}
    for c in codes:
        p = os.path.join(ASHARE, c.replace(".", "_") + ".parquet")
        if os.path.exists(p):
            df = pd.read_parquet(p)
            col = "close" if "close" in df.columns else df.columns[0]
            s = df[col]
            s.index = pd.to_datetime(df["trade_date"] if "trade_date" in df.columns else df.index)
            closes[c] = s.sort_index()
    print(f"行情可覆盖 {len(closes)}/{len(codes)} 只标的")

    groups = {True: [], False: [], None: []}
    missing = []
    for s in sigs:
        fr = fwd_returns(s["code"], s["detect_date"] or s["dt"], closes)
        if fr is None:
            missing.append(s["code"])
            continue
        vv = s.get("vv")
        groups[vv if vv in (True, False) else None].append(fr)

    rows = [group_stats(g, k) for k, g in
            ((True, groups[True]), (False, groups[False]), (None, groups[None])) if g]
    print("\n=== 后验对照表（检出日收盘买入，% 前复权口径随 parquet）===")
    print(pd.DataFrame(rows).to_string(index=False))
    if missing:
        print(f"\n无行情标的（已剔除）: {len(missing)} 条信号, {len(set(missing))} 只")

    # 模拟盘口径（可匹配样本，持仓期混合，仅参考）
    try:
        paper = json.load(open(PAPER, encoding="utf-8"))
    except Exception:
        paper = {"closed": []}
    ptrades = {(t["code"], t["type"], t["sig_dt"]): t.get("ret", t.get("pnl"))
               for t in paper.get("closed", []) + paper.get("open", []) if isinstance(t, dict)}
    pmap = {True: [], False: []}
    for s in sigs:
        key = (s["code"], s["type"], s["dt"])
        if key in ptrades and s.get("vv") in (True, False):
            r = ptrades[key]
            if r is not None:
                pmap[s["vv"]].append(r)
    print("\n=== 模拟盘口径（按 code+type+dt 关联 closed+open，持有期混合，仅参考）===")
    prows = []
    for k in (True, False):
        v = pmap[k]
        if v:
            prows.append({"组别": f"vv={k}", "笔数": len(v),
                          "均收益": f"{sum(v)/len(v)*100:+.2f}%",
                          "胜率": f"{sum(x > 0 for x in v)/len(v)*100:.1f}%"})
    print(pd.DataFrame(prows).to_string(index=False) if prows else "无可匹配模拟盘样本")


if __name__ == "__main__":
    main()
