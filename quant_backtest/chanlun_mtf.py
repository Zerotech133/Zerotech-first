# -*- coding: utf-8 -*-
"""缠论工作台 P7 多级别联立 · 日线信号 × 60 分钟次级别共振（影子标注）。

教学口径（2026-09 抖音缠论背驰教学）：「日线处于上涨趋势时，次级别底
背驰构成日线的三买」——大级别定位置、小级别择时机，多级别同一位置给
出同一结论才有意义。本模块只做影子标注（mt/mtv），不拦截、不单独成信号。

判定（v1 简化口径，如实声明）：
· 对日线买类信号（1/2/3买），取其终结的那根日线向下笔（s_dt→信号日）
  为观察窗；在 60m K 线上跑完整信号层，若窗内出现至少一个「次级别 1买
  且 MACD 底背驰 mbei=True」→ mt=True（卖类对称：窗内 1卖+顶背驰）。
· 次级别数据未覆盖整个观察窗（US/HK 分钟存量短、CN 走 tushare 较深、
  CRYPTO 走币安）→ mt=None，绝不记 False（无数据 ≠ 无共振）。

confluence(bis_d, sig, bars_sub, sub_period="60m")
  → (mt, mtv)
    mt: True/False/None（None=数据未覆盖或结构不可判）
    mtv: {"sub","win":[起,止],"hit":次级别信号dt,"mfv":...} 或 None
"""

from chanlun_signal import analyze as _sub_analyze

SUB_PERIOD = "60m"
# 观察窗起止允许的次级别覆盖余量（根）：窗末日至少有数据到当日
_TAIL_TOL_DAYS = 3


def _window_bi(bis_d, sig):
    """日线信号终结的同向笔（买=向下笔，卖=向上笔）；找不到返回 None。"""
    want = -1 if sig["type"].endswith("买") else 1
    for b in bis_d:
        if b["e_dt"] == sig.get("dt") and b["dir"] == want:
            return b
    return None


def _covered(bars_sub, d0, d1):
    """次级别数据是否覆盖观察窗 [d0, d1]（按日期前缀比较，允许末日滞后
    几根/跨周末，TAIL_TOL 仅在数据末日晚于 d1-3 日即视为覆盖尾部）。"""
    if not bars_sub:
        return False
    ds = sorted(b["dt"][:10] for b in bars_sub)
    if ds[0] > d0:
        return False
    # 末日数据：不晚于 d1 太多即可（次级别信号可能在 d1 当日盘中确认）
    return ds[-1] >= d0 and not (
        ds[-1] < d1 and (ds[-1] < _shift_day(d1, -_TAIL_TOL_DAYS))
    )


def _shift_day(d, n):
    """YYYY-MM-DD 粗移 n 个自然日（仅覆盖判定容差用，不要求交易日历）。"""
    from datetime import datetime, timedelta

    try:
        return (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=n)).strftime(
            "%Y-%m-%d"
        )
    except ValueError:
        return d


def confluence(bis_d, sig, bars_sub, sub_period=SUB_PERIOD):
    bi = _window_bi(bis_d, sig)
    if bi is None or not bars_sub:
        return None, None
    d0, d1 = bi["s_dt"], sig["dt"]
    if not _covered(bars_sub, d0, d1):
        return None, None
    sub = _sub_analyze(bars_sub)
    want_t = "1买" if sig["type"].endswith("买") else "1卖"
    for s in sub.get("signals", []):
        if s.get("type") != want_t or s.get("mbei") is not True:
            continue
        sd = s["dt"][:10]
        if d0 <= sd <= d1:
            return True, {
                "sub": sub_period,
                "win": [d0, d1],
                "hit": s["dt"],
                "mfv": s.get("mfv"),
            }
    return False, {"sub": sub_period, "win": [d0, d1], "hit": None, "mfv": None}
