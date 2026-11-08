# -*- coding: utf-8 -*-
"""缠论择势层：判断「现在这个市场，允不允许做多」。

为什么单独一层
--------------
一买/二买/三买是**结构信号**，不是买入指令。它们回答的是「这只票的结构
位置在哪」；而「现在能不能做」是市场层面的问题。混在一起就会犯一个具体
的错：9~11 月大盘一路跌，产线照样每天产出几十上百个买点，如果你把买点
当买入指令，就会在下跌里被反复摩擦。

缠论判断「势」的四件事（本模块按此实现）
--------------------------------------
1. 价格在最后一个中枢的哪一侧（above / inside / below）
   —— 中枢是多空反复确认的价格区间，破了下方且回不来，是下跌中继。
2. 中枢序列的方向（最近 N 个中枢的 ZG 是上移、走平还是下移）
   —— 中枢上移 = 缠论意义上最硬的「上升趋势」定义。
3. 最后一笔的方向与相对力度
4. 最近是否出现买点、以及该买点之后有没有被破坏

三档输出
--------
GREEN  中枢上移 且 价格在中枢上方        → 允许正常做多
AMBER  价格在中枢内部，或中枢走平        → 只做结构最强的，半仓
RED    价格在中枢下方 且 中枢下移        → 不做多，等结构重建

用法:
    python tools/research/chanlun_regime.py
    python tools/research/chanlun_regime.py --asof 2026-11-06
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
for _p in (QB, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = os.path.join(QB, "data")
IDX = os.path.join(DATA, "tdx", "ts_index_daily")

# 势的观察对象。多指数交叉，避免单一指数失真：
# 中证全指代表「全市场」，中证2000 代表池内票的主体（中小盘），
# 沪深300 代表权重股，上证代表情绪面。
WATCH = [
    ("000985.CSI", "中证全指", "全市场"),
    ("932000.CSI", "中证2000", "小盘"),
    ("000300.SH", "沪深300", "权重"),
    ("000001.SH", "上证指数", "情绪"),
]

# 主判指数：决定档位的那一个。选全指而不是上证，因为上证被权重股绑架，
# 池内票绝大多数是中小盘，用上证判势会系统性偏乐观。
PRIMARY = "000985.CSI"


def _load_index(code, asof):
    """从分片 parquet 里取单只指数到 asof 的日线。"""
    import glob

    import pandas as pd

    rows = []
    for p in sorted(glob.glob(os.path.join(IDX, "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open",
                                        "high", "low", "close", "vol", "amount"])
        d = d[d["ts_code"] == code]
        if len(d):
            rows.append(d)
    if not rows:
        return []
    df = pd.concat(rows, ignore_index=True)
    df["trade_date"] = (df["trade_date"].astype(str)
                        .str.replace("-", "", regex=False).str[:8])
    df = df.drop_duplicates("trade_date").sort_values("trade_date")
    df = df[df["trade_date"] <= asof]
    out = []
    for _, r in df.iterrows():
        if not r["close"] or r["close"] != r["close"]:
            continue
        out.append({
            "dt": r["trade_date"],
            "o": float(r["open"]) if r["open"] == r["open"] else float(r["close"]),
            "h": float(r["high"]) if r["high"] == r["high"] else float(r["close"]),
            "l": float(r["low"]) if r["low"] == r["low"] else float(r["close"]),
            "c": float(r["close"]),
            "v": float(r["vol"]) if r["vol"] == r["vol"] else 0.0,
            "a": float(r["amount"]) if r["amount"] == r["amount"] else 0.0,
        })
    return out


def _ma(bars, i, n):
    seg = bars[max(0, i - n + 1): i + 1]
    return sum(b["c"] for b in seg) / len(seg) if seg else None


def structure_state(bars):
    """对一条日线序列算缠论结构状态。返回可 JSON 的 dict。"""
    import chanlun_signal as csig

    if len(bars) < 130:
        return None
    st = csig.analyze(bars)
    pvs = st.get("pivots") or []
    bis = st.get("bis") or []
    segs = st.get("segments") or []
    sigs = st.get("signals") or []
    i = len(bars) - 1
    px = bars[i]["c"]

    # 1) 价格相对最后一个中枢的位置
    zone, zg, zd = None, None, None
    pv_h = n_bi = None
    if pvs:
        p = pvs[-1]
        zg, zd = float(p["zg"]), float(p["zd"])
        pv_h = (zg - zd) / zd if zd > 0 else None
        n_bi = p.get("n_bi")
        zone = ("above" if px > zg else ("inside" if px > zd else "below"))

    # 2) 中枢序列方向（取最近 3 个，ZG 严格抬升才算上移）
    pv_dir = None
    pv_series = []
    if len(pvs) >= 2:
        zgs = [float(p["zg"]) for p in pvs[-3:]]
        if len(zgs) >= 2:
            ups = sum(1 for a, b in zip(zgs, zgs[1:]) if b > a)
            downs = sum(1 for a, b in zip(zgs, zgs[1:]) if b < a)
            pv_dir = 1 if ups == len(zgs) - 1 else (-1 if downs == len(zgs) - 1 else 0)
    # 只报最近 4 个中枢的 ZG/ZD，用来回答「它是从哪个区间跌下来的」
    for p in pvs[-4:]:
        pv_series.append({"zg": round(float(p["zg"]), 2),
                          "zd": round(float(p["zd"]), 2),
                          "s_dt": p.get("s_dt"), "e_dt": p.get("e_dt")})

    # 3) 最后一笔
    last_bi = None
    if bis:
        b = bis[-1]
        last_bi = {"dir": b["dir"], "s_dt": b["s_dt"], "e_dt": b["e_dt"],
                   "s_price": float(b["s_price"]), "e_price": float(b["e_price"]),
                   "days": bars.index(next(x for x in bars if x["dt"] == b["e_dt"])) -
                           bars.index(next(x for x in bars if x["dt"] == b["s_dt"]))}

    # 4) 最近的买点信号（只认买方向），及其后是否被破坏
    last_sig = None
    for s in reversed(sigs):
        if s["type"] in ("1买", "2买", "3买"):
            dt = str(s["dt"])
            j = None
            for k, b in enumerate(bars):
                if b["dt"] == dt:
                    j = k
                    break
            after = bars[j:] if j is not None else []
            min_low = min(b["l"] for b in after) if after else None
            last_sig = {
                "type": s["type"], "dt": dt,
                "price": float(s["price"]),
                "bars_ago": (len(bars) - 1 - j) if j is not None else None,
                "broken_pct": round((min_low / float(s["price"]) - 1) * 100, 2)
                              if min_low else None,
            }
            break

    # 5) 均线与位置
    ma20, ma60 = _ma(bars, i, 20), _ma(bars, i, 60)
    seg60 = bars[max(0, i - 59): i + 1]
    lo60 = min(b["l"] for b in seg60)
    hi60 = max(b["h"] for b in seg60)
    qpos = (px - lo60) / (hi60 - lo60) if hi60 > lo60 > 0 else None
    ret20 = px / bars[i - 20]["c"] - 1 if i >= 20 else None
    ret5 = px / bars[i - 5]["c"] - 1 if i >= 5 else None

    return {
        "asof": bars[i]["dt"], "px": round(px, 3),
        "zone": zone, "zg": round(zg, 3) if zg else None,
        "zd": round(zd, 3) if zd else None,
        "pv_height_pct": round(pv_h * 100, 2) if pv_h else None,
        "pv_n_bi": n_bi, "pv_dir": pv_dir, "n_pivots": len(pvs),
        "pv_series": pv_series,
        "n_bis": len(bis), "n_segs": len(segs),
        "ma20": round(ma20, 3) if ma20 else None,
        "ma60": round(ma60, 3) if ma60 else None,
        "ma20_slope_pct": round((ma20 / _ma(bars, i - 5, 20) - 1) * 100, 2)
                          if ma20 and i >= 5 and _ma(bars, i - 5, 20) else None,
        "qpos60": round(qpos, 3) if qpos is not None else None,
        "ret5_pct": round(ret5 * 100, 2) if ret5 is not None else None,
        "ret20_pct": round(ret20 * 100, 2) if ret20 is not None else None,
        "last_bi": last_bi, "last_sig": last_sig,
    }


def grade_one(st):
    """单个标的的势档位：GREEN / AMBER / RED，附理由。

    判据顺序刻意从「硬」到「软」：先看中枢（多空确认过的区间），再看
    均线（滞后的平滑）。中枢上移且站上上沿是最强组合；中枢下移且跌破
    下沿是最弱组合；其余一律 AMBER，因为「说不清」的时候半仓比满仓安全。
    """
    if not st:
        return "RED", ["数据不足"]
    zone, pv_dir = st["zone"], st["pv_dir"]
    why = []
    if zone:
        why.append({"above": "站上中枢上沿", "inside": "中枢内部",
                    "below": "跌破中枢下沿"}[zone])
    if pv_dir == 1:
        why.append("中枢上移")
    elif pv_dir == -1:
        why.append("中枢下移")
    elif pv_dir == 0:
        why.append("中枢走平")
    if st["ma20_slope_pct"] is not None:
        why.append("MA20 %s" % ("向上" if st["ma20_slope_pct"] > 0 else "向下"))

    if zone == "above" and pv_dir == 1:
        return "GREEN", why
    if zone == "below" and pv_dir == -1:
        return "RED", why
    if zone == "below":
        # 跌破中枢但中枢没下移 → 可能是回抽，但结构没确认，不给做多
        return "RED", why
    if zone == "inside" and pv_dir == -1:
        return "RED", why
    return "AMBER", why


def regime_history(bars, lookback=140, step=1):
    """逐日回算势档位，得到「势的轨迹」。

    为什么必须做这件事：只说「今天是 RED」没有操作含义——RED 可能刚从
    GREEN 掉下来（那是第一根警棍，该减仓），也可能已经 RED 三个月（那是
    既成事实，该等结构重建）。两者的动作完全不同，所以必须知道档位的
    历史序列和最近一次转档发生在哪天。

    实现上每个点用 bars[:i+1] 重算结构，中枢/笔会随窗口右端延伸而修正，
    这正是缠论的真实处境（结构要等后续笔确认），不做未来函数。
    """
    hist = []
    i = len(bars) - 1
    lo = max(140, i - lookback + 1)
    for j in range(lo, i + 1, step):
        st = structure_state(bars[: j + 1])
        g, _ = grade_one(st)
        hist.append({"dt": bars[j]["dt"], "grade": g,
                     "px": round(bars[j]["c"], 3),
                     "zone": st["zone"] if st else None,
                     "pv_dir": st["pv_dir"] if st else None})
    return hist


def summarize_history(hist):
    """从轨迹里抽出：当前档位持续多久、最近转档点、各档天数分布。"""
    if not hist:
        return None
    cur = hist[-1]["grade"]
    # 找当前档位的起点
    start = len(hist) - 1
    while start > 0 and hist[start - 1]["grade"] == cur:
        start -= 1
    # 最近一次转档（含方向）
    flip = None
    for k in range(1, len(hist)):
        if hist[k]["grade"] != hist[k - 1]["grade"]:
            flip = {"dt": hist[k]["dt"], "from": hist[k - 1]["grade"],
                    "to": hist[k]["grade"], "bars_ago": len(hist) - 1 - k,
                    "px": hist[k]["px"]}
    days = {}
    for h in hist:
        days[h["grade"]] = days.get(h["grade"], 0) + 1
    n = len(hist)
    return {"cur": cur, "cur_since": hist[start]["dt"],
            "cur_days": len(hist) - start, "n_days": n,
            "share": {k: round(v / n * 100, 1) for k, v in days.items()},
            "last_flip": flip,
            "series": [{"dt": h["dt"], "g": h["grade"], "px": h["px"]} for h in hist]}


def next_levels(st):
    """从当前档位往上走，需要依次越过哪几个价位；往下走，撑在哪。

    缠论的转档不是渐变的，是价位事件：站上/跌破某个区间才算数。把这些
    价位算出来，比说「等待情绪修复」有用得多。

    注意方向：上方看「回到哪个中枢」，下方看「跌到哪个中枢」。早期版本
    取「前一中枢下沿」当失效位，结果那根中枢在现价上方 20%——序列不是
    单调的，必须按价格关系筛，不能按时间顺序取。
    """
    if not st or not st.get("zg"):
        return []
    px, zg, zd = st["px"], st["zg"], st["zd"]
    out = []
    up = []
    if px < zd:
        up.append({"to": "AMBER", "need": "站回中枢下沿 %.2f" % zd,
                   "px": zd, "dist_pct": round((zd / px - 1) * 100, 2)})
        up.append({"to": "GREEN", "need": "站上中枢上沿 %.2f 且中枢不再下移" % zg,
                   "px": zg, "dist_pct": round((zg / px - 1) * 100, 2)})
    elif px < zg:
        up.append({"to": "GREEN", "need": "站上中枢上沿 %.2f" % zg,
                   "px": zg, "dist_pct": round((zg / px - 1) * 100, 2)})
    else:
        up.append({"to": "维持", "need": "守住中枢上沿 %.2f" % zg,
                   "px": zg, "dist_pct": round((zg / px - 1) * 100, 2)})
    out += up

    # 下方支撑：现价之下最近的中枢。序列里可能多个，取 ZG 最高的那个
    below = [p for p in st.get("pv_series") or [] if p["zg"] < px]
    if below:
        p = max(below, key=lambda x: x["zg"])
        out.append({"to": "下方支撑", "need": "%.0f~%.0f 区间（%s 起）"
                    % (p["zd"], p["zg"], p.get("s_dt") or "-"),
                    "px": p["zg"], "dist_pct": round((p["zg"] / px - 1) * 100, 2)})
        out.append({"to": "破位确认", "need": "跌破该区间下沿 %.0f 则下一个区间也失守" % p["zd"],
                    "px": p["zd"], "dist_pct": round((p["zd"] / px - 1) * 100, 2)})
    else:
        out.append({"to": "下方支撑", "need": "无（已跌破全部历史中枢，处于新低区）",
                    "px": None, "dist_pct": None})
    return out


def overall(primary_grade, grades):
    """合成总档位。主判指数说了算，但若小盘风格是 RED 要单独提示
    —— 池内票多是中小盘，小盘 RED 而全指 GREEN 的组合很常见，
    这时做多是「权重护指数、个股在跌」的典型陷阱。"""
    g = dict(grades)
    small = g.get("932000.CSI")
    note = []
    if primary_grade == "GREEN" and small == "RED":
        note.append("全指 GREEN 但小盘 RED：指数被权重撑住，个股在跌，做多需减半")
        return "AMBER", note
    if primary_grade == "AMBER" and small == "RED":
        note.append("小盘 RED：池内中小盘标的普遍破位")
        return "RED", note
    return primary_grade, note


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="20261106")
    ap.add_argument("--json", default=os.path.join(ROOT, "portal", "chanlun_regime.json"))
    args = ap.parse_args()

    asof = args.asof.replace("-", "")
    print("=" * 78)
    print("缠论择势 · asof %s" % asof)
    print("=" * 78)

    detail, grades = {}, {}
    for code, name, role in WATCH:
        bars = _load_index(code, asof)
        st = structure_state(bars)
        g, why = grade_one(st)
        grades[code] = g
        detail[code] = {"name": name, "role": role, "grade": g,
                        "why": why, "st": st}
        print("\n[%s] %s（%s）→ %s" % (code, name, role, g))
        if not st:
            print("  数据不足")
            continue
        print("  时点 %s  收盘 %.3f  60日分位 %s" %
              (st["asof"], st["px"],
               ("%.2f" % st["qpos60"]) if st["qpos60"] is not None else "-"))
        print("  中枢 %d 个  最后 %s ~ %s  高度 %s%%  笔数 %s  方向 %s"
              % (st["n_pivots"],
                 ("%.2f" % st["zd"]) if st["zd"] else "-",
                 ("%.2f" % st["zg"]) if st["zg"] else "-",
                 st["pv_height_pct"], st["pv_n_bi"],
                 {1: "上移", -1: "下移", 0: "走平", None: "-"}[st["pv_dir"]]))
        if st["pv_series"]:
            print("    中枢序列：%s" % " → ".join(
                "%.0f~%.0f" % (p["zd"], p["zg"]) for p in st["pv_series"]))
        print("  笔 %d 根  最后笔 %s (%s→%s)  段 %d"
              % (st["n_bis"],
                 "向上" if st["last_bi"]["dir"] > 0 else "向下",
                 st["last_bi"]["s_dt"], st["last_bi"]["e_dt"], st["n_segs"]))
        print("  MA20 %.2f (斜率 %s%%)  MA60 %.2f  5日 %s%%  20日 %s%%"
              % (st["ma20"], st["ma20_slope_pct"], st["ma60"],
                 st["ret5_pct"], st["ret20_pct"]))
        if st["last_sig"]:
            s = st["last_sig"]
            print("  最近买点 %s @ %s (价 %.2f, %s 根前)，之后最深 %s%%"
                  % (s["type"], s["dt"], s["price"], s["bars_ago"],
                     s["broken_pct"]))
        else:
            print("  最近买点 无")

    g_all, note = overall(detail[PRIMARY]["grade"], grades)
    print("\n" + "=" * 78)
    print("总档位：%s" % g_all)
    print("  主判 = %s（%s）" % (PRIMARY, detail[PRIMARY]["name"]))
    for n in note:
        print("  注意：" + n)
    print("  分项：%s" % "  ".join("%s=%s" % (detail[c]["name"], grades[c])
                                  for c, _, _ in WATCH))

    # ---- 转档条件 ----
    print("\n转档需要依次越过：")
    for lv in next_levels(detail[PRIMARY]["st"]):
        d = lv["dist_pct"]
        print("  → %-8s %s%s"
              % (lv["to"], lv["need"],
                 ("（现价 %+.2f%%）" % d) if d is not None else ""))

    # ---- 势的轨迹（主判 + 小盘） ----
    print("\n势的轨迹（逐日回算，最近 140 个交易日）：")
    hist_out = {}
    for code in (PRIMARY, "932000.CSI"):
        bars = _load_index(code, asof)
        h = regime_history(bars, lookback=140)
        s = summarize_history(h)
        hist_out[code] = s
        nm = detail[code]["name"]
        if not s:
            continue
        print("  %s：当前 %s 已 %d 个交易日（自 %s）｜近 %d 日占比 %s"
              % (nm, s["cur"], s["cur_days"], s["cur_since"], s["n_days"],
                 " ".join("%s=%.0f%%" % (k, v) for k, v in sorted(s["share"].items()))))
        f = s["last_flip"]
        if f:
            print("    最近转档 %s：%s → %s（%d 根前，指数 %.2f）"
                  % (f["dt"], f["from"], f["to"], f["bars_ago"], f["px"]))
        else:
            print("    近 %d 日无转档" % s["n_days"])
    print("=" * 78)

    out = {"asof": asof, "grade": g_all, "primary": PRIMARY,
           "notes": note, "detail": detail,
           "next_levels": next_levels(detail[PRIMARY]["st"]),
           "history": hist_out}
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("→ %s" % args.json)


if __name__ == "__main__":
    main()
