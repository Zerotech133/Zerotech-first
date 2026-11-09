# -*- coding: utf-8 -*-
"""缠论多级别推演卡 K20 · 三级联立（方案 A）→ K22 v2 增强。

背景（老田×三石 0930/1002 聊天方法论数字化）：
  日线定方向 + 30 分钟定段落 + 5 分钟定时点（区间套），锚位=各级别中枢
  上下沿（ZG/ZD）与分型极值（笔端点），每步推演=带触发价位的完备分类，
  2 买质量=止损幅度（2 买低点至前低锚距离）。

数据流（纯只读，不写图，符合链纪律）：
  chanlun_data.load_kline（1d/30m/5m）
  → chanlun_signal.analyze（bis/pivots/signals/fractals 一次拿全）
  → level_view 逐级组装 → card 三级联立 → CLI / --json / chanlun_plot

v2 增量（K22，学习素材 5 批 18 图 + 开源对标 chan.py 定标参数）：
  ① 关键点三灯（#8）：中枢底距离（空间）+ 次级别走势完成（结构）+
     关键均线到位（动能）——三灯齐=「不到关键位不动手」的机器化；
  ② 二买三位置分档（#1）：信号价 vs 本级别中枢 ZG/ZD → 上方>内部>下方；
     质量=止损距离（5% 铁律）+ 回撤比例（chan.py max_bs2_rate=0.618）；
     同级别二买后再现抬升二买 → 2s·类二（「二买后还是二买」）；
  ③ 买卖点细分标签（开源谱系）：1类→趋势背驰/MACD背驰/动量衰减；
     3买→3a（一买后的枢=趋势三买）/3b（一买前的枢）/3·盘整三买；
  ④ 区间套规则化（qjt）：本级别近端买点 + 次级别近端一买同现 → 联立确认。

用法：
  python chanlun_card.py 301216.SZ            # 卡面
  python chanlun_card.py 301216.SZ --json     # 原始 JSON
"""

import json
import sys

import chanlun_data as CD
from chanlun_signal import analyze as sig_analyze

# 三级联立（聊天口径）与装载根数
LEVELS = {"1d": 500, "30m": 1500, "5m": 2400}
ANCHOR_BIS = 8  # 锚位回看笔数（近端分型极值窗）
SIG_WIN = 20  # 「近端信号」窗口（K 线根数）

# v2 定标参数（开源对标吸收，推演卡展示口径、不拦截）
KP_NEAR_PCT = 2.5  # 灯1/灯3 距离阈值：现价距锚/均线 ±2.5% 内=到位
BS2_STOP_MAX = 5.0  # 2 买止损距离铁律（%）：交易纪律图「亏损 5% 坚决止损」
BS2_RETRACE_MAX = 0.618  # chan.py max_bs2_rate：二买回撤黄金分割上限


def _ma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _trend(bis, last_px=None):
    """v3 #10 趋势过滤（法层：永远只做上升趋势/找长升短调）。

    口径（HH/HL 结构，近两上笔高点 + 近两下笔低点）：
      HH 且 HL → up；LH 且 LL → down；其余 → range。
    延伸修正：末笔没走完时最新价已破端点（上笔新高/下笔新低），
    用 last_px 修正极值（600337 实证：确认端点 3.14 而现价 3.82）。
    长升短调：up 态 + 末笔为调整下笔 + 前上笔升幅 ≥2× 本调幅。
    """
    ups = [b for b in bis if b["dir"] == 1]
    dns = [b for b in bis if b["dir"] == -1]
    hh = ups[-1]["e_price"] if ups else None
    hl = dns[-1]["e_price"] if dns else None
    hh_prev = ups[-2]["e_price"] if len(ups) > 1 else None
    hl_prev = dns[-2]["e_price"] if len(dns) > 1 else None
    if last_px and bis:
        if bis[-1]["dir"] == 1 and hh is not None and last_px > hh:
            hh = last_px  # 上笔延伸新高
        if bis[-1]["dir"] == -1 and hl is not None and last_px < hl:
            hl = last_px  # 下笔延伸新低
    state = "range"
    if None not in (hh, hl, hh_prev, hl_prev):
        if hh > hh_prev and hl > hl_prev:
            state = "up"
        elif hh < hh_prev and hl < hl_prev:
            state = "down"
    short_adj = None
    if state == "up" and bis[-1]["dir"] == -1 and ups:
        up_amp = hh - ups[-1]["s_price"]
        dn_amp = bis[-1]["e_price"] - bis[-1]["s_price"]
        if dn_amp < 0 < up_amp and up_amp >= 2 * (-dn_amp):
            short_adj = True
    return {
        "state": state,
        "short_adjust": short_adj,
        "hh": hh,
        "hh_prev": hh_prev,
        "hl": hl,
        "hl_prev": hl_prev,
    }


def level_view(code, period):
    """单级别视图：K 线 → 信号层 → 末笔/中枢/锚位/近端信号（含影子字段）。

    返回 None=该级别数据不可用（如实降级，不中断三级联立）。
    """
    r = CD.load_kline(code, period=period, limit=LEVELS[period])
    if not r.get("ok") or len(r.get("bars", [])) < 60:
        return None
    bars = r["bars"]
    try:
        st = sig_analyze(bars)
    except Exception as e:
        print("  [%s] 信号层失败：%s" % (period, str(e)[:60]))
        return None
    bis, pivots = st.get("bis") or [], st.get("pivots") or []
    if not bis:
        return None

    last, prev = bis[-1], (bis[-2] if len(bis) > 1 else None)
    frs = st.get("fractals") or []
    last_fr = frs[-1] if frs else None

    pv = pivots[-1] if pivots else None
    pv_state = None
    if pv:
        e_px = last["e_price"]
        if e_px > pv["zg"]:
            pv_state = "上方离开"
        elif e_px < pv["zd"]:
            pv_state = "下方离开"
        else:
            pv_state = "构建中"

    # 锚位：近 ANCHOR_BIS 笔端点极值（分型极值）+ 中枢上下沿
    win = bis[-ANCHOR_BIS:]
    his = [b["e_price"] for b in win if b["dir"] == 1] + [
        b["s_price"] for b in win if b["dir"] == -1
    ]
    los = [b["s_price"] for b in win if b["dir"] == 1] + [
        b["e_price"] for b in win if b["dir"] == -1
    ]

    cutoff_idx = max(0, len(bars) - SIG_WIN)
    cutoff = bars[cutoff_idx]["dt"]
    sigs = [s for s in st.get("signals", []) if s["dt"] >= cutoff]

    closes = [b["c"] for b in bars]
    _BT = 8  # 持仓卡用的近端笔尾（含趋势判定所需 ups/dns）
    return {
        "bars_n": len(bars),
        "range": [bars[0]["dt"], bars[-1]["dt"]],
        "last_px": bars[-1]["c"],
        "ma20": _ma(closes, 20),
        "ma60": _ma(closes, 60),
        "trend": _trend(bis, bars[-1]["c"]),
        "bis_tail": [
            {k: b[k] for k in ("s_dt", "e_dt", "s_price", "e_price", "dir")}
            for b in bis[-_BT:]
        ],
        "last_bi": {
            "dir": "上" if last["dir"] == 1 else "下",
            "s_dt": last["s_dt"],
            "e_dt": last["e_dt"],
            "s_price": last["s_price"],
            "e_price": last["e_price"],
            "pending": True,
            "confirmed_tail": prev is not None,
        },
        "last_fractal": (
            {"dt": last_fr["dt"], "type": last_fr["type"], "price": last_fr["price"]}
            if last_fr
            else None
        ),
        "pivot": (
            {
                "zg": pv["zg"],
                "zd": pv["zd"],
                "s_dt": pv["s_dt"],
                "e_dt": pv["e_dt"],
                "state": pv_state,
            }
            if pv
            else None
        ),
        "anchors": {
            "high_%d" % ANCHOR_BIS: max(his) if his else None,
            "low_%d" % ANCHOR_BIS: min(los) if los else None,
            "pivot_zg": pv["zg"] if pv else None,
            "pivot_zd": pv["zd"] if pv else None,
        },
        "signals_recent": sigs,  # v2：保留全字段（bei/bei2/mbei/tbe/vv）
    }


def refine_label(sigs):
    """v2-c 细分标签（开源谱系映射）：近端信号列表 → 标签列表（同序）。

    1买：tbe→趋势背驰 / mbei→MACD背驰 / 其他→动量衰减
    2买：同列表内更早 2买存在 → 2s·类二
    3买：之前最近 1买与中枢 s_dt 比较 → 3a（枢在一买后=趋势三买）/
         3b（枢在一买前）/ 3·盘整三买；卖点对称只标基础型。
    """
    out = []
    for i, s in enumerate(sigs):
        t = s["type"]
        if t == "1买":
            if s.get("tbe"):
                lab = "1类·趋势背驰"
            elif s.get("mbei"):
                lab = "1类·MACD背驰"
            else:
                lab = "1类·动量衰减"
        elif t == "2买":
            earlier = any(x["type"] == "2买" and x["dt"] < s["dt"] for x in sigs[:i])
            lab = "2s·类二" if earlier else "2买"
        elif t == "3买":
            f1 = [x for x in sigs[:i] if x["type"] == "1买" and x["dt"] < s["dt"]]
            if not f1:
                lab = "3·盘整三买"
            else:
                pv_s = s.get("_pv_s_dt")
                lab = "3a·趋势三买" if (pv_s and f1[-1]["dt"] < pv_s) else "3b·转折三买"
        elif t.endswith("卖"):
            lab = t
        else:
            lab = t
        out.append(lab)
    return out


def two_buy(views):
    """v2-b 2 买质量（三位置分档 + 0.618 回撤 + 5% 铁律）。

    逐级别扫描近端全部 2 买：pos=信号价 vs 本级别中枢（上方>内部>下方），
    stop=信号价至前低锚距离%，retrace=（前高-信号价）/（前高-前1买价）。
    返回 {"best":…, "all":[…]}；无任何 2 买 → {"best": None, "all": []}。
    """
    found = []
    for p in ("1d", "30m", "5m"):
        v = views.get(p)
        if not v:
            continue
        pv = v.get("pivot")
        lo = v["anchors"]["low_%d" % ANCHOR_BIS]
        b2s = [s for s in v.get("signals_recent", []) if s["type"] == "2买"]
        for s in b2s:
            px = s["price"]
            pos = None
            if pv and px:
                pos = "上方" if px > pv["zg"] else "内部" if px >= pv["zd"] else "下方"
            stop = round((px - lo) / px * 100, 1) if (px and lo) else None
            # 回撤比例：前 1 买价 → 之间高点 → 本 2 买价
            retrace = None
            f1 = [
                x
                for x in v.get("signals_recent", [])
                if x["type"] == "1买" and x["dt"] < s["dt"]
            ]
            if f1 and px:
                lo0 = f1[-1]["price"]
                hi = v["anchors"]["high_%d" % ANCHOR_BIS]
                if lo0 and hi and hi > lo0:
                    retrace = round((hi - px) / (hi - lo0), 3)
            ok = (stop is None or stop <= BS2_STOP_MAX) and (
                retrace is None or retrace <= BS2_RETRACE_MAX
            )
            found.append(
                {
                    "level": p,
                    "sig_dt": s["dt"],
                    "sig_px": px,
                    "pos": pos,
                    "stop_dist_pct": stop,
                    "retrace": retrace,
                    "pass": ok,
                }
            )
    best = None
    if found:
        best = min(
            found,
            key=lambda x: x["stop_dist_pct"] if x["stop_dist_pct"] is not None else 999,
        )
    return {"best": best, "all": found}


def key_point(views):
    """v2-a 关键点三灯（#8）：中枢底 / 次级别完成 / 关键均线。

    场景=回踩买（日线回踩至中枢底，次级别止跌，均线支撑）：
    灯1 空间——日线现价距近端中枢 ZD ≤KP_NEAR_PCT% 或已触及；
    灯2 结构——次级别（30m 缺则 5m）末笔向下且末端已现底分型；
    灯3 动能——日线现价距 MA20 ≤KP_NEAR_PCT% 且 MA20>MA60 多头排列。
    """
    d = views.get("1d")
    if not d:
        return None
    px = d["last_px"]
    # 灯1：中枢底距离
    zd = (d.get("pivot") or {}).get("zd") or d["anchors"]["low_%d" % ANCHOR_BIS]
    l1 = {"on": False, "detail": "无锚"}
    if zd and px:
        pct = (px - zd) / px * 100
        if pct <= KP_NEAR_PCT:
            l1 = {
                "on": True,
                "detail": "锚 %.2f · 现价 %s（%+.1f%%）"
                % (zd, "已到/破下方" if pct <= 0 else "上方", pct),
            }
        else:
            l1 = {"on": False, "detail": "锚 %.2f · 现价上方 %+.1f%% 未到" % (zd, pct)}
    # 灯2：次级别走势完成
    l2 = {"on": False, "detail": "次级别不可用"}
    for p in ("30m", "5m"):
        sv = views.get(p)
        if not sv:
            continue
        lb, fr = sv["last_bi"], sv.get("last_fractal")
        if (
            lb["dir"] == "下"
            and fr
            and fr["type"] == "底"
            and str(fr["dt"]) >= str(lb["e_dt"])
        ):
            l2 = {
                "on": True,
                "detail": "%s 末分型 底@%.2f（%s）" % (p, fr["price"], fr["dt"]),
            }
        elif lb["dir"] == "下":
            l2 = {"on": False, "detail": "%s 末笔向下延伸中·无底分型" % p}
        else:
            l2 = {"on": False, "detail": "%s 末笔向上·非回踩窗口" % p}
        break
    # 灯3：关键均线
    l3 = {"on": False, "detail": "均线不足"}
    ma20, ma60 = d.get("ma20"), d.get("ma60")
    if ma20 and ma60 and px:
        pct = (px - ma20) / px * 100
        bull = ma20 > ma60
        if bull and abs(pct) <= KP_NEAR_PCT:
            l3 = {
                "on": True,
                "detail": "MA20 %.2f · 距 %+.1f%% · 多头排列" % (ma20, pct),
            }
        elif not bull:
            l3 = {"on": False, "detail": "MA20<MA60 空头排列·等反转"}
        else:
            l3 = {"on": False, "detail": "MA20 %.2f · 距 %+.1f%% 未到" % (ma20, pct)}
    lights = [l1, l2, l3]
    n_on = sum(1 for x in lights if x["on"])
    verdict = (
        "三灯齐 · 关键点到（果断评估开仓）"
        if n_on == 3
        else "差 %d 灯 · 继续等（不到关键位不动手）" % (3 - n_on)
    )
    return {
        "pivot_bottom": l1,
        "sub_done": l2,
        "ma_ok": l3,
        "n_on": n_on,
        "verdict": verdict,
    }


def qjt(views):
    """v2-d 区间套规则化：本级别近端买点 + 次级别近端一买同现。"""
    out = []
    d = views.get("1d")
    if not d:
        return out
    mains = [
        s for s in d.get("signals_recent", []) if s["type"] in ("1买", "2买", "3买")
    ]
    for p in ("30m", "5m"):
        sv = views.get(p)
        if not sv:
            continue
        subs = [s for s in sv.get("signals_recent", []) if s["type"] == "1买"]
        for m in mains:
            for s in subs:
                out.append(
                    {
                        "main_lv": "1d",
                        "main": m["type"],
                        "main_dt": m["dt"],
                        "sub_lv": p,
                        "sub_dt": s["dt"],
                        "note": "日线 %s（%s）+ %s 一买（%s）联立"
                        % (m["type"], m["dt"], p, s["dt"]),
                    }
                )
    return out


def classify(views):
    """分类应对模板（锚位 if/else，逐级从 5m 往上）。"""
    out = []
    lab = {"5m": "5 分钟", "30m": "30 分钟", "1d": "日线"}
    for p in ("5m", "30m", "1d"):
        v = views.get(p)
        if not v or not v.get("pivot"):
            continue
        zg, zd = v["pivot"]["zg"], v["pivot"]["zd"]
        hi, lo = (
            v["anchors"]["high_%d" % ANCHOR_BIS],
            v["anchors"]["low_%d" % ANCHOR_BIS],
        )
        out.append(
            "[%s] 站稳 %.2f → 有机会；放量站上 %.2f → 转折成立"
            % (lab[p], zg, hi if hi else zg)
        )
        out.append("[%s] 跌破 %.2f 后反抽不进 → %s 3卖" % (lab[p], zd, lab[p]))
        if lo:
            out.append("[%s] 回踩 %.2f 不破 → 2 买观察位" % (lab[p], lo))
    return out


def card(code):
    """三级联立推演卡组装（v2：三灯/细分标签/二买分档/区间套）。"""
    views = {p: level_view(code, p) for p in LEVELS}
    # 3 买细分需要中枢起点：把 pivot.s_dt 附到信号上（refine 内部读取）
    for v in [x for x in views.values() if x]:
        pv_s = (v.get("pivot") or {}).get("s_dt")
        for s in v.get("signals_recent", []):
            s["_pv_s_dt"] = pv_s
        v["sig_labels"] = refine_label(v["signals_recent"])
    return {
        "code": code,
        "levels": views,
        "classify": classify(views),
        "two_buy": two_buy(views),
        "key_point": key_point(views),
        "qjt": qjt(views),
    }


def _fd(dt):
    """日期显示：日线取前 10 位，分钟取 MM-DD HH:MM。"""
    s = str(dt)
    return s[:10] if len(s) <= 10 else s[5:16]


def _print(c):
    """卡面人类可读打印（对齐聊天语言）。"""
    lab = {"1d": "日线（定方向）", "30m": "30 分钟（定段落）", "5m": "5 分钟（定时点）"}
    print("═" * 62)
    print("多级别推演卡 · %s" % c["code"])
    kp = c.get("key_point")
    if kp:
        print("─" * 62)
        print("关键点三灯：%s" % kp["verdict"])
        print("  灯1 空间·中枢底：%s" % kp["pivot_bottom"]["detail"])
        print("  灯2 结构·次级别：%s" % kp["sub_done"]["detail"])
        print("  灯3 动能·均线：%s" % kp["ma_ok"]["detail"])
    for p in ("1d", "30m", "5m"):
        v = c["levels"].get(p)
        print("─" * 62)
        if not v:
            print("%s：数据不可用" % lab[p])
            continue
        b, pv = v["last_bi"], v["pivot"]
        pend = "没走完" if b["pending"] else "已走完"
        tr = v.get("trend") or {}
        tr_s = {"up": "↑上升", "down": "↓下降"}.get(tr.get("state"), "→震荡")
        if tr.get("short_adjust"):
            tr_s += "·长升短调✓"
        if tr.get("hh") and tr.get("hl"):
            tr_s += "（HH %.2f / HL %.2f）" % (tr["hh"], tr["hl"])
        print(
            "%s：%d 根，现价 %.2f · 趋势 %s" % (lab[p], v["bars_n"], v["last_px"], tr_s)
        )
        print(
            "  末笔：%s %s→%s（%.2f→%.2f）· %s"
            % (
                b["dir"],
                _fd(b["s_dt"]),
                _fd(b["e_dt"]),
                b["s_price"],
                b["e_price"],
                pend,
            )
        )
        if pv:
            print(
                "  中枢：ZG %.2f / ZD %.2f（%s~%s）· %s"
                % (pv["zg"], pv["zd"], pv["s_dt"][:10], pv["e_dt"][:10], pv["state"])
            )
        a = v["anchors"]
        print(
            "  锚位：高 %.2f / 低 %.2f（近 %d 笔）"
            % (
                a["high_%d" % ANCHOR_BIS] or 0,
                a["low_%d" % ANCHOR_BIS] or 0,
                ANCHOR_BIS,
            )
        )
        if v["signals_recent"]:
            lbls = v.get("sig_labels") or refine_label(v["signals_recent"])
            print(
                "  近端信号：%s"
                % "；".join(
                    "%s@%.2f(%s)" % (lb, s["price"], _fd(s["dt"]))
                    for s, lb in zip(v["signals_recent"][-3:], lbls[-3:])
                )
            )
    print("─" * 62)
    print("分类应对：")
    for line in c["classify"]:
        print("  · %s" % line)
    tb = c.get("two_buy") or {}
    best = tb.get("best")
    print("─" * 62)
    if best:
        print(
            "2 买质量：%s级 %s @%.2f · 位置[%s] · 失效位 %.1f%%%s · 回撤 %s%s"
            % (
                best["level"],
                _fd(best["sig_dt"]),
                best["sig_px"],
                best["pos"] or "?",
                best["stop_dist_pct"],
                ""
                if (
                    best["stop_dist_pct"] is None
                    or best["stop_dist_pct"] <= BS2_STOP_MAX
                )
                else " ⚠超5%铁律",
                ("%.3f" % best["retrace"])
                if best["retrace"] is not None
                else "无1买基准",
                "" if best["pass"] else " · ⚠不合规",
            )
        )
        for x in tb.get("all", [])[1:]:
            print(
                "        另：%s级 %s @%.2f [%s] stop %s retr %s"
                % (
                    x["level"],
                    _fd(x["sig_dt"]),
                    x["sig_px"],
                    x["pos"] or "?",
                    x["stop_dist_pct"],
                    x["retrace"],
                )
            )
    else:
        print("2 买质量：近端无 2 买信号")
    q = c.get("qjt") or []
    if q:
        print("区间套：%d 组联立" % len(q))
        for x in q[:3]:
            print("  · %s" % x["note"])
    print("═" * 62)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    code = sys.argv[1]
    c = card(code)
    if "--json" in sys.argv:
        print(json.dumps(c, ensure_ascii=False, indent=1, default=str))
    else:
        _print(c)


if __name__ == "__main__":
    main()
