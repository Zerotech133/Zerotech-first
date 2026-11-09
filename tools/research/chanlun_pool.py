# -*- coding: utf-8 -*-
"""缠论股票池：结构入池 ≠ 买入信号（v2，自带结构重算与生命周期）。

用户提的问题
------------
「一买二买三买不等于要买卖股票，它是要择时和择势对吧，那就应该进入一个
股票池，股票池再去观察什么时候择时什么时候择势。」

对。之前把买点当买入指令是概念混淆。本脚本把三者彻底分开，且每一层的
规则都不是按教科书写的，是按本目录下的实证结果写的：

  Layer 1 结构层   1买/2买/3买 → **入池资格**（只回答「有没有值得盯的结构」）
  Layer 2 择势层   市场层面 → 产出是**仓位上限**，不是买卖信号
  Layer 3 择时层   个股层面 → 只做出场（ZG 生死线），不做入场

支撑这份设计的四份实证（同目录）
--------------------------------
`pool_by_year.py`    全样本 20 日 alpha：1买 −0.01%（t=−0.10，n=11,839）、
                     2买 +0.31%（t=1.50）、3买 −0.42%（t=−3.98）
                     → 三类买点**不产生 alpha**，收益≈beta×全A
                     → 3买 显著为负，该降级而不是优选
`regime_robust.py`   指数择势判不了方向：顺向 GREEN t=−2.72；
                     逆向剔 2024-09 后 t=−16.14
`entry_timing_lab.py` 七种入场规则全量 829 笔全部为负（最好次日开盘 −3.06%）
`exit_zg_lab.py`     唯一有效的一层：ZG 止损 +2.32pp，但触发率 86%

v2 相对 v1 的修正
-----------------
v1 直接从 pick 产物读 `zg`，但那里只有 3买 带中枢（1买/2买 的信号对象无
zg 字段），导致 374 只没有生死线；且 `px_vs_sig` 中位 6.2%、最大 196%，
大量信号早已过期却仍在池里。v2 自己重算结构，并给每只加生命周期：
  new（新鲜，可观察）→ running（已启动，等回踩）→ expired（过期，出池）
  任何阶段 break_pct ≤ −8 直接 broken（破位出池）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QB = os.path.join(ROOT, "quant_backtest")
for _p in (QB, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ASHARE = os.path.join(QB, "data", "ashare")
PICK = os.path.join(ROOT, "portal", "pick_20261106.json")
REGIME = os.path.join(ROOT, "portal", "chanlun_regime.json")
OUT = os.path.join(ROOT, "portal", "chanlun_pool_20261106.json")

ASOF = "2026-11-06"
ASOF_N = "20261106"

# ── 实证常数（见文件头） ──
TYPE_ALPHA = {
    "1买": {"alpha_pct": -0.01, "t": -0.10, "n": 11839,
            "note": "alpha 恒为 0，收益完全等于 beta×全A"},
    "2买": {"alpha_pct": 0.31, "t": 1.50, "n": 4803,
            "note": "唯一曾有过正 alpha（2024 +1.62%），2025 后衰减"},
    "3买": {"alpha_pct": -0.42, "t": -3.98, "n": 19186,
            "note": "显著负 alpha，系统性差于基准"},
}
ZONE_RET = {"above": -2.98, "inside": -3.75, "below": 0.20}
EXIT_GAIN = 2.32

# 生命周期阈值：按 px_vs_sig（信号价→现价的涨幅）与信号年龄切。
# 8% 来自上一轮 pick 的「已涨超 8% 不追」，20% 是彻底错过第一段，
# 40 个交易日约等于两个月的结构有效期。
FRESH_PCT = 8.0
FRESH_AGE = 15          # 超过 15 个交易日未确认的信号不算「新鲜」
EXPIRED_PCT = 20.0
EXPIRED_AGE = 40
BROKEN_PCT = -8.0


def _one(job):
    code, typ, sig_dt, name = job
    path = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    if not os.path.exists(path):
        return None
    import chanlun_scan as cs
    import chanlun_signal as csig

    bars = cs._bars_from_parquet(path)
    if not bars or len(bars) < 130:
        return None
    idx = {str(b["dt"]).replace("-", "")[:8]: i for i, b in enumerate(bars)}
    d0 = idx.get(ASOF_N)
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

    want = (typ, str(sig_dt))
    sig = next((s for s in sigs if (s["type"], str(s["dt"])) == want), None)
    if sig is None:
        return None
    sig_px = float(sig["price"])
    if sig_px <= 0:
        return None

    # 中枢一律取「当前最后一个中枢」，而不是信号对象里的 zg。
    # 理由：生死线是给现在用的，不是给信号当天用的。
    zg = float(pvs[-1]["zg"]) if pvs else None
    zd = float(pvs[-1]["zd"]) if pvs else None
    pv_h = (zg - zd) / zd if zg and zd and zd > 0 else None
    n_bi = pvs[-1].get("n_bi") if pvs else None
    zone = ("above" if zg and px > zg else
            ("inside" if zd and px > zd else ("below" if zg else None)))
    pv_up = None
    if len(pvs) >= 2:
        pv_up = 1 if pvs[-1]["zg"] > pvs[-2]["zg"] else (
            -1 if pvs[-1]["zg"] < pvs[-2]["zg"] else 0)

    # 信号之后的最深破位（判断结构是否已被破坏）
    i_sig = idx.get(str(sig_dt).replace("-", "")[:8])
    if i_sig is None:
        i_sig = next((i for i, b in enumerate(bars) if str(b["dt"]) == str(sig_dt)), None)
    break_pct = None
    if i_sig is not None and i_sig <= d0:
        min_low = min(b["l"] for b in bars[i_sig: d0 + 1])
        break_pct = (min_low / sig_px - 1.0) * 100

    px_vs_sig = (px / sig_px - 1.0) * 100
    sig_age = (d0 - i_sig) if i_sig is not None else None

    # 生死线：ZG 下 1.5%，但必须夹在现价下方 3%~15%
    if zg and zone != "below":
        death = max(zg * 0.985, px * 0.85)
        death = min(death, px * 0.97)
        death_rule = "ZG 下 1.5%（收盘跌破即出）"
    else:
        death = max(sig_px * 0.92, px * 0.85)
        death = min(death, px * 0.97)
        death_rule = "信号价 -8%（已在 ZG 下方，ZG 不适用）"

    # 生命周期
    if break_pct is not None and break_pct <= BROKEN_PCT:
        stage = "broken"
    elif px_vs_sig > EXPIRED_PCT or (sig_age is not None and sig_age > EXPIRED_AGE):
        stage = "expired"
    elif px_vs_sig > FRESH_PCT:
        stage = "running"
    elif sig_age is not None and sig_age > FRESH_AGE:
        stage = "running"        # 涨幅不大但信号已旧 → 归入等回踩，不叫新鲜
    else:
        stage = "new"

    return {
        "code": code, "name": name, "type": typ,
        "sig_dt": str(sig_dt), "sig_age": sig_age,
        "px": round(px, 3), "sig_px": round(sig_px, 3),
        "px_vs_sig_pct": round(px_vs_sig, 2),
        "zg": round(zg, 3) if zg else None,
        "zd": round(zd, 3) if zd else None,
        "zone": zone, "pv_h_pct": round(pv_h * 100, 2) if pv_h else None,
        "pv_n_bi": n_bi, "pv_up": pv_up,
        "break_pct": round(break_pct, 2) if break_pct is not None else None,
        "death_px": round(death, 3), "death_rule": death_rule,
        "death_disp_pct": round((death / px - 1) * 100, 2),
        "stage": stage, "stale": stale,
        "type_alpha_pct": TYPE_ALPHA.get(typ, {}).get("alpha_pct"),
        "type_alpha_t": TYPE_ALPHA.get(typ, {}).get("t"),
        "zone_ret_ref": ZONE_RET.get(zone),
    }


def grade(r):
    """池内定级。先看类型 alpha（统计支撑），再看位置，最后看破位。

    「alpha 是否为负」按统计显著性判断，不按小数点：1买 的 alpha = −0.01%
    但 t = −0.10，实质就是 0（等价于持有指数），不能因为带个负号就降级；
    3买 的 −0.42% 配 t = −3.98 才是真的负。判据取 t 值更稳。
    """
    if r["type"] == "3买" or r["stage"] == "broken":
        return "avoid"
    t = r.get("type_alpha_t")
    if t is None or t > -1.0:            # 不显著为负 → 视为中性及以上
        return "watch" if r.get("zone") in ("below", "inside") else "observe"
    return "observe"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    with open(PICK, encoding="utf-8") as f:
        pick = json.load(f)
    with open(REGIME, encoding="utf-8") as f:
        regime = json.load(f)
    jobs = [(r["code"], r["type"], r["sig_dt"], r.get("name") or "")
            for r in pick["all"]]
    print("重算结构：%d 只候选 ..." % len(jobs))
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for k, res in enumerate(ex.map(_one, jobs, chunksize=10)):
            if res:
                res["grade"] = grade(res)
                rows.append(res)
            if (k + 1) % 100 == 0:
                print("  ...%d/%d" % (k + 1, len(jobs)))
    print("可算 %d 只（%.1f%%）\n" % (len(rows), len(rows) / len(jobs) * 100))

    order = {"watch": 0, "observe": 1, "avoid": 2}
    rows.sort(key=lambda x: (order[x["grade"]],
                             {"new": 0, "running": 1, "expired": 2, "broken": 3}[x["stage"]],
                             -(x.get("type_alpha_pct") or -9)))

    g = regime.get("grade")
    d = regime.get("detail", {}).get(regime.get("primary", ""), {})
    st = d.get("st") or {}

    print("=" * 92)
    print("缠论股票池 · asof %s · 结构入池（不是买入清单）" % ASOF)
    print("=" * 92)
    print("入池 %d 只｜类型 %s" % (len(rows), "  ".join(
        "%s=%d" % (k, v) for k, v in sorted(Counter(r["type"] for r in rows).items()))))
    print("分级     %s" % "  ".join(
        "%s=%d" % (k, v) for k, v in sorted(Counter(r["grade"] for r in rows).items())))
    print("生命周期 %s" % "  ".join(
        "%s=%d" % (k, v) for k, v in sorted(Counter(r["stage"] for r in rows).items())))
    print("有中枢   %d 只（能算 ZG 生死线）；无中枢 %d 只"
          % (sum(1 for r in rows if r["zg"]), sum(1 for r in rows if not r["zg"])))

    print("\n" + "-" * 92)
    print("【Layer 2 择势】%s｜产出是仓位上限，不是买卖信号" % g)
    print("-" * 92)
    print("  主判 %s（%s）收 %.2f" % (regime.get("primary"), d.get("name"), st.get("px") or 0))
    for lv in (regime.get("next_levels") or [])[:2]:
        dd = lv.get("dist_pct")
        print("  %-6s %s%s" % (lv.get("to"), lv.get("need"),
                              ("（%+.2f%%）" % dd) if dd is not None else ""))
    print("  实证：择势判不了方向（顺向 t=−2.72；逆向剔 2024-09 后 t=−16.14）")
    print("        → RED 的含义是「不新增 beta 暴露」，不是「马上要跌」。")

    print("\n" + "-" * 92)
    print("【Layer 3 择时】只做出场")
    print("-" * 92)
    print("  七种入场规则全量 829 笔全负（最好次日开盘 −3.06%，t=−5.86）")
    print("  ZG 止损是唯一有效的一层：%+.2fpp（−2.94%% → −0.62%%），触发率 86%%" % EXIT_GAIN)
    print("  → 每只标 death_px，跌破无条件出，不看理由、不等反弹。")

    print("\n" + "-" * 92)
    print("【Layer 1 结构层】三类买点实证 alpha（全样本 20 日，扣 beta）")
    print("-" * 92)
    for t in ("1买", "2买", "3买"):
        v = TYPE_ALPHA[t]
        print("  %-4s alpha %+6.2f%%  t=%+5.2f  n=%6d  %s"
              % (t, v["alpha_pct"], v["t"], v["n"], v["note"]))
    print("  → 1买 alpha≈0 表示「只暴露 beta」；3买 −0.42% 表示「系统性差于基准」。")

    for gr, title in (("watch", "核心观察｜新鲜且在有效位置"),
                      ("observe", "观察不主动"),
                      ("avoid", "回避｜3买或已破位")):
        sub = [r for r in rows if r["grade"] == gr]
        print("\n" + "-" * 92)
        print("%s：%d 只" % (title, len(sub)))
        print("-" * 92)
        if not sub:
            continue
        print("  %-2s %-10s %-9s %-4s %-6s %7s %4s %8s %8s %7s %7s %6s %5s"
              % ("#", "code", "name", "typ", "阶段", "px", "位", "vsSig", "brk",
                 "ZG", "death", "disp", "age"))
        for i, r in enumerate(sub[: args.top], 1):
            print("  %-2d %-10s %-9s %-4s %-6s %7.2f %4s %7s %7s %7s %7.2f %6.1f%% %5s"
                  % (i, r["code"], (r["name"] or "")[:9], r["type"],
                     {"new": "新鲜", "running": "已启动", "expired": "过期",
                      "broken": "破位"}[r["stage"]],
                     r["px"], {"above": "上", "inside": "内", "below": "下"}.get(r["zone"], "-"),
                     "%+.1f" % r["px_vs_sig_pct"],
                     ("%+.1f" % r["break_pct"]) if r["break_pct"] is not None else "-",
                     ("%.2f" % r["zg"]) if r["zg"] else "-",
                     r["death_px"], r["death_disp_pct"],
                     r["sig_age"] if r["sig_age"] is not None else "-"))

    n_new = sum(1 for r in rows if r["stage"] == "new")
    n_watch = sum(1 for r in rows if r["grade"] == "watch")
    n_watch_new = sum(1 for r in rows if r["grade"] == "watch" and r["stage"] == "new")
    print("\n" + "=" * 92)
    print("池内结论")
    print("=" * 92)
    print("  · %d 只入池 → 核心观察 %d 只，其中「新鲜且结构有效」只有 %d 只"
          % (len(rows), n_watch, n_watch_new))
    print("  · 新鲜（未过期）合计 %d 只；过期 %d 只、破位 %d 只"
          % (n_new, sum(1 for r in rows if r["stage"] == "expired"),
             sum(1 for r in rows if r["stage"] == "broken")))
    print("  · 现在一只都不建议动手，三条理由同时成立：")
    print("      ① 三类买点 alpha ≤ 0（3买 t=−3.98）→ 结构层不产生超额")
    print("      ② 势 %s → 择势层不允许新增 beta 暴露" % g)
    print("      ③ ZG 止损触发率 86%%、中位第 1 天 → 多数信号一进场就触发")
    print("  · 池子的用途是**监控结构转折**，等三件事之一：")
    print("      ① 势转 AMBER（需站回 %s，%+.2f%%）"
          % (st.get("zd"), (st["zd"] / st["px"] - 1) * 100 if st.get("zd") and st.get("px") else 0))
    n_tri = sum(1 for r in rows if r.get("pv_up") == 1 and r.get("zone") == "above"
                and (r.get("break_pct") or 0) >= 0)
    print("      ② 池内出现「中枢上移 + 站上 ZG + 回踩不破」同时成立")
    print("         （宽松口径当前 %d 只；上一轮叠加空间与流动性后只剩 3 只）" % n_tri)
    print("      ③ 2买 alpha 按季复算重新转正（2025 后已衰减到 0）")
    print("  · 若坚持要做：2买 ＞ 1买 ＞＞ 3买，且必须承认这是 beta 暴露不是 alpha。")
    print("=" * 92)

    out = {"asof": ASOF, "n": len(rows),
           "n_by_grade": dict(Counter(r["grade"] for r in rows)),
           "n_by_type": dict(Counter(r["type"] for r in rows)),
           "n_by_stage": dict(Counter(r["stage"] for r in rows)),
           "regime": {"grade": g, "primary": regime.get("primary"),
                      "next_levels": regime.get("next_levels", [])},
           "evidence": {"type_alpha": TYPE_ALPHA, "zone_ret": ZONE_RET,
                        "exit_zg_gain_pp": EXIT_GAIN},
           "thresholds": {"fresh_pct": FRESH_PCT, "fresh_age": FRESH_AGE,
                          "expired_pct": EXPIRED_PCT, "expired_age": EXPIRED_AGE,
                          "broken_pct": BROKEN_PCT},
           "pool": rows}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("→ %s" % OUT)


if __name__ == "__main__":
    main()
