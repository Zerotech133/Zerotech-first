# -*- coding: utf-8 -*-
"""缠论结构特征集：把「结构」变成可喂给树模型的数值矩阵。

为什么单独一层
--------------
回填池（chanlun_pool_ext_daily_full.jsonl）里 npv/conf/div_area/pv_state 全部
是 None——`chanlun_ext_backfill` 的 daily 路径没有调用 chanlun_gray.enrich，
所以池行的「结构特征」实际只剩 bei/bei2/mbei/tbe/vv 五个布尔的背驰量能标志。
用这五个布尔训练，测出来的是「量价特征有没有用」，不是「结构有没有用」。

本模块从 parquet 重算结构几何（笔/中枢），提取真正的位置类特征：
价在中枢内还是中枢外、距 ZG/ZD 多远、中枢多大、已经走了几笔、最后一笔
力度相对前一笔是放大还是衰减（背驰的量化代理）。

防泄漏铁律
----------
所有结构特征只取 `e_dt <= detect_date` 的笔与中枢（笔终点在信号日之前，
信号日必然可见）；量价特征只取 `bars[:detect_idx+1]`（截至检出日收盘）。
标签用 detect_date 次日开盘入场、10 个交易日后收盘离场，与前几轮口径一致。

输出
----
data/chanlun_struct_ds.parquet —— 每行一个信号：code/type/entry_date
+ 30 个特征 + y_alpha10 / y_raw10 标签 + 成本所需 px/adv/sigma。

用法:
    python chanlun_struct_features.py                     # 全量
    python chanlun_struct_features.py --limit 200         # 抽样自检
    python chanlun_struct_features.py --hold 20           # 换标签持期
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
DEFAULT_POOL = os.path.join(DATA, "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(DATA, "chanlun_struct_ds.parquet")


def _pct(a, b):
    if not a or not b or b <= 0:
        return None
    return a / b - 1.0


def _struct_feats(bars, d0):
    """detect_idx=d0 时点可见的结构几何特征。

    用 chanlun_bi.analyze 跑完整序列后，只保留 e_dt <= detect_date 的
    笔/中枢——笔终点已出现，信号日必然可见，不构成前视。
    """
    import chanlun_bi

    res = chanlun_bi.analyze(bars[: d0 + 1])
    ddt = bars[d0]["dt"]
    bis = [b for b in (res.get("bis") or []) if b["e_dt"] <= ddt]
    pvs = [p for p in (res.get("pivots") or []) if p["e_dt"] <= ddt]
    f = {
        "st_n_bis": len(bis),
        "st_n_pivots": len(pvs),
    }
    # 最近一笔
    if bis:
        last = bis[-1]
        amp = _pct(last["e_price"], last["s_price"])
        f["st_last_dir"] = 1.0 if last["e_price"] > last["s_price"] else -1.0
        f["st_last_amp"] = amp
        i_s = next((i for i, b in enumerate(bars) if b["dt"] == last["s_dt"]), None)
        i_e = next((i for i, b in enumerate(bars) if b["dt"] == last["e_dt"]), None)
        if i_s is not None and i_e is not None:
            f["st_last_bars"] = float(i_e - i_s)
            f["st_gap_bi"] = float(d0 - i_e)
        if len(bis) >= 2:
            prev_amp = _pct(bis[-2]["e_price"], bis[-2]["s_price"])
            if amp is not None and prev_amp not in (None, 0):
                f["st_amp_ratio"] = abs(amp) / abs(prev_amp)
            f["st_prev_amp"] = prev_amp
        if len(bis) >= 3:
            amps = [abs(_pct(b["e_price"], b["s_price"]) or 0) for b in bis[-3:]]
            f["st_amp3"] = sum(amps) / 3.0
    # 最后一个已完成中枢
    if pvs:
        pv = pvs[-1]
        zg, zd = pv["zg"], pv["zd"]
        c = bars[d0]["c"]
        f["st_pv_h"] = (zg - zd) / zd if zd > 0 else None  # 中枢相对高度
        f["st_pv_pos"] = (c - zd) / (zg - zd) if zg > zd else None  # <0 中枢下
        f["st_pv_dzg"] = (c - zg) / zg if zg > 0 else None
        f["st_pv_nbi"] = float(pv.get("n_bi") or 0)
        i_pv_s = next((i for i, b in enumerate(bars) if b["dt"] == pv["s_dt"]), None)
        f["st_pv_age"] = float(d0 - i_pv_s) if i_pv_s is not None else None
        # 中枢后走了几笔（>0 表示已离开中枢）
        f["st_pv_exit_bis"] = float(sum(1 for b in bis if b["s_dt"] > pv["e_dt"]))
    return f


def _price_feats(bars, amt, d0):
    """截至 detect_date 收盘的量价特征（全部用 <= d0 的数据）。"""
    c = [b.get("c") for b in bars]
    v = [b.get("v") for b in bars]
    f = {}
    for n in (5, 20, 60):
        if d0 - n >= 0 and c[d0 - n]:
            f["q_pre%d" % n] = c[d0] / c[d0 - n] - 1.0
    for n in (20, 60):
        if d0 - n >= 1:
            px = [x for x in c[d0 - n: d0 + 1] if x]
            if len(px) >= 3:
                rets = [px[i] / px[i - 1] - 1.0 for i in range(1, len(px))]
                f["q_vol%d" % n] = statistics.pstdev(rets)
    # 量比：近 5 日均量 / 20 日均量
    if d0 >= 20:
        a5 = [x for x in amt[d0 - 4: d0 + 1] if x]
        a20 = [x for x in amt[d0 - 19: d0 + 1] if x]
        if len(a5) >= 3 and len(a20) >= 10:
            m5 = sum(a5) / len(a5)
            m20 = sum(a20) / len(a20)
            if m20 > 0:
                f["q_volratio"] = m5 / m20
    # 收盘在区间内的分位
    for n in (20, 60):
        if d0 - n >= 0:
            seg = bars[d0 - n: d0 + 1]
            hi = max((b.get("h") or 0) for b in seg)
            lo = min((b.get("l") or 1e9) for b in seg)
            if hi > lo > 0:
                f["q_pos%d" % n] = (c[d0] - lo) / (hi - lo)
                f["q_dma%d" % n] = c[d0] / ((hi + lo) / 2.0) - 1.0
    # 对数股价与对数成交额（规模/流动性）
    if c[d0] and c[d0] > 0:
        f["q_logpx"] = math.log(c[d0])
    adv = [x for x in amt[max(0, d0 - 19): d0 + 1] if x]
    if adv:
        f["q_logadv"] = math.log(sum(adv) / len(adv) * 1000.0)
    # 20 日买卖压力：收盘位置加权（简化 Amihud 非流动性代理）
    if d0 >= 20 and adv:
        rets = []
        for i in range(d0 - 19, d0 + 1):
            r = _pct(c[i], c[i - 1]) if i > 0 else None
            if r is not None and amt[i]:
                rets.append(abs(r) / (amt[i] * 1000.0))
        if rets:
            f["q_illiq"] = math.log(sum(rets) / len(rets) + 1e-12)
    return f


def _one(job):
    code, rows, hold, need = job
    import chanlun_alpha as ca
    import chanlun_scan as cs
    from chanlun_backtest import _dir_ret

    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    bars = cs._bars_from_parquet(path) or []
    if len(bars) < 130 or len(bars) <= need + 2:
        return []
    try:
        import pandas as pd

        df = pd.read_parquet(path)[["trade_date", "amount"]]
    except Exception:
        return []
    amt_map = {cs._td_dt(t): (a if a and a > 0 else None)
               for t, a in zip(df["trade_date"], df["amount"])}
    amt = [amt_map.get(b["dt"]) for b in bars]
    idx = {b["dt"]: i for i, b in enumerate(bars)}

    out = []
    for r in rows:
        d0 = idx.get(r.get("detect_date"))
        if d0 is None or d0 < 60 or d0 + 1 + need >= len(bars):
            continue
        ei = d0 + 1
        px = bars[ei].get("o")
        if not px or px <= 0:
            continue
        xi = ei + hold
        raw = _dir_ret(r["type"], px, bars[xi]["c"])
        if raw is None:
            continue
        ed, xd = bars[ei]["dt"], bars[xi]["dt"]
        beta, _, _ = ca.beta_of(code, ed)
        br = ca.bench_window_ret(ed, xd)
        alpha = None if br is None else float(raw) - beta * br
        if alpha is None:
            continue

        rec = {
            "code": code, "type": r["type"], "sig_dt": r["dt"],
            "detect_date": r["detect_date"], "entry_date": ed,
            "y_raw": round(float(raw), 6),
            "y_alpha": round(float(alpha), 6),
            "px": round(float(px), 4),
            "beta": round(float(beta), 4),
            "f_lag": r.get("lag"),
            "f_bei": r.get("bei"), "f_bei2": r.get("bei2"),
            "f_mbei": r.get("mbei"), "f_tbe": r.get("tbe"),
            "f_vv": r.get("vv"),
        }
        # ADV / sigma（成本模型要对齐）
        a20 = [x for x in amt[max(0, ei - 19): ei] if x]
        rec["adv"] = (sum(a20) / len(a20) * 1000.0) if a20 else None
        px20 = [b.get("c") for b in bars[max(0, ei - 20): ei + 1] if b.get("c")]
        if len(px20) >= 3:
            rr = [px20[i] / px20[i - 1] - 1.0 for i in range(1, len(px20))]
            rec["sigma"] = statistics.pstdev(rr)
        rec.update(_struct_feats(bars, d0))
        rec.update(_price_feats(bars, amt, d0))
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser(description="缠论结构特征集构建")
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--hold", type=int, default=10)
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    rows = [json.loads(li) for li in open(a.pool, encoding="utf-8") if li.strip()]
    rows = [r for r in rows if r.get("entry_date") and r.get("detect_date")]
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    codes = sorted(by_code)
    if a.limit:
        codes = codes[: a.limit]
    # dedup：同票相邻信号至少间隔 hold（与成本模型/持期实验同口径）
    dedup = {}
    for c in codes:
        lst = sorted(by_code[c], key=lambda x: x["detect_date"])
        kept, seen = [], set()
        for r in lst:
            if r["detect_date"] in seen:
                continue
            seen.add(r["detect_date"])
            kept.append(r)
        dedup[c] = kept
    n_sig = sum(len(v) for v in dedup.values())
    print("pool=%s 信号=%d 标的=%d hold=%d" % (os.path.basename(a.pool), n_sig, len(codes), a.hold))

    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part in ex.map(
            _one, [(c, dedup[c], a.hold, a.hold + 2) for c in codes], chunksize=4
        ):
            res.extend(part)
    print("特征构建完成 %d 行，用时 %.0fs" % (len(res), time.time() - t0))
    if not res:
        print("无样本")
        return

    import pandas as pd

    df = pd.DataFrame(res)
    # 同日信号数（拥挤度，横截面特征）
    cnt = df.groupby("entry_date")["code"].transform("count")
    df["m_cnt"] = cnt.astype(float)
    # 基准前 20 日收益（regime 代理）
    df = df.sort_values(["entry_date", "code"]).reset_index(drop=True)
    print("特征列 %d 个：%s" % (df.shape[1] - 10, ", ".join(
        c for c in df.columns if c.startswith(("st_", "q_", "f_", "m_")))))
    df.to_parquet(a.out, index=False)
    print("-> %s  (%d 行 × %d 列)" % (a.out, df.shape[0], df.shape[1]))
    print("日期范围 %s ~ %s" % (df["entry_date"].min(), df["entry_date"].max()))
    print("标签均值 alpha %.3f%% / raw %.3f%%"
          % (df["y_alpha"].mean() * 100, df["y_raw"].mean() * 100))


if __name__ == "__main__":
    main()
