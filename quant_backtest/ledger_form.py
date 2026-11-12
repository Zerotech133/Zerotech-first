# -*- coding: utf-8 -*-
"""账本形态语言层 · 适配层（算法已上移到 chanlun_form）

2026-11-12：形态不再由账本私算。market_scan 出信号时手里已有 analyze() 结果，
直接复用落盘（零成本）；账本只读不算。故本模块只保留「读日线」与两个兼容
门面：form_of / chart_of —— 老调用方（体检脚本、叙述层）无需改动。

算法口径、踩坑记录、为什么按 day 而非 dt 自算，全部见 chanlun_form 模块头。
"""
import os

import pandas as pd

import chanlun_form as CF


def bars_of(fp):
    """单只票全量日线 → [{dt,o,h,l,c,v}]（升序）。读不到返回 []。"""
    if not os.path.exists(fp):
        return []
    df = pd.read_parquet(fp)
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date")
    cols = {c: c for c in ("open", "high", "low", "close") if c in df.columns}
    if len(cols) < 4:
        return []
    out = []
    for r in df.itertuples():
        try:
            out.append({"dt": str(r.trade_date), "o": float(r.open),
                        "h": float(r.high), "l": float(r.low),
                        "c": float(r.close),
                        "v": float(getattr(r, "vol", 0) or 0)})
        except (TypeError, ValueError):
            continue
    return out


def form_of(bars, day, price=None, zg=None, zd=None, dt=None):
    """截止 day 自算结构 → 形态 dict（见 chanlun_form.form_at）。"""
    return CF.form_at(bars, day, price, zg, zd, dt)


def chart_of(bars, day, nd=CF.CHART_ND):
    """→ 绘图数据。与 form_of 同一份结构，图文指向同一个中枢。"""
    if not bars:
        return None
    seq = [b for b in bars if str(b["dt"]) <= str(day)][-CF.N:]
    if len(seq) < CF.MIN_BARS:
        return None
    from chanlun_bi import analyze

    return CF.chart_from_struct(analyze(seq, [b["c"] for b in seq]), seq, nd)
