# -*- coding: utf-8 -*-
"""缠论灰度层 v4 · 中枢演化 + 背驰双口径 + 置信度融合（影子入图）。

纪律红线（K21 v4 定义，INTEL_RD_PLAN L788）：
· 图写入仅走 cl1_sync 灰度——本模块只产出字段，不改信号触发/拦截；
· 推演卡纯只读——chanlun_card / chanlun_plot / chanlun_position 不调本模块；
· 影子口径连续性优先于算法先进性——不动 chanlun_signal.analyze 主管线，
  只在其产出的 signals 上附加字段（与 mbei/tbe/vv 同风格就地标注）。

三件灰度（缺数据记 None 不判，三态 True/False/None）：
1. div_area / fv_area —— MACD 面积背驰（Chan-Compass 范式）：
   信号笔区间红绿柱面积（同符号和，下跌笔取负柱、上涨笔取正柱）
   vs 上一同向笔区间面积；价格创新低/高且面积萎缩 → True。
   fv_area=[前序面积, 本笔面积]（证据留 jsonl，不入图）。
2. div_dif / fv_dif —— DIF 极值背驰（与 mbei 端点值口径并行择优）：
   信号笔区间 DIF 极值（下跌笔取最小 / 上涨笔取最大）vs 前序同向笔
   区间极值；价格创新低/高而极值收敛 → True。fv_dif=[前序极值, 本笔极值]。
3. pv_state / pv_dir / pv_fv —— 中枢演化状态机（第29课）：
   信号前最近两个已完成中枢 P1→P2 的 ZG/ZD 关系：
   · P2.zd > P1.zg（整体上移）→ state=extend, dir=up（趋势延伸）
   · P2.zg < P1.zd（整体下移）→ state=extend, dir=dn
   · 区间重叠            → state=turn, dir=None（原趋势衰竭、转折风险）
   不足两个中枢记 None。pv_fv=[p1_zg, p1_zd, p2_zg, p2_zd]。
4. conf —— 置信度融合（ChanStock 范式，夹取 [0.10, 0.95]）：
   基础 0.50；背驰任一 True（mbei / div_area / div_dif）+0.15；
   量能共振 vv=True +0.10；信号前中枢数 ≥3 ×0.85（趋势成熟末期左尾
   加深先例：tgraph 3+枢分组含灾难尾）；因子缺失跳过该项不增不减。

信号笔定位（与 chanlun_macd.divergence 同法，全类型通用）：
want_dir = -1 if type.endswith("买") else 1——1/2/3 类买点 dt 均落在
向下笔终点（段末下跌笔 / 转折后首回调笔 / 中枢上沿回踩笔），卖类对称。

用法（pool 生成端挂点，见 chanlun_scan.scan_all）：
  from chanlun_gray import enrich
  st = analyze(bars)
  enrich(bars, st)     # 就地给 st["signals"] 附加灰度字段，返回标注数

CLI 自测：
  python chanlun_gray.py 600337.SH       # zhice-db 取数 → 全信号灰度表
"""
import sys

from chanlun_macd import macd_series


# ── 信号笔定位 ────────────────────────────────────────────
def _locate(bis, sig):
    """信号笔 + 前序同向笔的索引；找不到返回 (None, None)。"""
    want = -1 if sig["type"].endswith("买") else 1
    k = None
    for i, b in enumerate(bis):
        if b["e_dt"] == sig.get("dt") and b["dir"] == want:
            k = i
            break
    if k is None:
        return None, None
    j = None
    for h in range(k - 1, -1, -1):
        if bis[h]["dir"] == want:
            j = h
            break
    return k, j


def _hist_area(macd, i_s, i_e, dir_):
    """笔区间 MACD 柱面积：下跌笔取负柱和绝对值，上涨笔取正柱和。"""
    seg = macd[i_s:i_e + 1]
    if dir_ == -1:
        return -sum(m["hist"] for m in seg if m["hist"] < 0)
    return sum(m["hist"] for m in seg if m["hist"] > 0)


def _dif_ext(macd, i_s, i_e, dir_):
    """笔区间 DIF 极值：下跌笔取最小（最负），上涨笔取最大。"""
    seg = [m["dif"] for m in macd[i_s:i_e + 1]]
    if not seg:
        return None
    return min(seg) if dir_ == -1 else max(seg)


# ── ①② 背驰双口径 ─────────────────────────────────────────
def _div_dual(bars, bis, sig, macd):
    """面积 + DIF 极值双口径背驰。

    返回 (div_area, fv_area, div_dif, fv_dif)；无同向前序笔 / 端点
    映射失败 → 全 None（不可判）。
    """
    k, j = _locate(bis, sig)
    if k is None or j is None:
        return None, None, None, None
    dt2i = {b["dt"]: i for i, b in enumerate(bars)}
    ks, ke = dt2i.get(bis[k]["s_dt"]), dt2i.get(bis[k]["e_dt"])
    js, je = dt2i.get(bis[j]["s_dt"]), dt2i.get(bis[j]["e_dt"])
    if None in (ks, ke, js, je) or ke <= ks or je <= js:
        return None, None, None, None
    want = bis[k]["dir"]
    px_k, px_j = bis[k]["e_price"], bis[j]["e_price"]
    new_ext = px_k < px_j if want == -1 else px_k > px_j   # 价格创新低/高
    div_area, fv_area = None, None
    a_j, a_k = _hist_area(macd, js, je, want), _hist_area(macd, ks, ke, want)
    if a_j > 0 and a_k > 0:
        div_area = bool(new_ext and a_k < a_j)             # 面积萎缩
        fv_area = [round(a_j, 4), round(a_k, 4)]
    div_dif, fv_dif = None, None
    d_j, d_k = _dif_ext(macd, js, je, want), _dif_ext(macd, ks, ke, want)
    if d_j is not None and d_k is not None:
        conv = d_k > d_j if want == -1 else d_k < d_j      # 极值收敛
        div_dif = bool(new_ext and conv)
        fv_dif = [round(d_j, 4), round(d_k, 4)]
    return div_area, fv_area, div_dif, fv_dif


# ── ③ 中枢演化状态机（第29课） ─────────────────────────────
def _prior_pivots(pivots, sig):
    """信号日之前已完成的中枢（e_dt ≤ 信号 dt，升序）。"""
    return [p for p in pivots if p["e_dt"] <= (sig.get("dt") or "")]


def _pivot_evol(prior):
    """相邻两枢 P1→P2 的 ZG/ZD 关系 → (state, dir, fv)。

    state: extend（新区间整体离开旧区间，趋势延伸）/ turn（区间重叠，
    原趋势衰竭）；不足两枢返回 (None, None, None)。
    """
    if len(prior) < 2:
        return None, None, None
    p1, p2 = prior[-2], prior[-1]
    fv = [p1["zg"], p1["zd"], p2["zg"], p2["zd"]]
    if p2["zd"] > p1["zg"]:
        return "extend", "up", fv
    if p2["zg"] < p1["zd"]:
        return "extend", "dn", fv
    return "turn", None, fv


# ── ④ 置信度融合 ──────────────────────────────────────────
def _conf(sig, n_prior):
    """ChanStock 范式置信度：背驰+0.15 / 共振+0.10 / 多枢×0.85。"""
    c = 0.50
    if any(sig.get(x) is True for x in ("mbei", "div_area", "div_dif")):
        c += 0.15
    if sig.get("vv") is True:
        c += 0.10
    if n_prior is not None and n_prior >= 3:
        c *= 0.85
    return round(min(max(c, 0.10), 0.95), 3)


# ── 主入口 ────────────────────────────────────────────────
def enrich(bars, st):
    """就地给 st["signals"] 附加灰度字段；返回标注信号数。

    macd_series 只算一次（逐信号复用）；任何单信号异常吞掉记 None，
    不影响其余信号与调用方主流程（灰度失败不阻断扫描）。
    """
    sigs = st.get("signals") or []
    if not sigs:
        return 0
    macd = macd_series(bars or [])
    n = 0
    for s in sigs:
        try:
            s["div_area"], s["fv_area"], s["div_dif"], s["fv_dif"] = \
                _div_dual(bars, st["bis"], s, macd)
            prior = _prior_pivots(st["pivots"], s)
            s["pv_state"], s["pv_dir"], s["pv_fv"] = _pivot_evol(prior)
            s["conf"] = _conf(s, len(prior))
            s["n_prior"] = len(prior)      # S 批：mls 特征直用（原仅回填算）
        except Exception:
            s["div_area"] = s["fv_area"] = s["div_dif"] = s["fv_dif"] = None
            s["pv_state"] = s["pv_dir"] = s["pv_fv"] = None
            s["conf"] = None
            s["n_prior"] = None
        n += 1
    return n


# ── CLI 自测 ──────────────────────────────────────────────
if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "600337.SH"
    from chanlun_data import load_kline
    from chanlun_signal import analyze
    r = load_kline(code, "1d", 500)
    if not r.get("ok"):
        raise SystemExit("load_kline failed: %s" % r)
    bars = r["bars"]
    st = analyze(bars)
    n = enrich(bars, st)
    print("=== %s 灰度层（bars=%d bis=%d pivots=%d signals=%d enrich=%d）==="
          % (code, len(bars), len(st["bis"]), len(st["pivots"]),
             len(st["signals"]), n))
    print("%-10s %-12s %-6s %-6s %-6s %-8s %-4s %-5s %s"
          % ("type", "dt", "px", "area", "dif", "pv", "dir", "conf",
             "mbei/vv"))
    for s in st["signals"]:
        print("%-10s %-12s %-6.2f %-6s %-6s %-8s %-4s %-5s %s/%s"
              % (s["type"], s["dt"], s.get("price") or 0,
                 "T" if s.get("div_area") else
                 ("F" if s.get("div_area") is False else "—"),
                 "T" if s.get("div_dif") else
                 ("F" if s.get("div_dif") is False else "—"),
                 s.get("pv_state") or "—", s.get("pv_dir") or "—",
                 s.get("conf") if s.get("conf") is not None else "—",
                 "T" if s.get("mbei") else
                 ("F" if s.get("mbei") is False else "—"),
                 "T" if s.get("vv") else ("F" if s.get("vv") is False else "—")))
