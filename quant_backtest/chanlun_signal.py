# -*- coding: utf-8 -*-
"""缠论工作台信号层 · 三类买卖点规则化（基于笔 / 线段 / 中枢）。

口径说明（查看器页面同步标注，简化口径如实声明）：
· 一类（趋势转折·动量衰减简化）：线段被确认终结（存在后续段）时，段内
  最后两根同向笔相比幅度收窄 → 线段极值记 1买（向下段）/ 1卖（向上段）；
  段内同向笔不足两根（过渡段）不产生一类信号。
· 二类（转折后首次回踩不破极值）：1买之后新向上段的第一根向下笔低点
  高于 1买价 → 该回调笔终点记 2买；1卖之后反弹笔高点低于 1卖价对称记 2卖。
· 三类（中枢离开后回踩不回区间）：中枢覆盖结束后第一根向上笔突破 ZG，
  其后第一根向下笔低点仍高于 ZG → 该低点记 3买；向下笔跌破 ZD、反弹笔
  高点仍低于 ZD 对称记 3卖。同一中枢只报首个三类信号。
  P5 盘整背驰影子标注（2026-09-27，仅标注不拦截）：三类信号附 bei 字段
  ——回踩 / 反抽笔力度弱于离开笔（BEICHI_RATIO=1.0，力度=笔端点绝对价差，
  与一类「动量衰减」同一口径）即视为盘整背驰（反向力量衰竭、离开趋势可能
  延续），fv 记录 [离开笔力度, 回踩笔力度]；评估样本中「bei=True 子集」
  与全集在可成交口径下对比，达标后再考虑启用过滤。
  P6-b 正宗盘整背驰影子标注（2026-09-27，教学复刻口径，仅标注不拦截）：
  同向力度衰减——以离开笔之前最近一根同向（与回踩笔同方向）笔为前序腿
  a（3买=前序下跌腿，3卖=前序上涨腿），本次回踩/反抽力度弱于前序同向
  腿即衰竭（更高低点/更低高点的力度表达）；fv2=[前序腿力度, 本次回踩
  笔力度]，窗口内无前序同向腿时 bei2=None。与 P5「回踩 vs 离开」的跨向
  口径并行影子、样本面板择优，不互相推翻。
  P6-a MACD 背驰影子标注（2026-09-27，仅 1买/1卖，仅标注不拦截）：
  mbei=True 表示信号笔终点价格创新低/高、但该点 DIF 较上一同向笔终点
  反向收敛（底背驰/顶背驰，简化用 DIF 极值位代替红绿柱面积），mfv 记录
  [前序端点价, 本端点价, 前序 DIF, 本笔 DIF]。
  P6-c 趋势背驰完整版影子标注（2026-09-26，仅 1买/1卖，仅标注不拦截）：
  tbe=True 表示教学 a+A+b+B+c 结构成立——信号前存在 ≥2 个同向不重叠
  中枢（后中枢 ZG/ZD 完全越过前中枢）、两中枢离开笔同向、信号价在末
  中枢趋势侧之外，且末段 c 力度弱于连接段 b（见 chanlun_trend）；
  tfv 记录中枢沿与两段力度。信号前中枢不足 2 个 tbe=None（不判），
  结构可见但不满足记 False，与 P6-a 并行影子、面板择优。
· 完结确认：二/三类信号依据的回调 / 反弹笔不能是最后一根笔（最后一笔
  终点随行情延伸可能修正），其后续笔必须已存在。

find_signals(bis, segments, pivots) 返回（按 dt 升序，同位去重）：
  [{"type", "dt", "price", "note", "bei", "fv", "bei2", "fv2"(仅3类)}]
analyze(bars) 在 chanlun_bi 结果基础上追加 "signals"，并对 1买/1卖
追加 P6-a MACD 影子标注 mbei/mfv（价创新低/高而 DIF 反向收敛，
见 chanlun_macd；无同向前序笔时 mbei=None，仅标注不拦截）。
全部信号追加 P8-a 量能共振 vv/vv_ev（终结笔均量缩于前同向笔=资金
配合，见 chanlun_flow；成交量缺失 vv=None）。
全部信号追加 P2 K线形态共振 pt/pt_name（信号日经典 K 线形态与信号
方向同向=共振，见 chanlun_pattern；OHLC 恒可判 True/False 无 None 态，
仅标注不拦截）。
大盘环境 reg/reg_ev 由调用方按市场外部附加（信号层不持有指数数据）。
P9 长周期趋势 regime reg_trend/reg_trend_ev 同由调用方按市场外部附加
（close>MA200=True 牛市 regime，见 chanlun_flow.regime_trend_at；
与 P8-b MA20/MA60 并行影子、口径不互相推翻）。
"""

from chanlun_bi import analyze as _az, _bi_h, _bi_l
from chanlun_macd import macd_series, divergence as _macd_div
from chanlun_flow import volume_confluence as _vol_cf
from chanlun_trend import trend_beichi as _trend_beichi
from chanlun_beichi24 import trend_beichi24 as _trend_beichi24, panzheng_beichi24 as _pz_beichi24

# P5：回踩/反抽笔力度 < 离开笔力度 × 该比例 → 盘整背驰（影子评估阈值，
# 未经可成交口径复评不启用拦截）
BEICHI_RATIO = 1.0


def _bi_force(b):
    """笔力度（端点绝对价差，与一类动量衰减同口径）。"""
    s, e = b.get("s_price"), b.get("e_price")
    if s is None or e is None:
        return None
    return abs(e - s)


def _panzheng_beichi(leave, pull):
    """盘整背驰判定：回踩/反抽笔力度弱于中枢离开笔。

    返回 (bei, fv)；fv=[离开笔力度, 回踩笔力度]，力度缺失时 bei=None。
    """
    v_leave, v_pull = _bi_force(leave), _bi_force(pull)
    if v_leave is None or v_pull is None:
        return None, None
    return (v_pull < v_leave * BEICHI_RATIO, [round(v_leave, 4), round(v_pull, 4)])


def _entry_leave_beichi(bis, k, pull):
    """P6-b 正宗盘整背驰：本次回踩笔 vs 离开笔之前最近同向腿的力度。

    3买时 pull 为向下回踩笔，在 bis[:k]（离开笔及之前）中向前找最近的
    下跌腿作 a；3卖上下对称。本次力度 < 前序腿即同向力度衰竭。
    返回 (bei2, fv2)，fv2=[前序腿力度, 本次回踩笔力度]；窗口内无同向
    前序腿时 bei2=None。
    """
    prev = None
    for h in range(k - 1, -1, -1):
        if bis[h]["dir"] == pull["dir"]:
            prev = bis[h]
            break
    if prev is None:
        return None, None
    v_prev, v_pull = _bi_force(prev), _bi_force(pull)
    if v_prev is None or v_pull is None:
        return None, None
    return (v_pull < v_prev * BEICHI_RATIO, [round(v_prev, 4), round(v_pull, 4)])


def _seg_range(bis, seg):
    """线段对应的笔索引区间 [i0, i1]（含）；找不到返回 (None, None)。"""
    i0 = i1 = None
    for i, b in enumerate(bis):
        if i0 is None and b["s_dt"] == seg["s_dt"]:
            i0 = i
        if b["e_dt"] == seg["e_dt"]:
            i1 = i
    return i0, i1


def _seg_signals(bis, segs):
    """一类买卖点：已确认线段极值 + 段内动量衰减（价格背驰简化）。"""
    out = []
    for si, seg in enumerate(segs):
        if si + 1 >= len(segs):
            break  # 末段随数据截断，未确认终结
        i0, i1 = _seg_range(bis, seg)
        if i0 is None or i1 is None:
            continue
        same = [k for k in range(i0, i1 + 1) if bis[k]["dir"] == seg["dir"]]
        if len(same) < 2:
            continue  # 过渡段：无动量对比基准
        last_v = abs(bis[same[-1]]["e_price"] - bis[same[-1]]["s_price"])
        prev_v = abs(bis[same[-2]]["e_price"] - bis[same[-2]]["s_price"])
        if last_v < prev_v:  # 动量衰减
            if seg["dir"] == -1:
                out.append(
                    {
                        "type": "1买",
                        "dt": seg["e_dt"],
                        "price": seg["e_price"],
                        "note": "向下线段终结·动量衰减",
                    }
                )
            else:
                out.append(
                    {
                        "type": "1卖",
                        "dt": seg["e_dt"],
                        "price": seg["e_price"],
                        "note": "向上线段终结·动量衰减",
                    }
                )
    return out


def _second_signals(bis, segs, firsts):
    """二类买卖点：一类转折后新段首次回调 / 反弹不破一类极值。"""
    if not firsts:
        return []
    n = len(bis)
    out = []
    for si, seg in enumerate(segs):
        if si == 0 or si + 1 >= len(segs):
            continue  # 首段无一类转折来源；末段未确认
        i0, _ = _seg_range(bis, seg)
        if i0 is None or i0 + 1 >= n - 1:
            continue  # 回调笔须完结（非最后一根笔）
        base = [f for f in firsts if f["dt"] == segs[si - 1]["e_dt"]]
        if not base:
            continue
        f0 = base[0]
        pull = bis[i0 + 1]  # 新段第一根回调 / 反弹笔
        if seg["dir"] == 1 and pull["dir"] == -1 and _bi_l(pull) > f0["price"]:
            out.append(
                {
                    "type": "2买",
                    "dt": pull["e_dt"],
                    "price": pull["e_price"],
                    "note": "转折后首回调不破极值",
                }
            )
        elif seg["dir"] == -1 and pull["dir"] == 1 and _bi_h(pull) < f0["price"]:
            out.append(
                {
                    "type": "2卖",
                    "dt": pull["e_dt"],
                    "price": pull["e_price"],
                    "note": "转折后首反弹不破极值",
                }
            )
    return out


def _pivot_signals(bis, pvs):
    """三类买卖点：中枢离开后回踩 / 反抽不回区间（每枢只报首个）。"""
    n = len(bis)
    out = []
    for p in pvs:
        i1 = None
        for i, b in enumerate(bis):
            if b["e_dt"] == p["e_dt"]:
                i1 = i
        if i1 is None:
            continue
        k = i1 + 1  # 中枢覆盖结束后的第一根笔
        while k < n - 2:  # 回踩笔须完结（k+1 非最后一根笔）
            b = bis[k]
            if b["dir"] == 1 and b["e_price"] > p["zg"]:
                r = bis[k + 1]
                if r["dir"] == -1 and _bi_l(r) > p["zg"]:
                    bei, fv = _panzheng_beichi(b, r)
                    bei2, fv2 = _entry_leave_beichi(bis, k, r)
                    out.append(
                        {
                            "type": "3买",
                            "dt": r["e_dt"],
                            "price": r["e_price"],
                            "zg": p["zg"],
                            "zd": p["zd"],
                            "note": "中枢上沿回踩不破",
                            "bei": bei,
                            "fv": fv,
                            "bei2": bei2,
                            "fv2": fv2,
                        }
                    )
                    break
            elif b["dir"] == -1 and b["e_price"] < p["zd"]:
                r = bis[k + 1]
                if r["dir"] == 1 and _bi_h(r) < p["zd"]:
                    bei, fv = _panzheng_beichi(b, r)
                    bei2, fv2 = _entry_leave_beichi(bis, k, r)
                    out.append(
                        {
                            "type": "3卖",
                            "dt": r["e_dt"],
                            "price": r["e_price"],
                            "note": "中枢下沿反抽不破",
                            "bei": bei,
                            "fv": fv,
                            "bei2": bei2,
                            "fv2": fv2,
                        }
                    )
                    break
            k += 1
    return out


def find_signals(bis, segments, pivots):
    """笔/线段/中枢 → 三类买卖点列表（dt 升序，同位同型去重）。"""
    firsts = _seg_signals(bis, segments)
    sigs = firsts + _second_signals(bis, segments, firsts) + _pivot_signals(bis, pivots)
    sigs.sort(key=lambda s: s["dt"])
    seen, out = set(), []
    for s in sigs:
        key = (s["dt"], s["type"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def analyze(bars):
    """信号层入口：原始升序 K 线 → 全量结构 + 三类买卖点。"""
    bars = bars or []
    st = _az(bars)
    st["signals"] = find_signals(st["bis"], st["segments"], st["pivots"])
    # P6-a：1买/1卖 附 MACD 背驰影子标注（截断重放时随 bars 自然截断，
    # 在线/离线口径自动一致）
    if st["signals"]:
        ms = macd_series(bars)
        for s in st["signals"]:
            if s["type"] in ("1买", "1卖"):
                s["mbei"], s["mfv"] = _macd_div(bars, st["bis"], s, ms)
                # P6-c：教学完整版趋势背驰（双中枢+末段力度衰减）
                s["tbe"], s["tfv"] = _trend_beichi(st["bis"], st["pivots"], s)
                # 24 课正宗趋势背驰（MACD 柱面积 + B 段 0 轴校验，2026-11-10
                # 老田批准落地）：与力度版 tbe 并行影子、不互相推翻
                try:
                    s["tbe24"], s["tfv24"] = _trend_beichi24(
                        bars, st["bis"], st["pivots"], s, ms
                    )
                except Exception:
                    s["tbe24"], s["tfv24"] = None, None
            if s["type"] in ("3买", "3卖"):
                # 24 课盘整背驰（MACD 面积版，与力度版 bei/bei2 并行）
                try:
                    s["bei24"], s["fv24"] = _pz_beichi24(bars, st["bis"], s, ms)
                except Exception:
                    s["bei24"], s["fv24"] = None, None
            # P8-a：全部信号附量能共振（缩量）影子标注
            s["vv"], s["vv_ev"] = _vol_cf(bars, st["bis"], s)
        # P2 K线形态共振影子标注（K172→K175，仅标注不拦截）：
        # pt=信号日经典K线形态与信号方向共振（买点×看涨形态 / 卖点×看跌
        # 形态，dir=0 中性十字不算），pt_name=形态名；信号日无形态或方向
        # 不合记 False（OHLC 恒可判，无 None 态）。形态只用信号日及之前
        # 的K线，无未来函数；识别失败降级 None 不影响结构信号。
        try:
            from chanlun_pattern import detect as _pat_detect

            pmap = {}
            for p in _pat_detect(bars):
                pmap.setdefault(p["dt"], p)
            for s in st["signals"]:
                want = 1 if "买" in s["type"] else -1
                p = pmap.get(s["dt"])
                s["pt"] = bool(p and p["dir"] == want)
                s["pt_name"] = p["name"] if p else None
        except Exception:
            for s in st["signals"]:
                s.setdefault("pt", None)
                s.setdefault("pt_name", None)
    return st
