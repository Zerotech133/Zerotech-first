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

FAR_PCT = 20.0          # 余量超过 20% 的止损线已经不可用（等于没有）


def _death_state(death, px):
    """生死线相对现价的位置：四档，含义完全不同，不能并成一个「余量」。"""
    if death >= px:
        return "breached"          # 已在生死线下方：结构已破
    m = px / death - 1.0
    if m < 0.03:
        return "tight"             # 余量 <3%：随时触发，风险贴脸
    if m > FAR_PCT / 100.0:
        return "far"               # 余量 >20%：止损形同虚设，回调先破位
    return "valid"


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

    # 生死线 = 结构失效位，纯结构推导，不夹现价。
    #
    # 早期版本写的是 death = min(max(结构位, px*0.85), px*0.97)，把线硬夹在
    # 现价下方 3%~15%。后果是 257/489 只（52.6%）的「生死线」退化成 px*0.97，
    # 变成一条随价移动的固定止损，标签却仍写「ZG 下 1.5%」。余量恒等于 3%，
    # 观察台上既无法逼近也无法拉开，C 栏机械命中 257 只 —— 这条线就废了。
    # 结构位离现价远，说明的是「追得太高」，该由位置条件否决，不该把线挪过来。
    if zg and zone == "above":
        death, death_rule = zg * 0.985, "站上中枢：回踩不破 ZG（ZG 下 1.5%）"
    elif zg and zd and zone == "inside":
        death, death_rule = zd * 0.985, "中枢内：跌破 ZD 1.5% 即结构坏"
    else:
        death, death_rule = sig_px * 0.92, "信号价 -8%（ZG 在现价上方，不适用）"

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
        # 余量 = 现价高出结构线多少。>=0 不是「余量很小」，是「已经在下方」。
        # 四档分开，观察台才不会把破位票排进「最安全」那一栏，也不会把
        # 已经跑到结构位 2 倍以上的票当成「余量充足」——那种线的止损等于没有。
        "death_state": _death_state(death, px),
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


def trade_of(r):
    """L1 选择层的真正输出：这笔够不够格进**交易层**。

    依据（见 hold_term_verify.py §C，829 笔已到期）：
      1买  持 3 日 +2.29%（t=2.50）、持 5 日 +1.92%（t=1.99）、持 20 日 −1.47%
      2买  任何持有期都不显著且全为负（持3 −0.56% t=−0.85、持20 −0.40% t=−0.24）
      3买  系统差于基准（alpha −0.42%，t=−3.98）

    所以只有 1买 进交易层；2买 留池观察不交易；3买 与破位直接不给资格。
    「进交易层」也不等于"现在买"——还要过 Layer 2 的势闸门。
    """
    if r["type"] != "1买" or r["stage"] == "broken":
        return "no"
    return "candidate" if r["stage"] == "new" else "watch_only"


def idx_mom20(code="000985.CSI", n=20):
    """指数过去 n 日累计涨跌（Layer 2 势闸门，建仓当日收盘即可算）。"""
    import glob

    try:
        import pandas as pd
    except ImportError:
        return None
    d_dir = os.path.join(ROOT, "quant_backtest", "data", "tdx", "ts_index_daily")
    clos = {}
    for p in sorted(glob.glob(os.path.join(d_dir, "*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == code]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if c == c and c > 0:            # 剔 NaN
                clos[dt.replace("-", "")[:8]] = c
    ds = sorted(clos)
    if len(ds) <= n:
        return None
    return clos[ds[-1]] / clos[ds[-1 - n]] - 1.0


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
                res["trade"] = trade_of(res)
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
    print("交易层   %s" % "  ".join(
        "%s=%d" % (k, v) for k, v in sorted(Counter(r["trade"] for r in rows).items())))
    print("         candidate=够格进交易层（1买且新鲜）；watch_only=留池等回踩；no=不给资格")

    mom20 = idx_mom20()
    gate = "未知" if mom20 is None else ("停手（弱市）" if mom20 < -0.02
                                        else ("正常做（强市，可持 5~8 日）" if mom20 > 0.02
                                              else "只做隔日（中市）"))

    print("\n" + "-" * 92)
    print("【Layer 2 择势】%s｜产出是「今天开不开新仓」，不是仓位微调" % g)
    print("-" * 92)
    print("  主判 %s（%s）收 %.2f" % (regime.get("primary"), d.get("name"), st.get("px") or 0))
    for lv in (regime.get("next_levels") or [])[:2]:
        dd = lv.get("dist_pct")
        print("  %-6s %s%s" % (lv.get("to"), lv.get("need"),
                              ("（%+.2f%%）" % dd) if dd is not None else ""))
    if mom20 is not None:
        print("  动量闸门 %s 20 日累计 %+.2f%% → **%s**（阈值 ±2%%）"
              % (regime.get("primary"), mom20 * 100, gate))
    print("  实证：按笔等权，弱市任何持有期都亏且越持越亏（持5 −0.90% t=−2.42，")
    print("        持20 −5.57% t=−7.31）；强市持5~8日为正（+1.08% t=2.49 / +1.73% t=3.17）。")
    print("        → 势不是开关方向的，是决定「允不允许开仓、允许拿多久」。")

    print("\n" + "-" * 92)
    print("【Layer 3 择时】不找入场点位，只定持有期 + 出场")
    print("-" * 92)
    print("  入场：七种规则全量 829 笔全负（最好次日开盘 −3.06%，t=−5.86）→ 不等价格")
    print("  持有：强市 5~8 日｜中市 1 日｜弱市 0（不建仓）")
    print("  同一批信号只改持有期：持20日 −4.98% → 持5日 +6.16%（自检 A4 归零检验 11.2pp）")
    print("  ZG 止损仍保留作下限：%+.2fpp（−2.94%% → −0.62%%），触发率 86%%，先触发先出" % EXIT_GAIN)

    print("\n" + "-" * 92)
    print("【Layer 1 结构层】三类买点 × 持有期，按笔等权（决定够不够格进交易层）")
    print("-" * 92)
    for t in ("1买", "2买", "3买"):
        v = TYPE_ALPHA[t]
        print("  %-4s 20日alpha %+6.2f%%  t=%+5.2f  n=%6d  %s"
              % (t, v["alpha_pct"], v["t"], v["n"], v["note"]))
    print("  短持有实测：1买 持3 +2.29%(t=2.50) / 持5 +1.92%(t=1.99)；")
    print("              2买 任何持有期全负（持3 −0.56% t=−0.85）。")
    print("  → 只有 1买 进交易层；2买 留池观察不交易；3买 与破位出池。")

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
    n_cand = sum(1 for r in rows if r["trade"] == "candidate")
    n_cand_below = sum(1 for r in rows if r["trade"] == "candidate" and r["zone"] == "below")
    print("\n" + "=" * 92)
    print("池内结论")
    print("=" * 92)
    print("  · %d 只入池 → 交易层够格 %d 只（1买且新鲜）；留池等回踩 %d 只；不给资格 %d 只"
          % (len(rows), n_cand, sum(1 for r in rows if r["trade"] == "watch_only"),
             sum(1 for r in rows if r["trade"] == "no")))
    print("  · 新鲜 %d 只；过期 %d 只、破位 %d 只"
          % (n_new, sum(1 for r in rows if r["stage"] == "expired"),
             sum(1 for r in rows if r["stage"] == "broken")))
    print("  · 现在一只都不动手，三条理由按重要性排：")
    if mom20 is not None:
        need = (0.98 / (1.0 + mom20) - 1.0) * 100
        print("      ① 势闸门：20 日动量 %+.2f%% → %s。恢复到「允许开仓」需 +%.2f%%"
              % (mom20 * 100, gate, need))
    print("      ② 交易层只认 1买：持3 +2.29%(t=2.50)、持5 +1.92%(t=1.99)；")
    print("         2买 任何持有期全负（持3 −0.56% t=−0.85），留池观察不交易")
    print("      ③ 够格的 1买 只有 %d 只%s"
          % (n_cand, ("，其中 %d 只还在最后一个中枢下方（zone=below）" % n_cand_below)
             if n_cand_below else ""))
    print("  · 池子的用途是**监控结构转折**，恢复交易只有一个条件：")
    print(f"      20 日动量回到 −2% 以上（比上一轮的「站回中枢下沿 {st.get('zd')}」更早触发，")
    print("      因为动量是 20 日窗口、中枢是绝对价位；且前者有 §3.2 的实证支撑）")
    print("  · 若势转中/强：1买 优先，强市持 5~8 日、中市只做隔日，单票 ≤5%。")
    print("    2买/3买 不因势转好而解禁——它们的问题在结构质量，不在市场环境。")
    print("=" * 92)

    out = {"asof": ASOF, "n": len(rows),
           "n_by_grade": dict(Counter(r["grade"] for r in rows)),
           "n_by_type": dict(Counter(r["type"] for r in rows)),
           "n_by_stage": dict(Counter(r["stage"] for r in rows)),
           "n_by_trade": dict(Counter(r["trade"] for r in rows)),
           "regime": {"grade": g, "primary": regime.get("primary"),
                      "mom20_pct": round(mom20 * 100, 2) if mom20 is not None else None,
                      "gate": gate,
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
