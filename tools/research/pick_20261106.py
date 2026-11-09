# -*- coding: utf-8 -*-
"""基于 2026-11-06 收盘的缠论结构选股（审美口径，不是 alpha 验证）。

定位
----
前几轮已证明：入场信号在 hold（不止损）口径下 t=0.55、无可辨识选股能力，
alpha 来自 8% 移动止损截断左尾。本脚本不推翻那个结论，也不假装有。

它只回答：若纯粹按缠论结构的美学标准看，11-06 收盘这个时点上，哪几只的
图形是「值得等」的——回抽不破、中枢干净、回踩无力、中枢在抬、不在追高、
上方有空间、失效位清晰。

时点与新鲜度
------------
`sig_dt` 是笔的终点，`detect_date` 才是产线真正看到信号的日期（笔要等后续
笔确认）。所以新鲜度按 detect_date 算，且只取最近 8 个交易日检出的
（>= --since），这才是"11-06 时点的结构"，不是给旧仓位找理由。

九维评分（权重和为 1）
--------------------
A 结构位     0.20  三买看现价距 ZG（跌回中枢即失效）；二买看是否已启动且未破
                  回调低点；一买看背驰是否成立
B 中枢质量   0.10  高度 3%~12% 为佳（分歧收敛），笔数 3~7 为佳（震荡不过久）
D 回踩力度   0.10  回踩幅度/离开幅度，<0.35 满分（洗盘而非反转），>1.0 差
E 位置       0.10  60 日区间分位，>0.9 即追高
F 量能       0.06  5 日均额/20 日均额，0.9~2.5 温和放量
G 盈亏比     0.14  (上方最近笔顶-现价)/(现价-止损)，>=2.5 满分
I 中枢抬升   0.10  最近两中枢 ZG 上移 = 缠论意义上的上升趋势
J 信号有效   0.12  现价是否守住信号低点（跌破 >3% 视为结构破坏）
K 趋势排列   0.08  现价 / MA20 / MA60 的相对位置（大级别方向的代理）

硬过滤（审美门槛，不参与打分）
------------------------------
- 名称含 ST / 退
- detect_date 早于 --since（默认 2026-10-28，最近 8 个交易日）
- 现价距信号价已涨超 12%（买点已过，不追）
- 盈亏比 < 1.5（上方压力太近，不值得做）
- 最近 20 日涨幅 > 40%（末端加速）
- 11-06 收盘涨停（买不进）
- 结构不足 / 匹配不到信号

止损口径（统一带缓冲带，避免 0.2% 这种必被打的止损）
- 三买：ZG 下 1.5%，但不近于现价 3%，不远于 12%
- 二买/一买：信号低点下 3%，clamp 到现价下方 10%~3%

输出
----
portal/pick_20261106.json + 标准输出清单

用法:
    python tools/research/pick_20261106.py
    python tools/research/pick_20261106.py --since 2026-10-23 --top 40
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
for _p in (QB, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.path.join(QB, "data")
ASHARE = os.path.join(DATA, "ashare")
PAPER = os.path.join(DATA, "chanlun_paper.json")
OUT = os.path.join(ROOT, "portal", "pick_20261106.json")

ASOF = "2026-11-06"
MIN_ADV = 0.8   # 20 日均成交额下限（亿元）：低于此建仓冲击成本不可控
W = {"A": 0.17, "B": 0.09, "D": 0.08, "E": 0.09, "F": 0.05,
     "G": 0.13, "I": 0.13, "J": 0.11, "K": 0.15}


# ---------- 单维打分 ----------

def _s_pos(typ, dzg, px_vs_sig, mbei, zone):
    """A 结构位。

    二买必须同时看「现价相对最近中枢」：缠论里中枢被向下破坏后反抽回
    区间内，是三卖观察区，不是买点——这一条不看，纯按"守住信号低点"
    打分，会把已破位的反抽全部选进来。
    """
    if typ == "3买":
        if dzg is None:
            return 0.40
        if dzg <= 0:
            return 0.10
        if dzg <= 0.08:
            return 1.00
        if dzg <= 0.15:
            return 0.70
        return 0.40
    if typ == "2买":
        if px_vs_sig is None:
            return 0.50
        if px_vs_sig > 0:
            base = 1.00 if px_vs_sig <= 0.06 else 0.75
        elif px_vs_sig > -0.02:
            base = 0.55
        else:
            base = 0.20
        base *= {"above": 1.00, "inside": 0.55, "below": 0.25}.get(zone, 0.85)
        return base
    if typ == "1买":
        base = 0.70 if mbei else 0.40
        if px_vs_sig is not None and px_vs_sig < 0:
            base *= 0.30
        return base
    return 0.0


def _s_pv(pv_h, n_bi):
    if pv_h is None or pv_h <= 0:
        return 0.30
    if 0.03 <= pv_h <= 0.12:
        h = 1.0
    elif pv_h < 0.03:
        h = max(0.45, pv_h / 0.03)
    else:
        h = max(0.15, 1.0 - (pv_h - 0.12) / 0.25)
    if n_bi is None:
        n = 0.5
    elif 3 <= n_bi <= 7:
        n = 1.0
    elif n_bi <= 11:
        n = 0.6
    else:
        n = 0.3
    return 0.6 * h + 0.4 * n


def _s_pull(r):
    if r is None:
        return 0.50
    if r <= 0.35:
        return 1.0
    if r <= 0.60:
        return 0.85
    if r <= 0.85:
        return 0.60
    if r <= 1.00:
        return 0.40
    return 0.15


def _s_qpos(q):
    if q is None:
        return 0.50
    if q <= 0.75:
        return 1.0
    if q <= 0.90:
        return 0.55
    return 0.20


def _s_vol(r):
    if r is None:
        return 0.50
    if 0.90 <= r <= 2.50:
        return 1.0
    if r < 0.90:
        return max(0.20, r / 0.90 * 0.8)
    return max(0.20, 1.0 - (r - 2.5) / 3.0)


def _s_rr(rr):
    if rr is None or rr <= 0:
        return 0.05
    if rr >= 2.5:
        return 1.0
    return max(0.10, rr / 2.5)


def _s_trend(up):
    if up is None:
        return 0.40
    return 1.0 if up > 0 else (0.55 if up == 0 else 0.25)


def _s_alive(break_pct):
    """J 信号有效性：信号日之后的最低价是否破过信号价。

    只看「现价 vs 信号价」会把「跌破 → 反抽回来」判成有效，而这恰是
    缠论里最该回避的一类（结构已破坏后的反抽）。必须看途中最低点。
    """
    if break_pct is None:
        return 0.40
    if break_pct >= 0:
        return 1.0
    if break_pct > -0.03:
        return 0.65
    if break_pct > -0.08:
        return 0.30
    return 0.10


def _s_ma(typ, px, ma20, ma60, slope):
    """K 趋势排列。三买看多头排列（必须已在上升趋势里）；
    二买/一买看 MA20 拐头与站上（缠论一买之后 MA60 本就还没转，用排列判它会全灭）。"""
    if not ma20 or not ma60:
        return 0.40
    if typ == "3买":
        if px > ma20 > ma60:
            return 1.0
        if px > ma60 and ma20 > ma60 * 0.99:
            return 0.70
        if px > ma60:
            return 0.45
        return 0.20
    above60 = px > ma60
    if px > ma20 and slope > 0 and above60:
        return 1.0
    if px > ma20 and slope > 0:
        return 0.70                 # 短期转强但仍在 MA60 下方，是反弹不是趋势
    if px > ma20:
        return 0.50
    if slope > 0:
        return 0.35
    return 0.15


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------- 单只计算 ----------

def _one(job):
    row, since = job
    code = row["code"]
    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    if not os.path.exists(path):
        return None
    import chanlun_scan as cs
    import chanlun_signal as csig

    bars = cs._bars_from_parquet(path)
    if not bars or len(bars) < 130:
        return None
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    d0 = idx.get(ASOF)
    stale = False
    if d0 is None:
        d0 = len(bars) - 1
        stale = True
    if d0 < 70:
        return None

    px = bars[d0]["c"]
    if not px or px <= 0:
        return None

    st = csig.analyze(bars[: d0 + 1])
    sigs = st.get("signals") or []
    pvs = st.get("pivots") or []
    bis = st.get("bis") or []

    want = (row["type"], str(row.get("sig_dt")))
    sig = None
    for s in sigs:
        if (s["type"], str(s["dt"])) == want:
            sig = s
            break
    if sig is None:
        return None

    typ = row["type"]
    sig_px = float(sig["price"])
    if sig_px <= 0:
        return None
    px_vs_sig = px / sig_px - 1.0

    zg = sig.get("zg")
    zd = sig.get("zd")
    dzg = (px - zg) / zg if zg else None

    ib = None
    for i, b in enumerate(bis):
        if b["e_dt"] == sig["dt"]:
            ib = i
    pull_ratio = None
    if ib is not None and ib >= 1:
        a = abs(bis[ib]["e_price"] - bis[ib]["s_price"])
        b0 = abs(bis[ib - 1]["e_price"] - bis[ib - 1]["s_price"])
        if b0 > 0 and bis[ib]["dir"] != bis[ib - 1]["dir"]:
            pull_ratio = a / b0

    pv_h = n_bi = None
    if zg and zd and zd > 0:
        pv_h = (zg - zd) / zd
        for p in pvs:
            if p["zg"] == zg and p["zd"] == zd:
                n_bi = p.get("n_bi")
                break
    elif pvs:
        p = pvs[-1]
        if p["zd"] > 0:
            pv_h = (p["zg"] - p["zd"]) / p["zd"]
            n_bi = p.get("n_bi")

    up = None
    if len(pvs) >= 2:
        up = 1 if pvs[-1]["zg"] > pvs[-2]["zg"] else (-1 if pvs[-1]["zg"] < pvs[-2]["zg"] else 0)

    # 止损
    if typ == "3买" and zg:
        stop = _clamp(zg * 0.985, px * 0.88, px * 0.97)
    else:
        stop = _clamp(sig_px * 0.97, px * 0.90, px * 0.97)

    # 目标：分两级。T1 = 上方最近一个向上笔的顶（缠论的第一压力位），
    # T2 = 120 日高点（结构走完后的合理终点）。
    # 只认至少 4% 空间内的笔顶——小级别笔顶常只在上方 2%，拿来当目标
    # 会把 RR 全部压到 1.5 以下，等于用噪声当压力位。
    tops = [b["e_price"] for b in bis
            if b["e_price"] > px * 1.04 and b["e_price"] >= b["s_price"]]
    if tops:
        target = min(tops)
        tgt_note = "上方最近笔顶"
    else:
        target = max(b["h"] for b in bars[max(0, d0 - 119): d0 + 1])
        tgt_note = "120日高点"
    hi120 = max(b["h"] for b in bars[max(0, d0 - 119): d0 + 1])
    t2 = max(hi120, target)
    rr = ((target - px) / (px - stop)) if px > stop else None

    seg60 = bars[max(0, d0 - 60): d0 + 1]
    lo60 = min(b["l"] for b in seg60)
    hi60 = max(b["h"] for b in seg60)
    qpos = (px - lo60) / (hi60 - lo60) if hi60 > lo60 > 0 else None

    ma20 = sum(b["c"] for b in bars[d0 - 19: d0 + 1]) / 20
    ma60 = sum(b["c"] for b in bars[d0 - 59: d0 + 1]) / 60
    ma20_prev = sum(b["c"] for b in bars[d0 - 24: d0 - 4]) / 20
    slope = (ma20 / ma20_prev - 1.0) if ma20_prev else 0.0

    a5 = [b["v"] * b["c"] for b in bars[d0 - 4: d0 + 1] if b.get("v")]
    a20 = [b["v"] * b["c"] for b in bars[d0 - 19: d0 + 1] if b.get("v")]
    volr = (sum(a5) / len(a5)) / (sum(a20) / len(a20)) if (len(a5) >= 3 and len(a20) >= 10) else None

    # 成交额必须取 parquet 的 amount 列：bars["v"] 的量纲不可靠
    # （volume 列是股还是手随数据源变），用它乘 close 会把 adv 算错量级。
    adv20 = None
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["trade_date", "amount"])
        amt_map = {cs._td_dt(t): a for t, a in zip(df["trade_date"], df["amount"])}
        amt = [amt_map.get(b["dt"]) for b in bars[max(0, d0 - 19): d0 + 1]]
        amt = [x for x in amt if x and x > 0]
        if len(amt) >= 10:
            adv20 = sum(amt) / len(amt) * 1000.0   # amount 单位千元 → 元
    except Exception:
        adv20 = None

    prev_c = bars[d0 - 1]["c"] if d0 >= 1 else None
    day_ret = (px / prev_c - 1.0) if prev_c else None
    limit_up = bool(day_ret is not None and day_ret > 0.095 and bars[d0]["h"] == px)
    ret20 = (px / bars[d0 - 20]["c"] - 1.0) if (d0 >= 20 and bars[d0 - 20]["c"]) else None

    i_sig = idx.get(sig["dt"])
    min_low_after = min(b["l"] for b in bars[i_sig: d0 + 1]) if i_sig is not None else None
    break_pct = (min_low_after / sig_px - 1.0) if min_low_after else None

    dd = str(row.get("detect_date") or "")[:10]
    i_det = idx.get(dd)
    gap = (d0 - i_det) if i_det is not None else None

    zone = None
    if pvs:
        p = pvs[-1]
        zone = ("above" if px > p["zg"] else
                ("inside" if px > p["zd"] else "below"))

    s = {
        "A": _s_pos(typ, dzg, px_vs_sig, sig.get("mbei"), zone),
        "B": _s_pv(pv_h, n_bi),
        "D": _s_pull(pull_ratio),
        "E": _s_qpos(qpos),
        "F": _s_vol(volr),
        "G": _s_rr(rr),
        "I": _s_trend(up),
        "J": _s_alive(break_pct),
        "K": _s_ma(typ, px, ma20, ma60, slope),
    }
    score = sum(W[k] * v for k, v in s.items())
    if stale:
        score *= 0.85

    return {
        "code": code, "name": row.get("name") or "",
        "type": typ, "sig_dt": row.get("sig_dt"),
        "detect_date": dd, "entry_date": row.get("entry_date"),
        "entry_px": row.get("entry_px"), "px": round(px, 3),
        "days_held": row.get("days_held"), "pnl": row.get("pnl"),
        "stale": stale, "gap_det": gap,
        "zg": round(zg, 3) if zg else None, "zd": round(zd, 3) if zd else None,
        "dzg_pct": round(dzg * 100, 2) if dzg is not None else None,
        "pv_h_pct": round(pv_h * 100, 2) if pv_h else None,
        "pv_nbi": n_bi, "pv_up": up, "n_pv": len(pvs),
        "sig_px": round(sig_px, 3),
        "px_vs_sig_pct": round(px_vs_sig * 100, 2),
        "zone": zone,
        "break_pct": round(break_pct * 100, 2) if break_pct is not None else None,
        "pull_ratio": round(pull_ratio, 2) if pull_ratio else None,
        "mbei": sig.get("mbei"), "tbe": sig.get("tbe"), "vv": sig.get("vv"),
        "stop": round(stop, 3), "target": round(target, 3), "tgt_note": tgt_note,
        "t2": round(t2, 3), "up_t2_pct": round((t2 - px) / px * 100, 2),
        "stop_pct": round((px - stop) / px * 100, 2),
        "up_pct": round((target - px) / px * 100, 2),
        "rr": round(rr, 2) if rr else None,
        "qpos60": round(qpos, 2) if qpos is not None else None,
        "ma20": round(ma20, 3), "ma60": round(ma60, 3),
        "ma20_slope_pct": round(slope * 100, 2),
        "volr": round(volr, 2) if volr else None,
        "adv20_yi": round(adv20 / 1e8, 2) if adv20 else None,
        "ret20_pct": round(ret20 * 100, 2) if ret20 is not None else None,
        "limit_up": limit_up,
        "score": round(score, 4),
        "dims": {k: round(v, 3) for k, v in s.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--since", default="2026-10-28")
    ap.add_argument("--max-hold", type=int, default=99)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--min-rr", type=float, default=1.5)
    ap.add_argument("--min-adv", type=float, default=MIN_ADV)
    args = ap.parse_args()

    paper = json.load(open(PAPER, encoding="utf-8"))
    kids = []
    for r in paper.get("open") or []:
        if r.get("type") not in ("1买", "2买", "3买"):
            continue
        if str(r.get("detect_date") or "")[:10] < args.since:
            continue
        if int(r.get("days_held") or 0) > args.max_hold:
            continue
        nm = str(r.get("name") or "")
        if "ST" in nm.upper() or "退" in nm:
            continue
        kids.append(r)
    print("候选（买点 + 检出>=%s）: %d 笔" % (args.since, len(kids)))

    jobs = [(r, args.since) for r in kids]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = [x for x in ex.map(_one, jobs) if x]
    print("结构可算且匹配到信号: %d 笔" % len(rows))

    all_rows = sorted(rows, key=lambda r: -r["score"])
    n_stale = sum(1 for r in rows if r["stale"])
    n_lu = sum(1 for r in rows if r["limit_up"])
    n_hot = sum(1 for r in rows if (r["ret20_pct"] or 0) > 40)
    n_chase = sum(1 for r in rows if r["px_vs_sig_pct"] > 12)
    n_rr = sum(1 for r in rows if (r["rr"] or 0) < args.min_rr)
    n_dzg = sum(1 for r in rows
                if r["type"] == "3买" and (r["dzg_pct"] or 0) > 15)
    n_brk = sum(1 for r in rows if (r["break_pct"] or 0) < -8)
    n_ill = sum(1 for r in rows if (r["adv20_yi"] or 0) < args.min_adv)
    n_below = sum(1 for r in rows
                  if r["type"] in ("2买", "3买") and r["zone"] == "below")
    rows = [r for r in rows
            if not r["limit_up"]
            and (r["ret20_pct"] or 0) <= 40
            and r["px_vs_sig_pct"] <= 8
            and (r["rr"] or 0) >= args.min_rr
            and (r["break_pct"] or 0) >= -8
            and not (r["type"] == "3买" and (r["dzg_pct"] or 0) > 15)
            and not (r["type"] in ("2买", "3买") and r["zone"] == "below")
            and (r["adv20_yi"] or 0) >= args.min_adv]
    rows.sort(key=lambda r: -r["score"])

    seen, keep = set(), []
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        keep.append(r)
    rows = keep

    print("剔除: 停牌 %d / 涨停 %d / 20日涨超40%% %d / 已涨超8%% %d / RR<1.5 %d"
          % (n_stale, n_lu, n_hot, n_chase, n_rr))
    print("      三买已远离15%% %d / 信号后深破8%% %d / 二买三买在中枢下方 %d / 20日额<%.1f亿 %d"
          % (n_dzg, n_brk, n_below, MIN_ADV, n_ill))
    print("同股去重后 %d 只\n" % len(rows))

    print("%-3s %-10s %-9s %-4s %6s %6s %6s %5s %5s %5s %6s %6s %5s %6s %6s %-6s %s"
          % ("#", "code", "name", "type", "score", "dzg%", "pvh%", "pull",
             "qpos", "zone", "stop%", "up%", "rr", "vsSig", "brk%", "dims", ""))
    for i, r in enumerate(rows[: args.top], 1):
        dm = "".join("%s%s" % (k, ("%d" % round(v * 9))) for k, v in r["dims"].items())
        print("%-3d %-10s %-9s %-4s %6.3f %6s %6s %5s %5s %5s %6.2f %6.1f %5s %6s %6s %s"
              % (i, r["code"], r["name"][:9], r["type"], r["score"],
                 ("%+.2f" % r["dzg_pct"]) if r["dzg_pct"] is not None else "-",
                 r["pv_h_pct"] if r["pv_h_pct"] else "-",
                 r["pull_ratio"] if r["pull_ratio"] else "-",
                 r["qpos60"] if r["qpos60"] is not None else "-",
                 {"above": "上", "inside": "内", "below": "下"}.get(r["zone"], "-"),
                 r["stop_pct"], r["up_pct"],
                 r["rr"] if r["rr"] else "-",
                 "%+.1f" % r["px_vs_sig_pct"],
                 "%+.1f" % r["break_pct"] if r["break_pct"] is not None else "-", dm))
        # adv 见 json

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({"asof": ASOF, "weights": W, "since": args.since,
               "min_rr": args.min_rr, "min_adv": args.min_adv,
               "n_pool": len(kids), "n_ok": len(rows),
               "dropped": {"stale": n_stale, "limit_up": n_lu, "too_hot": n_hot,
                           "chase": n_chase, "low_rr": n_rr},
               "all": all_rows,
               "picks": rows[: max(args.top, 60)]},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n写入 %s" % OUT)


if __name__ == "__main__":
    main()
