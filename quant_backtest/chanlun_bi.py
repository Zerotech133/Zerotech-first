# -*- coding: utf-8 -*-
"""缠论工作台 P3 结构识别 · 第二层：笔 / 线段 / 中枢（基于 P2 分型）。

口径说明（查看器页面同步标注）：
· 笔（新笔口径）：相邻异性分型相连，两分型中点的处理后 K 线索引差 >= 4
  （即一笔至少 5 根处理后 K 线）；同性分型出现时保留更极值者（已确认笔
  的终点随之修正延伸）；跨度不足的中间分型直接弃用，等待更远的异性分型。
· 线段（破坏确认法简化口径）：首段起点取最早「前三笔区间有公共重叠」
  处；终结用两步确认——反向笔破线段内前一同向笔极值成候选，其后第一个
  同向笔不创线段新极值则确认终结于线段极值（创则解除）；终结点必为线段
  极值，首笔即极值时允许不足三笔的过渡段，末段随数据截断。
· 中枢（笔中枢）：连续三笔的高低点区间重叠确定 [ZD, ZG]（ZD=三笔低点
  最高、ZG=三笔高点最低）；后续笔与区间有重叠则延伸（区间不变），某笔
  完全离开区间则中枢终结，该离开笔作为下一中枢的候选起点。

analyze(bars) 在 P2 结果基础上追加（所有 dt 均为分型 dt，前端可定位）：
  "bis":      [{"s_dt","e_dt","s_price","e_price","dir"}]       dir 1=上笔
  "segments": [{"s_dt","e_dt","s_price","e_price","dir","n_bi"}]
  "pivots":   [{"s_dt","e_dt","zg","zd","n_bi"}]
"""

SIGNALS_DEPRECATED = True  # True=本模块 signals 为对照口径，勿用于生产，权威口径见 chanlun_signal

from chanlun_fractal import _merge_inclusion as _mi, find_fractals as _ff

BI_MIN_GAP = 4  # 笔最小跨度：两分型中点处理后索引差（>=4 即至少 5 根）


def find_bis(fractals, merged):
    """分型序列 → 笔列表（方向交替、首尾相接）。"""
    idx = {m["dt"]: i for i, m in enumerate(merged)}
    pts = [f for f in fractals if f["dt"] in idx]
    if len(pts) < 2:
        return []
    bis = []
    pend = pts[0]
    for f in pts[1:]:
        if f["type"] == pend["type"]:
            # 同性分型：保留更极值（顶取更高、底取更低）；若已有笔终结于
            # pend，同步修正上一笔终点（行情延伸，笔随极值修正）
            if (f["type"] == "顶" and f["price"] > pend["price"]) or (
                f["type"] == "底" and f["price"] < pend["price"]
            ):
                if bis and bis[-1]["e_dt"] == pend["dt"]:
                    bis[-1]["e_dt"] = f["dt"]
                    bis[-1]["e_price"] = f["price"]
                pend = f
            continue
        if idx[f["dt"]] - idx[pend["dt"]] >= BI_MIN_GAP:
            bis.append(
                {
                    "s_dt": pend["dt"],
                    "e_dt": f["dt"],
                    "s_price": pend["price"],
                    "e_price": f["price"],
                    "dir": 1 if f["type"] == "顶" else -1,
                }
            )
            pend = f
        # 跨度不足：该分型不成笔，弃用（pend 不变，等更远的异性分型）
    return bis


def _bi_h(b):
    return max(b["s_price"], b["e_price"])


def _bi_l(b):
    return min(b["s_price"], b["e_price"])


def find_segments(bis):
    """笔序列 → 线段列表（破坏确认法，简化口径）。

    首段起点 = 最早出现「前三笔高低点区间有公共重叠」处（线段至少三笔
    且前三笔重叠；数据起点落在行情中段时据此跳过不成段的开头）。
    终结两步确认：向上线段中，向下笔终点跌破线段内前一个向下笔终点 →
    破坏候选；其后的第一个向上笔若不创线段新高 → 确认终结于线段最高点
    （创新高则解除候选）。向下线段对称。终结点必为线段极值；首笔即极值
    时会出现不足三笔的过渡段（如实呈现）；末段随数据截断终结于当前极值。
    """
    n = len(bis)
    if n < 3:
        return []
    start = None
    for i in range(n - 2):
        if max(_bi_l(bis[i]), _bi_l(bis[i + 1]), _bi_l(bis[i + 2])) <= min(
            _bi_h(bis[i]), _bi_h(bis[i + 1]), _bi_h(bis[i + 2])
        ):
            start = i
            break
    if start is None:
        return []
    segs = []
    i = start
    while i < n - 1:
        up = bis[i]["dir"] == 1
        ext_v, ext_k = bis[i]["e_price"], i  # 线段极值（同向笔终点）
        prev_opp = None  # 前一个反向笔终点
        pending = None  # 破坏候选笔索引
        end_k = None
        k = i + 1
        while k < n:
            b = bis[k]
            if b["dir"] == (1 if up else -1):
                new_ext = (up and b["e_price"] > ext_v) or (
                    not up and b["e_price"] < ext_v
                )
                if pending is not None:
                    if new_ext:
                        pending = None  # 解除：创线段新极值
                    else:
                        end_k = ext_k  # 确认：未创极值
                        break
                if new_ext:
                    ext_v, ext_k = b["e_price"], k
            else:
                if prev_opp is not None and (
                    (up and b["e_price"] < prev_opp)
                    or (not up and b["e_price"] > prev_opp)
                ):
                    pending = k  # 破坏候选
                prev_opp = b["e_price"]
            k += 1
        if end_k is not None:
            segs.append(
                {
                    "s_dt": bis[i]["s_dt"],
                    "s_price": bis[i]["s_price"],
                    "e_dt": bis[end_k]["e_dt"],
                    "e_price": bis[end_k]["e_price"],
                    "dir": 1 if up else -1,
                    "n_bi": end_k - i + 1,
                }
            )
            i = end_k + 1
        else:
            segs.append(
                {
                    "s_dt": bis[i]["s_dt"],
                    "s_price": bis[i]["s_price"],
                    "e_dt": bis[ext_k]["e_dt"],
                    "e_price": bis[ext_k]["e_price"],
                    "dir": 1 if up else -1,
                    "n_bi": n - i,
                }
            )
            i = n
    return segs


def find_pivots(bis):
    """笔序列 → 笔中枢列表。"""
    n = len(bis)
    if n < 3:
        return []
    out = []
    i = 0
    while i + 2 < n:
        zg = min(_bi_h(bis[i]), _bi_h(bis[i + 1]), _bi_h(bis[i + 2]))
        zd = max(_bi_l(bis[i]), _bi_l(bis[i + 1]), _bi_l(bis[i + 2]))
        if zg > zd:
            end_j = i + 2
            j = i + 3
            while j < n:
                if _bi_l(bis[j]) <= zg and _bi_h(bis[j]) >= zd:
                    end_j = j
                    j += 1
                else:
                    break
            out.append(
                {
                    "s_dt": bis[i]["s_dt"],
                    "e_dt": bis[end_j]["e_dt"],
                    "zg": zg,
                    "zd": zd,
                    "n_bi": end_j - i + 1,
                }
            )
            i = j  # 离开笔作为下一中枢候选起点
        else:
            i += 1
    return out


def _macd_hist(closes):
    """MACD 柱序列（与页面 _macd 同参：EMA12/26/DEA9，柱=2*(DIF-DEA)）。"""
    e12 = e26 = dea = None
    out = []
    for c in closes:
        e12 = c if e12 is None else e12 * 11 / 13 + c * 2 / 13
        e26 = c if e26 is None else e26 * 25 / 27 + c * 2 / 27
        dif = e12 - e26
        dea = dif if dea is None else dea * 0.8 + dif * 0.2
        out.append(2 * (dif - dea))
    return out


def _macd_area(macd, s_dt, e_dt):
    """笔区间内 MACD 柱面积 Σ|柱|（dt 字典序=时间序，闭区间含端点）。"""
    return sum(abs(v) for d, v in macd.items() if s_dt <= d <= e_dt)


def find_signals(bis, pivots, macd=None):
    """【废弃口径 · 禁止用于生产/回测】

    ★ 本函数输出的 signals 为对照口径（research-only），不可用于生产策略或
      回测结论。权威买卖点口径见 chanlun_signal.find_signals，全仓所有
      .signals 消费点均走 chanlun_signal，无一消费本函数。
    ★ 实测（100 只票）：与 chanlun_signal.find_signals 的整体 Jaccard 一致率
      仅 5.4%（1卖最差 0.8%），零只票完全一致；逐日截断重放（20 只 ×
      14,961 日）显示 44% 的信号在截断后消失——依赖随行情修正的笔终点与
      MACD 面积，属前视幻觉（look-ahead），没有任何一条信号能在自己的 dt
      当天被在线看到。
    ★ 唯一正确用法：chanlun_bi.analyze 的 bis / pivots / segments 是结构层
      权威（chanlun_signal 亦直接复用同一份），结构可用，signals 不要用。
    ★ 审计依据：deliverables/2026-11-05_三线并行_v6_口径审计与跨市场判定.md

    三类买卖点（简化判据，页面口径同步标注「简化」）：

    结构口径（K153 三轮实证定案）：中枢终点笔 bis[k-1] 即破沿离开笔
    （起点在区间内被 find_pivots 计入中枢、终点破沿）；bis[k]（首支与
    区间不重叠笔，s_dt==中枢终点）即离开后回抽/反抽笔。
    · 1 类 = 离开笔背驰：破沿（买:终点<ZD / 卖:终点>ZG）且背驰——
      macd 传入时为「笔幅衰减 OR MACD 面积萎缩」（K153 增强，页面
      _macd 同参；macd={dt:柱值}），否则退化为幅衰减；
      sub 子类（K154 标注，不拦截）：tbe=趋势背驰（紧邻前序中枢与
      当前中枢同向顺序排列且无重叠——向下趋势 ZG<prev.ZD / 向上趋势
      ZD>prev.ZG——后的离开段背驰），pbe=盘整背驰（单中枢口径）；
    · 2 类 = 1 类确认后第一个同向笔（k+1，与离开笔同向）不创 1 类极值；
    · 3 类 = 回抽/反抽笔极值不回区间（向下回抽低点>ZG=买3 / 向上反抽
      高点<ZD=卖3）——笔中枢「不重叠即终结」机制的天然镜像。
    挂点均在笔终点；输出按 dt 升序。t 值 b1/b2/b3/s1/s2/s3。
    """
    out = []
    if not bis:
        return out
    idx = {b["s_dt"]: i for i, b in enumerate(bis)}
    for pi, pv in enumerate(pivots):
        k = idx.get(pv["e_dt"])
        if k is None:
            continue
        lv = bis[k - 1] if k >= 1 else None  # 离开笔（中枢终点笔）
        rj = bis[k]  # 回抽/反抽笔
        # 1 类：离开笔背驰（破沿 + 幅衰减或 MACD 面积萎缩）
        if lv is not None:
            broke = (lv["dir"] == -1 and lv["e_price"] < pv["zd"]) or (
                lv["dir"] == 1 and lv["e_price"] > pv["zg"]
            )
            last_same = None  # 中枢内离开笔之外最后同向笔
            for j in range(k - 2, -1, -1):
                if bis[j]["e_dt"] > pv["e_dt"]:
                    break
                if bis[j]["dir"] == lv["dir"]:
                    last_same = bis[j]
                    break
            div = False
            if broke and last_same is not None:
                if macd is not None:
                    # K153：MACD 面积背驰 OR 幅衰减（灵敏度增强）
                    div = (
                        _macd_area(macd, lv["s_dt"], lv["e_dt"])
                        < _macd_area(macd, last_same["s_dt"], last_same["e_dt"])
                    ) or abs(lv["e_price"] - lv["s_price"]) < abs(
                        last_same["e_price"] - last_same["s_price"]
                    )
                else:
                    div = abs(lv["e_price"] - lv["s_price"]) < abs(
                        last_same["e_price"] - last_same["s_price"]
                    )
            if broke and div:
                # K154 sub 子类标注：趋势背驰=紧邻前序中枢与当前中枢
                # 同向顺序排列且无重叠（向下趋势 ZG<prev.ZD / 向上 ZD>prev.ZG）
                sub = "pbe"
                if pi >= 1:
                    pv0 = pivots[pi - 1]
                    if (lv["dir"] == -1 and pv["zg"] < pv0["zd"]) or (
                        lv["dir"] == 1 and pv["zd"] > pv0["zg"]
                    ):
                        sub = "tbe"
                out.append(
                    {
                        "d": lv["e_dt"],
                        "p": lv["e_price"],
                        "t": "b1" if lv["dir"] == -1 else "s1",
                        "sub": sub,
                    }
                )
                # 2 类：1 类后第一个同向笔（k 反向、k+1 同向）不创新极值
                if k + 1 < len(bis) and bis[k + 1]["dir"] == lv["dir"]:
                    r2 = bis[k + 1]
                    if lv["dir"] == -1 and r2["e_price"] > lv["e_price"]:
                        out.append({"d": r2["e_dt"], "p": r2["e_price"], "t": "b2"})
                    elif lv["dir"] == 1 and r2["e_price"] < lv["e_price"]:
                        out.append({"d": r2["e_dt"], "p": r2["e_price"], "t": "s2"})
        # 3 类：回抽/反抽笔极值不回区间（中枢终结机制天然镜像）
        if rj["dir"] == -1 and rj["e_price"] > pv["zg"]:
            out.append({"d": rj["e_dt"], "p": rj["e_price"], "t": "b3"})
        elif rj["dir"] == 1 and rj["e_price"] < pv["zd"]:
            out.append({"d": rj["e_dt"], "p": rj["e_price"], "t": "s3"})
    out.sort(key=lambda x: x["d"])
    return out


def analyze(bars, closes=None):
    """P3 入口：原始升序 K 线 → 分型 + 笔 + 线段 + 中枢 + 三类买卖点。

    closes（可选）：与 bars 等长对齐的收盘价序列——传入时 1 类买卖点
    背驰判据升级为「幅衰减 OR MACD 面积萎缩」（K153），None 退化 K151。
    """
    merged = _mi(bars or [])
    frs = _ff(merged)
    bis = find_bis(frs, merged)
    pivots = find_pivots(bis)
    macd = None
    if closes is not None and len(closes) == len(bars or []):
        macd = dict(zip((b["dt"] for b in bars), _macd_hist(closes)))
    return {
        "merged_n": len(merged),
        "fractals": frs,
        "top_n": sum(1 for x in frs if x["type"] == "顶"),
        "bottom_n": sum(1 for x in frs if x["type"] == "底"),
        "bis": bis,
        "segments": find_segments(bis),
        "pivots": pivots,
        "signals": find_signals(bis, pivots, macd),
    }
