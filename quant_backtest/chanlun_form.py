# -*- coding: utf-8 -*-
"""缠论形态层（结构 → 页面语言）· 2026-11-12

来源：2026-11-11 老田的 ST美克(600337) 5分钟画线图 —— 图上的语言是
「笔端点价 → 折线连笔 → 框出中枢 → 水平线标沿」。本模块把 chanlun_bi 的
笔/中枢翻成同一套语言，只是级别换成日线。

为什么独立成模块（原在 ledger_form.py，2026-11-12 上移）：
形态是**结构层属性**，不是账本的私有产物。market_scan 出信号时手里已经有
analyze() 的结果，直接复用即可零成本落盘；账本只负责展示。若形态只存在于
账本侧，则每次重建都要把每只票的结构再算一遍——这就是构建时间翻倍的来源。

两个入口，口径必须逐位一致：
    form_at(bars, day, ...)           自己算结构（离线/补算用）
    form_from_struct(st, cur, ...)    复用现成结构（market_scan 用，零成本）

关键口径（踩过的坑，勿改回）：
  · 一律以**选择日 day** 为截止自算，不用信号里的 zg/zd。sig 的 zg/zd 是
    结构日 dt 的价位，与 day 中位差 33 天、最长 304 天，混用必然自相矛盾
    （601238.SH：结构中枢 7.72 而选择日收盘 4.88）。
  · 框取**最后一个**中枢，位置由现价与 zg/zd 直接比较：up ⟺ pos>0。
    曾按"中枢结束后第一笔方向"定 leave，结果 177 笔 up 组 pos 全 ≤0，
    "向上离开"却在上沿之下，语义自毁。
  · 绘图与形态共用**同一份结构**。曾用 60 根单独画图、250 根算形态，
    实测 56% 的票图文指向不同中枢（文字说[13.25,11.35]，图上画[11.85,11.35]）。
"""
import pandas as pd

N = 250       # 结构窗口（与 market_scan.WARMUP 同口径）
CHART_ND = 60  # 绘图窗口（图上显示的 K 线根数）
MIN_BARS = 60  # 结构最少 K 线数


def _dts(v):
    """日期统一成字符串：market_scan 侧是 int，账本侧是 str，落盘只留 str。"""
    if v is None:
        return None
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return str(v)


def pick_pivot(pivots):
    """取最后一个已完成中枢（图里最后画出的那个框）。见模块头注。"""
    return pivots[-1] if pivots else None


def scan_meta(day, price, dt, cur):
    """扫描器口径对照：结构日 dt、滞后天数 gap、买点偏离 dev。

    单独抽出，是因为 market_scan 侧一天只算一次结构（form 主体），但每条信号
    的 gap/dev 不同（dt 与 price 是信号级的），必须能只补这两个字段。
    """
    out = {}
    if dt and day:
        try:
            out["gap"] = (pd.Timestamp(str(day)) - pd.Timestamp(str(dt))).days
            if price:
                out["s_price"] = round(float(price), 2)
                out["dev"] = round((float(cur) / float(price) - 1) * 100, 1)
        except (ValueError, TypeError):
            pass
    return out


def form_from_struct(st, cur, day=None, price=None, zg=None, zd=None, dt=None,
                     nbars=None):
    """结构 → 形态 dict。st 需含 bis / pivots（chanlun_bi.analyze 的返回）。

    cur   现价（= 截止 day 的最后一根收盘）
    price/zg/zd/dt  扫描器口径（结构日 dt 的价），仅作对照，不参与形态计算
    nbars 结构窗口内的 K 线根数（不给则跳过"K线不足"判定）
    """
    st = st or {}
    bis = st.get("bis") or []
    pivots = st.get("pivots") or []
    if nbars is not None and nbars < MIN_BARS:
        return {"ok": False, "why": "K线不足"}
    if not bis:
        return {"ok": False, "why": "无笔"}

    cur = float(cur)
    tail = bis[-5:]
    chain = [round(tail[0]["s_price"], 2)] + [round(b["e_price"], 2) for b in tail]
    out = {"ok": True, "chain": chain, "n_bi": len(bis),
           "n_pivot": len(pivots), "cur": round(cur, 2)}

    p = pick_pivot(pivots)
    if p:
        after = [b for b in bis if b["s_dt"] >= p["e_dt"]]
        # 离开方向 = 现价相对中枢框的位置（图上的语言），up ⟺ pos>0
        leave = "up" if cur > p["zg"] else ("down" if cur < p["zd"] else "in")
        out.update({
            "zg": round(p["zg"], 2), "zd": round(p["zd"], 2),
            "width": round((p["zg"] / p["zd"] - 1) * 100, 1),
            "pos": round((cur / p["zg"] - 1) * 100, 1),
            "pidx": pivots.index(p) + 1, "npv": len(pivots),
            "p_n_bi": p["n_bi"],
            "p_s": _dts(p["s_dt"]), "p_e": _dts(p["e_dt"]),
            "nbi_after": len(after), "leave": leave,
        })

    # 扫描器口径对照：结构日 dt、滞后天数、买点偏离
    out.update(scan_meta(day, price, dt, cur))
    return out


def form_at(bars, day, price=None, zg=None, zd=None, dt=None):
    """截止 day 自算结构 → 形态（离线/补算入口）。"""
    from chanlun_bi import analyze

    if not bars:
        return {"ok": False, "why": "无K线"}
    seq = [b for b in bars if str(b["dt"]) <= str(day)][-N:]
    if len(seq) < MIN_BARS:
        return {"ok": False, "why": "K线不足"}
    st = analyze(seq, [b["c"] for b in seq])
    return form_from_struct(st, seq[-1]["c"], day, price, zg, zd, dt,
                            nbars=len(seq))


def chart_from_struct(st, seq, nd=CHART_ND):
    """结构 + 显示窗口 → 绘图数据（前端 SVG 用）。

    返回 {"k":[[o,h,l,c]...], "b":[[i0,i1,p0,p1]...], "p":[[i0,i1,zg,zd]...],
          "d":[首日, 末日]}，笔/中枢端点用 K 线下标表示（省体积、免对日期）。

    与 form_from_struct 吃同一份 st：文字说哪个中枢，图上就画哪个框。
    为此显示窗口会按需向后延长（最多 nd+30 根）——固定 60 根时实测有票的
    中枢整个落在窗外，页面变成「文字讲中枢②、图上空无一框」。中枢比窗口
    更宽时画半截框（从图左缘起），框的右端始终在图内。

    只画完整落在窗内的笔（不贴边截断）：截断的半根笔端点价取的是窗外的价，
    会画到框外去，看着像异常尖刺。窗内没有笔就不画，不糊。
    """
    if not st or not seq:
        return None
    pvs_all = st.get("pivots") or []
    start = max(0, len(seq) - nd)
    if pvs_all:
        idx = {str(b["dt"]): i for i, b in enumerate(seq)}
        p = pvs_all[-1]
        pe, ps = idx.get(str(p["e_dt"])), idx.get(str(p["s_dt"]))
        if pe is not None:
            start = min(start, max(0, pe - 2))  # 终点必须进窗，留 2 根余量
            if ps is not None and (pe - ps) <= nd + 28:
                start = min(start, ps)          # 中枢不宽就整只框进来
        start = max(start, len(seq) - (nd + 30))  # 但窗口最多 nd+30 根
    win = seq[max(0, start):]
    if len(win) < 30:
        return None
    pos = {str(b["dt"]): i for i, b in enumerate(win)}

    ks = [[round(b["o"], 2), round(b["h"], 2), round(b["l"], 2),
           round(b["c"], 2)] for b in win]
    bis = []
    for b in st.get("bis") or []:
        i0, i1 = pos.get(str(b["s_dt"])), pos.get(str(b["e_dt"]))
        if i0 is None or i1 is None or i1 <= i0:
            continue
        bis.append([i0, i1, round(b["s_price"], 2), round(b["e_price"], 2)])
    pvs = []
    for p in pvs_all:
        i1 = pos.get(str(p["e_dt"]))
        if i1 is None:
            continue
        i0 = pos.get(str(p["s_dt"]))
        if i0 is None:
            i0 = 0  # 中枢起点在图左之外：半截框，从左边沿起画
        if i1 <= i0:
            continue
        pvs.append([i0, i1, round(p["zg"], 2), round(p["zd"], 2)])
    return {"k": ks, "b": bis, "p": pvs,
            "d": [_dts(win[0]["dt"]), _dts(win[-1]["dt"])]}
