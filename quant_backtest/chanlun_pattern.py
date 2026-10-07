# -*- coding: utf-8 -*-
"""缠论工作台 P2 补强 · 经典 K 线 15 形态识别（K172，tradingview-mcp 形态面自研）。

口径（双侧反转形态，趋势前置判定反转方向；与 chanlun_bi 笔端点/信号日
做「结构+形态」共振标注）：
· bars：[{dt,o,h,l,c}]（升序，与 chanlun_bi 同源 K 线）；
· 前置趋势：trend_win 根前收盘 vs 当前收盘，|涨跌| ≥ trend_thr 视为
  有趋势（上涨/下跌），形态的看涨/看跌判定依赖此前置；
· 实体 body=|c-o|、全幅 range=h-l、上影 us=h-max(o,c)、下影 ls=min(o,c)-l；
· 输出 [{dt,name,dir}] dir=1 看涨 / -1 看跌 / 0 中性，按 dt 升序；
  同日多形态按 dir 强度优先取第一个（共振标注取单名避免标签重叠）。

16 形态清单（看涨 8 / 看跌 8，均为教科书定义的简化数值口径；
「看跌孕线」由孕线分支的第二向产出——K210 图内实证补列，历史上
docstring 漏列导致「Technique 第 22 节点」疑云）：
  看涨：锤头 / 倒锤头 / 看涨吞没 / 启明星 / 刺透 / 三白兵 /
        看涨孕线 / 蜻蜓十字
  看跌：上吊线 / 流星线 / 看跌吞没 / 黄昏星 / 乌云盖顶 / 三乌鸦 /
        墓碑十字 / 看跌孕线
"""
TREND_WIN = 5
TREND_THR = 0.015      # 1.5% 视为有前置趋势
DOJI_BODY = 0.08       # 实体/全幅 ≤8% 视为十字


def _trend(bars, i):
    """i 日前置趋势：1 上涨 / -1 下跌 / 0 无（相对 trend_win 根前）。"""
    j = i - TREND_WIN
    if j < 0 or not bars[j]["c"]:
        return 0
    return 1 if bars[i]["c"] >= bars[j]["c"] * (1 + TREND_THR) else \
        (-1 if bars[i]["c"] <= bars[j]["c"] * (1 - TREND_THR) else 0)


def _geo(b):
    body = abs(b["c"] - b["o"])
    rng = max(b["h"] - b["l"], 1e-9)
    us = b["h"] - max(b["o"], b["c"])
    ls = min(b["o"], b["c"]) - b["l"]
    up = b["c"] > b["o"]
    return body, rng, us, ls, up


def detect(bars):
    """16 形态扫描 → [{dt,name,dir}]（同日取强度最高一型，dt 升序）。"""
    out = []
    n = len(bars or [])
    for i in range(2, n):
        b = bars[i]
        body, rng, us, ls, up = _geo(b)
        p1 = bars[i - 1]
        p2 = bars[i - 2]
        b1_body, _, _, _, b1_up = _geo(p1)
        tr = _trend(bars, i)
        name = dir_ = None
        doji = body <= rng * DOJI_BODY

        # —— 单根反转（影线口径=全幅比例：下影/上影占比判型） ——
        if doji and ls > body * 3 and us < body * 2:
            name, dir_ = "蜻蜓十字", 1 if tr <= 0 else 0
        elif doji and us > body * 3 and ls < body * 2:
            name, dir_ = "墓碑十字", -1 if tr >= 0 else 0
        elif ls >= rng * 0.6 and us <= rng * 0.1 and body < rng * 0.25:
            if tr == -1:
                name, dir_ = "锤头", 1
            elif tr == 1:
                name, dir_ = "上吊线", -1
        elif us >= rng * 0.6 and ls <= rng * 0.1 and body < rng * 0.25:
            if tr == -1:
                name, dir_ = "倒锤头", 1
            elif tr == 1:
                name, dir_ = "流星线", -1

        # —— 两根吞没/孕线/刺透/乌云 ——
        if name is None and b1_body > 0 and body > 0:
            if (not b1_up) and up and b["c"] > p1["o"] and b["o"] < p1["c"] \
                    and body > b1_body and tr == -1:
                name, dir_ = "看涨吞没", 1
            elif b1_up and (not up) and b["c"] < p1["o"] and \
                    b["o"] > p1["c"] and body > b1_body and tr == 1:
                name, dir_ = "看跌吞没", -1
            elif (not b1_up) and up and b["o"] < p1["c"] and \
                    b["c"] > (p1["o"] + p1["c"]) / 2 and \
                    b["c"] < p1["o"] and tr == -1:
                name, dir_ = "刺透", 1
            elif b1_up and (not up) and b["o"] > p1["c"] and \
                    b["c"] < (p1["o"] + p1["c"]) / 2 and \
                    b["c"] > p1["o"] and tr == 1:
                name, dir_ = "乌云盖顶", -1
            elif body < b1_body and \
                    min(b["o"], b["c"]) >= min(p1["o"], p1["c"]) - 1e-9 and \
                    max(b["o"], b["c"]) <= max(p1["o"], p1["c"]) + 1e-9:
                # 孕线：第二根实体完全含于第一根实体内，方向=反转第一根
                if b1_up and tr >= 0:
                    name, dir_ = "看跌孕线", -1
                elif (not b1_up) and tr <= 0:
                    name, dir_ = "看涨孕线", 1

        # —— 三根星线/兵乌 ——
        if name is None and i >= 2:
            b2_body, _, _, _, b2_up = _geo(p2)
            b1_body, _, _, _, b1_up = _geo(p1)
            if (not b2_up) and b2_body > 0 and \
                    b1_body <= b2_body * 0.5 and up and \
                    b["c"] > (p2["o"] + p2["c"]) / 2 and tr == -1:
                name, dir_ = "启明星", 1
            elif b2_up and b2_body > 0 and \
                    b1_body <= b2_body * 0.5 and (not up) and \
                    b["c"] < (p2["o"] + p2["c"]) / 2 and tr == 1:
                name, dir_ = "黄昏星", -1
            elif i >= 2 and up and b1_up and b2_up and \
                    b["c"] > p1["c"] > p2["c"] and \
                    body >= b1_body >= b2_body and \
                    us <= body * 0.3 and tr == 1:
                name, dir_ = "三白兵", 1
            elif (not up) and (not b1_up) and (not b2_up) and \
                    b["c"] < p1["c"] < p2["c"] and \
                    body >= b1_body >= b2_body and \
                    ls <= body * 0.3 and tr == -1:
                name, dir_ = "三乌鸦", -1

        if name:
            out.append({"dt": b["dt"], "name": name, "dir": dir_ or 0})
    return out


def resonmap(patterns, signal_dts):
    """结构信号日共振映射：{dt: 形态名}（仅信号日出现的形态，
    同日多形态取 dir 非零优先、名短优先，避免标签重叠）。"""
    sig = set(signal_dts or [])
    byd = {}
    for p in patterns or []:
        if p["dt"] not in sig:
            continue
        cur = byd.get(p["dt"])
        if cur is None or (p["dir"] != 0 and cur["dir"] == 0) or \
                (len(p["name"]) < len(cur["name"])):
            byd[p["dt"]] = p
    return {d: p["name"] for d, p in byd.items()}
