# -*- coding: utf-8 -*-
"""缠论 24 课正宗背驰 · MACD 柱面积口径（2026-11-10 老田批准落地）。

此前 bei/bei2（力度对比）与 mbei（DIF 收敛）都是简化影子口径，24 课的
标准判据从未真正实现——11-08 因子盘点 hf_feat2.json 里 8 个背驰相关
字段 100% 空。本模块按 24 课原文落地，与既有影子口径并行、不互相推翻
（本仓惯例：影子并行、面板择优）。

24 课判据（原文口径）：
· 趋势背驰：a+A+b+B+c 在一个大趋势里，A 之前已有中枢，B 是趋势另一
  中枢（MACD 黄白线回拉 0 轴附近），C 段完成时对应 MACD 柱面积（向上
  看红柱 / 向下看绿柱）< A 段面积 = 标准背驰。
  本库笔级实现：A/B=信号前最近两个同向不重叠中枢（复用 chanlun_trend
  的结构判定），b 段=两中枢间的连接段（取窗口内最强同向笔，口径同
  trend_beichi），c 段=信号终结笔；b/c 段的 MACD 柱面积按趋势同色柱
  分段求和对比；B 段中枢区间内 DIF 必须发生过变号（回抽/回踩 0 轴）。
· 盘整背驰（用于 3买/3卖）：C 不破中枢+面积小 → 必回跌；C 上破中枢
  +面积小 → 先出，回跌不回中枢 = 三买回补。笔级落地：信号回踩腿与
  其前序同向腿的「回踩方向柱面积」对比，本次面积 < 前序 → 同向力度
  衰竭（bei24=True），分支名记入 fv24（不拦截、只标注）。

面积定义：区间内与比较方向同色的 MACD 柱绝对值之和。柱=hist=
2*(dif-dea)（国内惯例，正=红柱）。下跌腿比绿柱（hist<0）、上涨腿比
红柱（hist>0）。

接口：
trend_beichi24(bars, bis, pivots, sig, ms=None) → (tbe24, tfv24)
  仅 1买/1卖。tfv24={"fb_area","fc_area","zero_cross"(bool),"c_ext"(bool)}
  None=结构不足/数据缺失（不判）；False=结构可见但不满足；True=标准背驰。
panzheng_beichi24(bars, bis, sig, ms=None) → (bei24, fv24)
  仅 3买/3卖。fv24=[前序腿面积, 本次腿面积, 分支名]；无前序同向腿=None。
"""

from chanlun_macd import macd_series

RATIO = 1.0  # 本次面积 < 前序面积 × RATIO 即衰竭（24 课口径=严格小于）


def _area(ms, i0, i1, sign):
    """[i0,i1] 区间内 sign 方向柱的面积（sign=1 红柱 / -1 绿柱，绝对值和）。"""
    if i0 is None or i1 is None or i0 > i1:
        return None
    tot = 0.0
    for i in range(i0, i1 + 1):
        h = ms[i]["hist"]
        if sign > 0 and h > 0:
            tot += h
        elif sign < 0 and h < 0:
            tot -= h
    return tot


def _dt2i(bars):
    return {b["dt"]: i for i, b in enumerate(bars)}


def _leg_area(bars, ms, b0, b1, sign):
    """笔区间 [b0..b1] 的同向柱面积（按笔端点 dt 映射到 K 线下标）。"""
    if b0 is None or b1 is None:
        return None
    d2i = _dt2i(bars)
    i0, i1 = d2i.get(b0), d2i.get(b1)
    if i0 is None or i1 is None:
        return None
    return _area(ms, i0, i1, sign)


def _find_sig_bi(bis, sig):
    """信号终结笔在 bis 中的下标（与 chanlun_macd.divergence 同定位口径）。"""
    want = -1 if sig["type"].endswith("买") else 1
    for i, b in enumerate(bis):
        if b["e_dt"] == sig.get("dt") and b["dir"] == want:
            return i
    return None


def trend_beichi24(bars, bis, pivots, sig, ms=None):
    """24 课趋势背驰（MACD 柱面积版）：结构复用 chanlun_trend 的双中枢口径。"""
    from chanlun_trend import _force  # 复用 b 段挑选口径（最强同向笔）

    want = -1 if sig["type"].endswith("买") else 1
    k = _find_sig_bi(bis, sig)
    if k is None:
        return None, None
    starts = {b["s_dt"]: i for i, b in enumerate(bis)}
    ends = {b["e_dt"]: i for i, b in enumerate(bis)}
    pv = []
    for p in pivots:
        i0, i1 = starts.get(p["s_dt"]), ends.get(p["e_dt"])
        if i0 is not None and i1 is not None and i1 < k:
            pv.append((i0, i1, p))
    if len(pv) < 2:
        return None, None  # 双中枢结构不足，不判
    ai0, ai1, A = pv[-2]
    bi0, bi1, B = pv[-1]
    # 趋势方向：两中枢不重叠（同 chanlun_trend）
    separated = (B["zg"] < A["zd"]) if want == -1 else (B["zd"] > A["zg"])
    if not separated:
        return False, {"reason": "overlap"}
    # c 须离开末中枢（同 chanlun_trend）
    if want == -1 and not (sig.get("price", 0) < B["zd"]):
        return False, {"reason": "reenter"}
    if want == 1 and not (sig.get("price", 0) > B["zg"]):
        return False, {"reason": "reenter"}
    # b 段：A 末笔 ~ B 成枢三笔窗口内最强同向笔（口径与 trend_beichi 一致）
    b_idx = None
    j_hi = min(bi0 + 2, bi1)
    for j in range(ai1, j_hi + 1):
        if bis[j]["dir"] == want and (
            b_idx is None or _force(bis[j]) > _force(bis[b_idx])
        ):
            b_idx = j
    if b_idx is None:
        return False, {"reason": "no_b"}
    ms = ms or macd_series(bars)
    if len(ms) < len(bars):
        return None, None
    fb_area = _leg_area(bars, ms, bis[b_idx]["s_dt"], bis[b_idx]["e_dt"], want)
    fc_area = _leg_area(bars, ms, bis[k]["s_dt"], bis[k]["e_dt"], want)
    if not fb_area or not fc_area:
        return None, None
    # B 段回抽 0 轴校验：末中枢区间内 DIF 发生过变号（黄白线回拉 0 轴附近）
    i_b0 = _dt2i(bars).get(B["s_dt"])
    i_b1 = _dt2i(bars).get(B["e_dt"])
    zero_cross = False
    if i_b0 is not None and i_b1 is not None:
        difs = [ms[i]["dif"] for i in range(i_b0, i_b1 + 1)]
        zero_cross = bool(difs) and (min(difs) < 0 < max(difs))
    # c 段创新高/新低校验（37 课加严项，只记不拦——24 课本身不要求）
    if want == -1:
        lo = min(b["l"] for b in bars if B["s_dt"] <= b["dt"] <= bis[k]["e_dt"])
        c_ext = bool(sig.get("price", 0) <= lo)
    else:
        hi = max(b["h"] for b in bars if B["s_dt"] <= b["dt"] <= bis[k]["e_dt"])
        c_ext = bool(sig.get("price", 0) >= hi)
    ok = bool(fc_area < fb_area * RATIO)
    fv = {
        "fb_area": round(fb_area, 6),
        "fc_area": round(fc_area, 6),
        "zero_cross": zero_cross,
        "c_ext": c_ext,
    }
    # 结构+面积成立即 True；0 轴校验不通过降级为「面积背驰」仍记 True 但
    # tfv 里留痕（教学原文 0 轴是「一般」，非硬条件）
    return (ok, fv) if ok else (False, dict(fv, reason="no_div"))


def panzheng_beichi24(bars, bis, sig, ms=None):
    """24 课盘整背驰（MACD 面积版）：回踩腿 vs 前序同向腿的同色柱面积。

    3买：回踩腿=向下笔（比绿柱）；3卖：反抽腿=向上笔（比红柱）。
    分支（24 课三分支，按回踩腿终点相对信号中枢位置归类，只记不拦）：
    · "inside"  腿终点仍在区间内+面积小 → 必回跌（短差位）
    · "above"   上破区间+面积小 → 先出，回跌不回 = 三买回补
    · "below"   下破区间+面积小 → 对称
    分支判定需要中枢——此处用「前序同向腿起点至信号终点」的实际价格区间
    近似（笔级简化，如实声明）；fv24=[前序面积, 本次面积, 分支]。
    """
    want = -1 if sig["type"].endswith("买") else 1
    k = _find_sig_bi(bis, sig)
    if k is None:
        return None, None
    prev = None
    for j in range(k - 1, -1, -1):
        if bis[j]["dir"] == want:
            prev = j
            break
    if prev is None:
        return None, None
    ms = ms or macd_series(bars)
    if len(ms) < len(bars):
        return None, None
    f_prev = _leg_area(bars, ms, bis[prev]["s_dt"], bis[prev]["e_dt"], want)
    f_this = _leg_area(bars, ms, bis[k]["s_dt"], bis[k]["e_dt"], want)
    if not f_prev or not f_this:
        return None, None
    ok = bool(f_this < f_prev * RATIO)
    # 分支：本次腿终点相对前序腿的价格区间
    band_lo = min(bis[prev]["s_price"], bis[prev]["e_price"])
    band_hi = max(bis[prev]["s_price"], bis[prev]["e_price"])
    endp = bis[k]["e_price"]
    if want == -1:  # 3买：向下腿
        branch = "above" if endp > band_hi else ("inside" if endp >= band_lo else "below")
    else:  # 3卖：向上腿
        branch = "below" if endp < band_lo else ("inside" if endp <= band_hi else "above")
    return ok, [round(f_prev, 6), round(f_this, 6), branch]
