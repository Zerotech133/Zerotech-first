# -*- coding: utf-8 -*-
"""缠论工作台 P6-a 指标层 · MACD（DIF/DEA/柱）与价格背驰（影子证据）。

教学口径（2026-09 抖音缠论背驰教学复刻）：
· MACD 背驰最直观——价格不断创新低但 MACD 绿柱越来越短 = 底背驰；
  价格创新高但红柱缩小 = 顶背驰；本质是「价格在演戏、能量在漏线」。
· 背驰 ≠ 反转，只描述原趋势动能衰减，故本模块产出仅作信号的影子
  标注（mbei/mfv），不拦截、不单独成信号。

实现简化（如实声明）：以 DIF 极值位比较代替红绿柱面积——
· 底背驰：当前下跌笔终点价格低于上一同向下跌笔终点（创新低），
  但该点 DIF 高于上一点 DIF（负值收敛/能量衰竭）；
· 顶背驰：当前上涨笔终点创新高而 DIF 低于上一同向上涨笔终点。
两点都要求信号笔在 bis 中存在同向前序笔，否则 mbei=None。

macd_series(bars, FAST=12, SLOW=26, SIG=9)
  → [{"dif","dea","hist"}]（与 bars 对齐；hist=2*(dif-dea)，国内惯例）
divergence(bars, bis, sig)
  → (mbei, mfv)；mfv=[前序笔端点价, 本笔端点价, 前序 DIF, 本笔 DIF]
"""

FAST = 12
SLOW = 26
SIG = 9


def _ema(vals, n):
    """标准 EMA：ema0=vals[0]，其后 alpha 递推（与 vals 等长对齐）。"""
    if not vals:
        return []
    a = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(out[-1] + a * (v - out[-1]))
    return out


def macd_series(bars, fast=FAST, slow=SLOW, sig=SIG):
    closes = [b["c"] for b in bars]
    if not closes:
        return []
    ef, es = _ema(closes, fast), _ema(closes, slow)
    dif = [round(f - s, 6) for f, s in zip(ef, es)]
    # DEA = DIF 的 9 周期 EMA（DIF 前缀未稳定段仍递推，不影响尾部比较）
    a = 2.0 / (sig + 1)
    dea = [dif[0]]
    for d in dif[1:]:
        dea.append(dea[-1] + a * (d - dea[-1]))
    return [{"dif": round(dif[i], 6), "dea": round(dea[i], 6),
             "hist": round(2 * (dif[i] - dea[i]), 6)}
            for i in range(len(closes))]


def divergence(bars, bis, sig, macd=None):
    """信号笔终点 vs 上一同向笔终点的「价格 + DIF」背驰判定。

    买类（1买）：信号笔为下跌笔，价创新低且 DIF 抬高 → 底背驰；
    卖类（1卖）：上涨笔，价创新高且 DIF 走低 → 顶背驰。
    无同向前序笔 / 信号笔找不到 → (None, None)。
    """
    dt2i = {b["dt"]: i for i, b in enumerate(bars)}
    want_dir = -1 if sig["type"].endswith("买") else 1
    end_k = None
    for i, b in enumerate(bis):
        if b["e_dt"] == sig.get("dt") and b["dir"] == want_dir:
            end_k = i
            break
    if end_k is None:
        return None, None
    prev_k = None
    for j in range(end_k - 1, -1, -1):
        if bis[j]["dir"] == want_dir:
            prev_k = j
            break
    if prev_k is None:
        return None, None
    this_b, prev_b = bis[end_k], bis[prev_k]
    i_this = dt2i.get(this_b["e_dt"])
    i_prev = dt2i.get(prev_b["e_dt"])
    if i_this is None or i_prev is None:
        return None, None
    macd = macd or macd_series(bars)
    d_this = macd[i_this]["dif"]
    d_prev = macd[i_prev]["dif"]
    px_this, px_prev = this_b["e_price"], prev_b["e_price"]
    if want_dir == -1:
        ok = px_this < px_prev and d_this > d_prev      # 底背驰
    else:
        ok = px_this > px_prev and d_this < d_prev      # 顶背驰
    return bool(ok), [round(px_prev, 4), round(px_this, 4),
                      round(d_prev, 4), round(d_this, 4)]
