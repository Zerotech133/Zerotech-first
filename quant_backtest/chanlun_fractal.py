# -*- coding: utf-8 -*-
"""缠论工作台 P2 结构识别 · 第一层：K 线包含处理 + 分型识别。

输入 P1 数据底座的升序 K 线 [{dt,o,h,l,c,v}]，analyze(bars) 返回：
  {"merged_n": 包含处理后K线数,
   "fractals": [{"dt","type":"顶"/"底","price"}...],
   "top_n" / "bottom_n"}
dt 取分型所在「处理后 K 线」组内首根原始 K 线的 dt（必存在于原始序列，
便于前端在原始 K 线上定位标注）。

规则（缠论标准）：
1) 包含处理：相邻 K 线存在包含关系（一根的高低点完全覆盖另一根）时合并——
   升向取 max(high)/max(low)，降向取 min(high)/min(low)；方向由前一处理后
   K 线相对其前一根的方向决定，首段默认向上。
2) 分型：处理后相邻三根，中间 K 线高、低点均为三者最高 → 顶分型；均为最低
   → 底分型。处理后相邻 K 线不再有包含关系，故顶/底分型严格交替。
"""

from chanlun_data import _f  # 复用同一容错数值转换


def _merge_inclusion(bars):
    """K 线包含处理。返回 [{"dt","h","l","n"}]（n=合并的原始根数）。"""
    out = []
    direction = 1  # 首段默认向上
    for b in bars:
        h, l = _f(b.get("h")), _f(b.get("l"))
        if h is None or l is None:
            continue
        if out:
            p = out[-1]
            if (p["h"] >= h and p["l"] <= l) or (h >= p["h"] and l <= p["l"]):
                if direction >= 0:
                    p["h"] = max(p["h"], h)
                    p["l"] = max(p["l"], l)
                else:
                    p["h"] = min(p["h"], h)
                    p["l"] = min(p["l"], l)
                p["n"] += 1
                continue
            direction = (
                1
                if (h > p["h"] and l > p["l"])
                else (-1 if (h < p["h"] and l < p["l"]) else direction)
            )
        out.append({"dt": b.get("dt"), "h": h, "l": l, "n": 1})
    return out


def find_fractals(merged):
    """处理后 K 线 → 分型列表（严格顶/底交替）。"""
    fr = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["h"] > a["h"] and b["h"] > c["h"] and b["l"] > a["l"] and b["l"] > c["l"]:
            fr.append({"dt": b["dt"], "type": "顶", "price": b["h"]})
        elif (
            b["l"] < a["l"] and b["l"] < c["l"] and b["h"] < a["h"] and b["h"] < c["h"]
        ):
            fr.append({"dt": b["dt"], "type": "底", "price": b["l"]})
    return fr


def analyze(bars):
    """P2 入口：原始升序 K 线 → 包含处理 + 分型识别结果。"""
    merged = _merge_inclusion(bars or [])
    fr = find_fractals(merged)
    return {
        "merged_n": len(merged),
        "fractals": fr,
        "top_n": sum(1 for x in fr if x["type"] == "顶"),
        "bottom_n": sum(1 for x in fr if x["type"] == "底"),
    }
