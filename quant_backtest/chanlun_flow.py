# -*- coding: utf-8 -*-
"""缠论工作台 P8 共振层 · 资金量能 + 大盘环境（影子标注）。

教学口径（2026-09 抖音缠论背驰教学）：背驰只描述动能衰减，「真正决定
方向的是资金」——需资金配合、大盘环境支持，多条件共振才有意义。本模块
两个判定均只作信号影子标注，不拦截、不单独成信号。

P8-a 量能共振（volume_confluence，全部信号类型）：
· 取信号终结的同向笔（买=向下笔、卖=向上笔）与其上一根同向笔，比较
  两根笔的「笔内 K 线平均成交量」；本次笔缩量（< 前同向笔 ×VOL_RATIO）
  视为资金配合——买类=下跌缩量卖压衰竭/回踩惜售，卖类=上涨缩量追高乏
  力。成交量缺失（全 0/None）→ (None, None)。

P8-b 大盘环境（regime_at）：
· 信号日截断指数日线（防未来函数），用收盘 vs MA20/MA60 定环境：
  收>MA20>MA60=up，收<MA20<MA60=down，其余=flat；
· 指数代理：CN=000001.SH 上证综指，CRYPTO=BTC/USDT；US/HK 库内无指数
  /ETF 数据 → None（无数据 ≠ 逆风，不参与覆盖率分母）。
· 顺风口径：买类 up、卖类 down 为顺风（aligned）。

P9 长周期趋势 regime（regime_trend_at，2026-09-27 影子标注·不拦截）：
· 信号日截断指数日线，用收盘 vs MA200 定长周期牛熊：
  收>MA200=True（牛市 regime），收≤MA200=False（熊市 regime）；
· 与 P8-b 的 MA20/MA60 短周期环境并行影子、口径不互相推翻——
  MA200 抓的是结构性牛熊分水岭（2018-2020 工作 / 2021-2023 失效的根因），
  MA20/MA60 抓的是中短期趋势配合。两者可同时为 True 也可分歧。
· 指数代理、数据不足口径与 P8-b 一致；MA200 需 ≥200 根历史。
· 时间外扩验证（2018-2023 tushare 8 标的）：close>MA200 子集 on 夏普 1.37、
  off 夏普 -1.73、差 3.10；样本内达标，仍需在线影子积累 n≥10 再评估启用。

volume_confluence(bars, bis, sig) → (vv, vv_ev)
  vv_ev=[前同向笔均量, 本次笔均量]
regime_at(market, sig_dt, idx_bars, ma_f=20, ma_s=60)
  → (reg, reg_ev)；reg ∈ up/down/flat/None；reg_ev=[收, MA20, MA60]
regime_trend_at(market, sig_dt, idx_bars, ma_n=200)
  → (reg_trend, reg_trend_ev)；reg_trend ∈ True/False/None；
    reg_trend_ev=[收, MA200]
aligned(reg, sig_type) → True/False/None
"""

VOL_RATIO = 1.0
REGIME_INDEX = {"CN": "000001.SH", "CRYPTO": "BTC/USDT"}
MA_FAST = 20
MA_SLOW = 60
MA_TREND = 200           # P9 长周期牛熊分水岭（close>MA200=牛市 regime）


def _ending_bi(bis, sig):
    want = -1 if sig["type"].endswith("买") else 1
    for i, b in enumerate(bis):
        if b["e_dt"] == sig.get("dt") and b["dir"] == want:
            return i, b
    return None, None


def _bi_mean_vol(bars, dt2i, bi):
    i0, i1 = dt2i.get(bi["s_dt"]), dt2i.get(bi["e_dt"])
    if i0 is None or i1 is None or i1 < i0:
        return None
    vs = [bars[i].get("v") or 0 for i in range(i0, i1 + 1)]
    if not vs or sum(vs) <= 0:
        return None
    return sum(vs) / len(vs)


def volume_confluence(bars, bis, sig):
    """信号终结笔均量 vs 上一同向笔均量：缩量 → vv=True。"""
    dt2i = {b["dt"]: i for i, b in enumerate(bars)}
    k, bi = _ending_bi(bis, sig)
    if bi is None:
        return None, None
    prev = None
    for j in range(k - 1, -1, -1):
        if bis[j]["dir"] == bi["dir"]:
            prev = bis[j]
            break
    v_now = _bi_mean_vol(bars, dt2i, bi)
    v_prev = _bi_mean_vol(bars, dt2i, prev) if prev is not None else None
    if v_now is None or v_prev is None:
        return None, None
    return (v_now < v_prev * VOL_RATIO,
            [round(v_prev, 2), round(v_now, 2)])


def _ma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def regime_at(market, sig_dt, idx_bars, ma_f=MA_FAST, ma_s=MA_SLOW):
    """信号日（含）截断指数序列后的多空环境；数据不足/缺指数 → None。"""
    if market not in REGIME_INDEX or not idx_bars:
        return None, None
    hist = [b for b in idx_bars if b["dt"] <= sig_dt]
    if len(hist) < ma_s:
        return None, None
    closes = [b["c"] for b in hist]
    c, mf, ms = closes[-1], _ma(closes, ma_f), _ma(closes, ma_s)
    if c > mf > ms:
        reg = "up"
    elif c < mf < ms:
        reg = "down"
    else:
        reg = "flat"
    return reg, [round(c, 4), round(mf, 4), round(ms, 4)]


def aligned(reg, sig_type):
    """买类顺风=up，卖类顺风=down；flat 明确不顺风，None=不可判。"""
    if reg is None:
        return None
    want = "up" if sig_type.endswith("买") else "down"
    return reg == want


def regime_trend_at(market, sig_dt, idx_bars, ma_n=MA_TREND):
    """P9 长周期趋势 regime：信号日截断指数，收>MA200=True（牛市 regime）。

    与 regime_at(MA20/MA60) 并行影子、口径不互相推翻。数据不足/缺指数 → None。
    时间外扩验证（2018-2023）：close>MA200 子集 on 夏普 1.37 off -1.73。
    """
    if market not in REGIME_INDEX or not idx_bars:
        return None, None
    hist = [b for b in idx_bars if b["dt"] <= sig_dt]
    if len(hist) < ma_n:
        return None, None
    closes = [b["c"] for b in hist]
    c, ma = closes[-1], _ma(closes, ma_n)
    if ma is None:
        return None, None
    return (c > ma, [round(c, 4), round(ma, 4)])
