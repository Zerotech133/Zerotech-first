# -*- coding: utf-8 -*-
"""缠论工作台 P6-c 趋势背驰完整版 · 教学 a+A+b+B+c 口径（影子标注）。

此前 1买/1卖 只做「单线段内两根同向笔力度收窄」的简化判定，不含趋势
所必需的中枢计数。本模块按教学定义补全，仅对 1买/1卖 附加 tbe/tfv，
只标注不拦截，与 P6-a MACD 背驰并行影子、面板择优。

教学定义（缠论 17/29 课，本工作台的简化如实声明）：
· 趋势 = 至少两个同向、依次排列且互不重叠的同级别中枢；趋势方向由
  两中枢的位置关系决定，不看离开笔瞬时方向（中枢可先反向假离开再被
  新中枢越过）：下跌趋势 B.zg < A.zd（后中枢整个区间在前中枢之下），
  上涨对称 B.zd > A.zg。严格教学按 GG/DD 判，本库笔中枢只存 ZG/ZD，
  故用区间沿判定。
· 趋势背驰 = a+A+b+B+c 中，离开末中枢 B 的末段 c 力度弱于连接前中枢
  A 到 B 的 b 段。笔级别简化：c=信号终结笔（其终点须在 B 的趋势侧
  之外，未折返区间）；b 在「A 末笔（贯穿中枢的离开笔会被 find_pivots
  记为中枢末笔）~ B 成枢三笔」窗口内取最强的趋势同向笔（干净趋势里即
  A 的离开笔本身，假离开场景取真正的推动腿）；力度=笔端点绝对价差，
  与 P5/P6 同口径，阈值 TRENDBEI_RATIO=1.0。

trend_beichi(bis, pivots, sig) → (tbe, tfv)
  tfv={"np":信号前已终结中枢数, "A":[zg,zd], "B":[zg,zd],
       "fb":b段力度, "fc":c段力度}（False 时附 reason）
  None = 信号笔找不到 / 信号前已终结中枢不足 2 个 / 力度数据缺失
         （数据不足不判，不计 False）；
  False= 结构可见但趋势背驰不成立（两中枢重叠/信号价折返区间/
  窗口内无同向 b 段/力度未衰减）；
  True = ≥2 同向不重叠中枢 + 末段力度衰减。
"""

TRENDBEI_RATIO = 1.0


def _force(b):
    """笔力度（端点绝对价差，与 chanlun_signal._bi_force 同口径）。"""
    s, e = b.get("s_price"), b.get("e_price")
    if s is None or e is None:
        return None
    return abs(e - s)


def trend_beichi(bis, pivots, sig):
    want = -1 if sig["type"].endswith("买") else 1     # 下跌趋势/上涨趋势
    # 信号终结笔（与 chanlun_macd.divergence 同定位口径）
    k = None
    for i, b in enumerate(bis):
        if b["e_dt"] == sig.get("dt") and b["dir"] == want:
            k = i
            break
    if k is None:
        return None, None
    # 中枢映射到笔索引，只保留信号笔之前已终结的（c 允许就是 B 离开笔）
    starts = {b["s_dt"]: i for i, b in enumerate(bis)}
    ends = {b["e_dt"]: i for i, b in enumerate(bis)}
    pv = []
    for p in pivots:
        i0, i1 = starts.get(p["s_dt"]), ends.get(p["e_dt"])
        if i0 is not None and i1 is not None and i1 < k:
            pv.append((i0, i1, p))
    if len(pv) < 2:
        return None, None                    # 窗口内趋势结构不足，不判
    ai0, ai1, A = pv[-2]                     # 相邻的前中枢
    bi0, bi1, B = pv[-1]                     # 末中枢（信号笔前最后一个）
    ev0 = {"np": len(pv)}
    # 趋势方向：两中枢区间须完全不重叠、依次沿趋势方向排列
    if want == -1:
        separated = B["zg"] < A["zd"]
    else:
        separated = B["zd"] > A["zg"]
    if not separated:
        return False, dict(ev0, reason="overlap")
    # 信号价须在末中枢趋势侧之外（c 确实离开 B，未折返区间）
    if want == -1 and not (sig.get("price", 0) < B["zd"]):
        return False, dict(ev0, reason="reenter")
    if want == 1 and not (sig.get("price", 0) > B["zg"]):
        return False, dict(ev0, reason="reenter")
    # b 段：A 末笔起（find_pivots 按区间重叠延伸，贯穿中枢的离开笔会被
    # 记为中枢末笔）、至 B 成枢三笔窗口内，取最强的趋势同向笔
    b_idx = None
    j_hi = min(bi0 + 2, bi1)                 # B 的前三笔（中枢成枢段）
    for j in range(ai1, j_hi + 1):
        if bis[j]["dir"] == want and (
                b_idx is None or _force(bis[j]) > _force(bis[b_idx])):
            b_idx = j
    if b_idx is None:
        return False, dict(ev0, reason="no_b")
    fb, fc = _force(bis[b_idx]), _force(bis[k])
    if fb is None or fc is None:
        return None, None
    return (fc < fb * TRENDBEI_RATIO,
            {"np": len(pv),
             "A": [round(A["zg"], 4), round(A["zd"], 4)],
             "B": [round(B["zg"], 4), round(B["zd"], 4)],
             "fb": round(fb, 4), "fc": round(fc, 4)})
