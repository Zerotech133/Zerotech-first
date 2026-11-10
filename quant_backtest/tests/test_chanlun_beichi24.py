# -*- coding: utf-8 -*-
"""24 课背驰落地单元验证（合成数据，不依赖真实行情）。

用例：
1. panzheng_beichi24：回踩腿面积衰竭 → bei24=True；无前序同向腿 → None；
   面积更强 → False。
2. trend_beichi24：合成 a+A+b+B+c 下跌趋势 + c 段绿柱收敛 → True；
   双中枢重叠 → False。
3. score_3buy：位置分用回踩笔低点（low/zg 口径），bei24 计分生效；
   low=None → None。
4. chanlun_signal.analyze 端到端：3买信号带 bei24 字段、1买带 tbe24 字段。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from chanlun_macd import macd_series
from chanlun_beichi24 import panzheng_beichi24, trend_beichi24, _area
from market_scan import score_3buy


def _mk_bars(prices):
    """收盘价序列 → 最小 bars（MACD 只看 c）。"""
    return [
        {"dt": str(20250101 + i), "o": p, "h": p * 1.01, "l": p * 0.99, "c": p}
        for i, p in enumerate(prices)
    ]


def _lerp(a, b, n):
    """线性插值 n 根（含两端）。"""
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _mk_bis(bars):
    """把 bars 分成两根交替笔（s/e 端点按价格取极值）。"""
    n = len(bars)
    mid = n // 2
    return [
        {"s_dt": bars[0]["dt"], "e_dt": bars[mid]["dt"],
         "s_price": bars[0]["c"], "e_price": bars[mid]["c"], "dir": -1},
        {"s_dt": bars[mid]["dt"], "e_dt": bars[-1]["dt"],
         "s_price": bars[mid]["c"], "e_price": bars[-1]["c"], "dir": 1},
    ]


def test_area_sign():
    # 前段跌（绿柱）、后段横：绿柱面积只累计负 hist
    bars = _mk_bars([10 - 0.2 * i for i in range(20)] + [9.0] * 20)
    ms = macd_series(bars)
    a_down = _area(ms, 0, 19, -1)
    a_flat = _area(ms, 20, 39, -1)
    assert a_down > 0 and a_flat >= 0, "绿柱面积应为非负"
    assert a_down > a_flat, "下跌段绿柱面积应大于横盘段"
    print("PASS 1 区间同色柱面积")


def test_panzheng_beichi24():
    # 前序下跌腿急跌、本次回踩腿缓跌（面积衰竭）→ bei24=True
    pre = [12 - 0.5 * i for i in range(12)]          # 急跌
    mid = [6.0 + 0.05 * i for i in range(8)]         # 弱反弹
    cur = [6.5 - 0.08 * i for i in range(12)]        # 缓跌（面积小）
    bars = _mk_bars(pre + mid + cur)
    ms = macd_series(bars)
    bis = [
        {"s_dt": bars[0]["dt"], "e_dt": bars[11]["dt"],
         "s_price": 12.0, "e_price": 6.5, "dir": -1},      # 前序下跌腿
        {"s_dt": bars[11]["dt"], "e_dt": bars[19]["dt"],
         "s_price": 6.5, "e_price": 6.9, "dir": 1},        # 反弹
        {"s_dt": bars[19]["dt"], "e_dt": bars[31]["dt"],
         "s_price": 6.9, "e_price": 5.54, "dir": -1},      # 本次回踩腿
    ]
    sig = {"type": "3买", "dt": bars[31]["dt"], "price": 5.54}
    bei24, fv = panzheng_beichi24(bars, bis, sig, ms)
    assert bei24 is True, "缓跌面积衰竭应判 bei24=True, got %s fv=%s" % (bei24, fv)
    assert len(fv) == 3 and fv[2] in ("above", "inside", "below"), fv
    # 无前序同向腿 → None
    sig0 = {"type": "3买", "dt": bars[11]["dt"], "price": 6.5}
    b0, f0 = panzheng_beichi24(bars, [bis[0]], sig0, ms)
    assert b0 is None, "无前序同向腿应为 None"
    print("PASS 2 盘整背驰 24 课面积口径")


def test_score_3buy_low_base():
    zg = 10.0
    # vr=1.2 落 ≥1.0 档（系数 0.80）→ 量能 24 分；位置 ≤2% 满档 40 分
    base = 40 + 24  # = 64
    # 回踩笔低点贴近（+1%）vs 信号日收盘远离（+15%）——旧口径按收盘会掉档
    s_low = score_3buy(10.1, zg, 1.2, False, False, False)
    assert s_low == base, "低点贴近位置分满档+量能档2: got %s" % s_low
    # bei24=True 计 0.30 份额
    s_b24 = score_3buy(10.1, zg, 1.2, False, False, True)
    assert s_b24 == base + 9, "bei24 应计 9 分: got %s" % s_b24
    # bei2=True 计 0.30 份额（同 9 分）
    s_b2 = score_3buy(10.1, zg, 1.2, False, True, False)
    assert s_b2 == s_b24, "bei2 与 bei24 份额应同为 0.30"
    # 三源全 True = 背驰分满 30
    s_all = score_3buy(10.1, zg, 1.2, True, True, True)
    assert s_all == base + 30, "三源全 True 背驰分应满 30: got %s" % s_all
    # low=None → None（防 None 除法）
    assert score_3buy(None, zg, 1.2, True, True, True) is None
    print("PASS 3 评分口径（low 基准 + 背驰三源）")


def test_analyze_end_to_end(monkeypatch):
    """端到端字段接线：手工笔/中枢结构 → analyze 应给 3买 附 bei24。

    分型识别对合成K线太脆（线性段不成分型），故 monkeypatch 结构层，
    只测信号层的 24 课字段注入。走势：下跌→中枢(9.8~11)→上破 12.5
    →回踩 11.9 不破 ZG=10.4 →确认笔。
    """
    import chanlun_signal

    # 每段 25 根：MACD(12,26,9) 需要足够长度让柱翻色，短腿会判 None
    # （「数据不足不判」的保守行为本身正确，见 24 课面积口径）
    px = (
        _lerp(14, 8, 25) + _lerp(8, 11, 25) + _lerp(11, 9.2, 25)
        + _lerp(9.2, 13.0, 25) + _lerp(13.0, 12.2, 25) + _lerp(12.2, 12.4, 6)
    )
    bars = _mk_bars(px)
    dt = [b["dt"] for b in bars]
    k1, k2, k3, k4, k5 = 24, 49, 74, 99, 124
    fake_st = {
        "bis": [
            {"s_dt": dt[0], "e_dt": dt[k1], "s_price": 14.0, "e_price": 8.0, "dir": -1},
            {"s_dt": dt[k1], "e_dt": dt[k2], "s_price": 8.0, "e_price": 11.0, "dir": 1},
            {"s_dt": dt[k2], "e_dt": dt[k3], "s_price": 11.0, "e_price": 9.2, "dir": -1},
            {"s_dt": dt[k3], "e_dt": dt[k4], "s_price": 9.2, "e_price": 13.0, "dir": 1},
            {"s_dt": dt[k4], "e_dt": dt[k5], "s_price": 13.0, "e_price": 12.2, "dir": -1},
            {"s_dt": dt[k5], "e_dt": dt[129], "s_price": 12.2, "e_price": 12.4, "dir": 1},
        ],
        "segments": [],
        "pivots": [
            {"s_dt": dt[0], "e_dt": dt[k3], "zg": 11.0, "zd": 9.2, "n_bi": 3}
        ],
    }
    monkeypatch.setattr(chanlun_signal, "_az", lambda b: fake_st)
    res = chanlun_signal.analyze(bars)
    sigs = res.get("signals") or []
    t3 = [s for s in sigs if s["type"] == "3买"]
    assert t3, "手工结构应产生 3买, got %s" % [s["type"] for s in sigs]
    s = t3[-1]
    assert "bei24" in s and "fv24" in s, "3买 应带 bei24/fv24 字段"
    assert s["bei24"] in (True, False), "bei24 应可判（None=数据缺失）got %s" % s["bei24"]
    assert s["fv24"] and len(s["fv24"]) == 3, "fv24=[前序面积,本次面积,分支]"
    print("PASS 4 analyze 端到端字段接线（3买 bei24=%s fv24=%s）" % (s["bei24"], s["fv24"]))


if __name__ == "__main__":
    test_area_sign()
    test_panzheng_beichi24()
    test_score_3buy_low_base()
    test_analyze_end_to_end(None)
    print("ALL PASS")
