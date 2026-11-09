# -*- coding: utf-8 -*-
"""影子盘：v3 规则与现行规则并行，逐日产出对照（K-RES-20261108-SHADOW）。

为什么要有影子盘
----------------
`three_layer_v3.py` 证明的是「同一批已走完的笔，换规则后终值不同」。它回答
不了产线每天要回答的那个问题：**今天该开几只、该拿几天、现在账上有多少在途**。
这两个问题的差别是「事后统计」与「事前决策」，中间隔着一个无法回避的东西——
未到期的仓位。到 2026-11-06 为止，1151 笔通过信号里有 322 笔还没走完，
批量回测把它们从样本里剔掉了（`mature` 过滤），于是最容易出事的那部分被系统性忽略。

本脚本把两套规则放进同一个引擎、同一条日历，从第一个开仓日跑到 as-of 日，
每天落一条流水：净值、现金、在途、开仓明细、平仓明细、被规则挡掉的候选。
在途仓位按 as-of 收盘价估值（并扣卖出费），所以它的净值是「今天清仓能拿回多少」，
不是「浮盈多少」——两者差一个卖出成本，别混。

两套规则
--------
现行（cur）  全部通过信号（1买+2买）、固定持 20 日、无单票上限、不停手
v3（v3）     只做 1买、弱市不做、强市持 8 日/中市持 1 日、单票 5% 上限
v3d（v3d）   v3 的持有期换成「强 5/中 5」，作为 D 档敏感性对照

势的分档口径与 `three_layer_v3.py` 完全一致：000985.CSI 的 20 日动量，
±2% 为界。改这里必须同时改那边，否则两套结论不可比。

幂等
----
同一天重复跑不会产生重复行：落盘前先剔除 as-of 及之后的旧行再追加。
同输入 → 同输出字节，`shadow_selfcheck.py` 的 S2 用 sha1 验证这一点。

用法:
    python tools/research/shadow_book.py                    # as-of = 数据最后一天
    python tools/research/shadow_book.py --asof 20261106
    python tools/research/shadow_book.py --universe mature   # 与 three_layer_v3 对账用
    python tools/research/shadow_book.py --rebuild           # 忽略旧流水全量重写
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from portfolio_mtm import Engine, Pick, build_ratio, load_price_panel  # noqa: E402
import hf_cost as HC          # 唯一的成本口径（第十八轮），本文件不再自己算成本

SHADOW = os.path.join(ROOT, "quant_backtest", "data", "combo_shadow.jsonl")
TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
OUTDIR = os.path.join(ROOT, "portal")
LEDGER = os.path.join(OUTDIR, "shadow_book_v3.jsonl")
STATE = os.path.join(OUTDIR, "shadow_state_v3.json")
RUNLOG = os.path.join(OUTDIR, "shadow_runs_v3.jsonl")

IDX_CODE = "000985.CSI"
TH = 0.02
BUY, SELL = 0.00075, 0.00125

# 三档书。键名会进流水，别轻易改（改名等于断掉历史序列）。
BOOKS = [
    {"k": "cur", "label": "现行", "types": None, "skip_weak": False,
     "h": {"强": 20, "中": 20, "弱": 20}, "hold": 20, "cap": None},
    {"k": "v3", "label": "v3全规则", "types": ("1买",), "skip_weak": True,
     "h": {"强": 8, "中": 1}, "hold": 8, "cap": 0.05},
    {"k": "v3d", "label": "v3-持5", "types": ("1买",), "skip_weak": True,
     "h": {"强": 5, "中": 5}, "hold": 5, "cap": 0.05},
]


def load_index(n=20):
    """返回 (axis, close, mom20)。axis 只含指数有收盘的日子，即交易日历。"""
    import pandas as pd
    clos = {}
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == IDX_CODE]
        for dt, c in zip(d["trade_date"].astype(str), d["close"].astype(float)):
            if math.isfinite(c) and c > 0:
                clos[dt.replace("-", "")[:8]] = c
    clos = dict(sorted(clos.items()))
    ds = list(clos)
    mom = {ds[i]: clos[ds[i]] / clos[ds[i - n]] - 1.0 for i in range(n, len(ds))}
    return ds, clos, mom


def net_of(c0, c1, buy_cost=BUY, sell_cost=SELL):
    """毛价差 → 净收益。成本默认沿用旧常量，保证不传参时逐位不变。"""
    return (c1 * (1.0 - sell_cost)) / (c0 * (1.0 + buy_cost)) - 1.0


def load_signals(asof, universe):
    recs = [json.loads(x) for x in open(SHADOW, encoding="utf-8") if x.strip()]
    keep = []
    for r in recs:
        if not r.get("pass"):
            continue
        if str(r["entry_date"]) > asof:
            continue                       # 未来函数：不许用 as-of 之后才出现的信号
        if universe == "mature" and not r.get("mature"):
            continue
        keep.append(r)
    return keep


def build_picks(rows, cfg, axis, apx, last_ax, buy_cost=BUY, sell_cost=SELL):
    """按一本书的规则把信号变成 Pick，并记录被规则挡掉的数量。

    到期日的三种情形必须分开，否则影子盘会系统性低估风险：
      done     个股序列走满 h 根 → 正常出场
      data_end 数据到日还没走满 → 真在途，按 as-of 收盘价估值
      halted   个股在 as-of 之前就没有K线了（停牌/退市）→ 资金锁死，
               不能释放现金。旧口径把它当"已出场"会把现金凭空放回来，
               继续拿去开新仓，等于用一笔卖不掉的票换了一笔新仓位。
    """
    by = defaultdict(list)
    rej = {"type": 0, "regime": 0, "nobar": 0}
    for x in rows:
        r, b, i = x["r"], x["b"], x["i"]
        if cfg["types"] and r["type"] not in cfg["types"]:
            rej["type"] += 1
            continue
        if cfg["skip_weak"] and x["reg"] == "弱":
            rej["regime"] += 1
            continue
        h = cfg["h"].get(x["reg"])
        if h is None:
            rej["regime"] += 1
            continue
        j = min(i + h, len(b["d"]) - 1)
        if j <= i:
            rej["nobar"] += 1
            continue
        trunc = (i + h) > (len(b["d"]) - 1)
        if trunc:
            status = "data_end" if b["d"][-1] >= axis[last_ax] else "halted"
        else:
            status = "done"
        # 到期日按交易日历推，停牌股也照样标出"本该哪天走"
        exp_ax = apx[r["entry_date"]] + h
        exp = axis[exp_ax] if exp_ax < len(axis) else None
        c0, c1 = b["c"][i], b["c"][j]
        ret = net_of(c0, c1, buy_cost, sell_cost)
        # 反解出「净收益等价」的出场价，供 build_ratio 用同一套成本重算路径
        exit_px = (1.0 + ret) * c0 * (1 + buy_cost) / (1 - sell_cost)
        # 未走完的仓位挂到日历之后（永不触发结算）：现金留在仓里，
        # 停牌的更不能提前释放。用 last_ax 会在最后一天统一平仓，
        # 把「持仓占比」这个指标抹成 0，也等于假装卖掉了卖不掉的票。
        exit_ax = len(axis) if trunc else apx[b["d"][i + h]]
        ratio = build_ratio(b, i, j, c0, exit_px, axis, apx,
                            buy_cost=buy_cost, sell_cost=sell_cost)
        if trunc:
            val = ratio[apx[b["d"][j]]]
            for ad in range(apx[b["d"][j]] + 1, last_ax + 1):
                ratio[ad] = val
        tag = {
            "code": r["code"], "name": r["name"], "type": r["type"],
            "h": h, "exp": exp, "hold_i": i, "exit_i": j,
            "status": status, "entry": r["entry_date"],
        }
        by[r["entry_date"]].append(
            Pick(r["code"], apx[r["entry_date"]], exit_ax, ratio, ret, tag=tag))
    return by, rej


def month_curve(axis, curve):
    out = {}
    for i, d in enumerate(axis):
        out[d[:6]] = round((curve[i] - 1.0) * 100, 2)
    return out


def tail_stats(curve, k):
    """最近 k 个交易日的区间收益（不年化，直接终值/起点）。"""
    if len(curve) <= k:
        k = len(curve) - 1
    if k <= 0:
        return 0.0
    return round((curve[-1] / curve[-1 - k] - 1.0) * 100, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="as-of 日 8 位，默认数据最后一天")
    ap.add_argument("--universe", choices=("live", "mature"), default="live",
                    help="live=全部已出现信号（默认，影子盘应如此）；mature=只取已走完，供对账")
    ap.add_argument("--rebuild", action="store_true", help="忽略旧流水，全量重写")
    ap.add_argument("--out", default=LEDGER)
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--min-ticket", type=float, default=None,
                    help="单笔最小金额占权益比例（如 0.003=0.3%%），做了才报可执行化对照")
    ap.add_argument("--slip", type=float, default=0.0,
                    help="单边滑点 bp，默认 0（= 现有口径，逐位不变）；51 = 全成本档")
    ap.add_argument("--comm", type=float, default=None,
                    help="佣金双边 bp，默认 20（买 7.5/卖 12.5）；0 = 完全不扣佣金")
    ap.add_argument("--cost-tag", default=None,
                    help="成本档标签。给了就把流水/状态写成 *_<tag>，"
                         "**绝不动 canonical 文件**——影子盘的历史不可改写，"
                         "换成本档必须另存，否则等于静默重述历史")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-log", action="store_true", help="不写运行留痕（自检用）")
    a = ap.parse_args()

    # 成本档换了就必须换文件名：canonical 流水是「历史不可改写」的，
    # 用 --rebuild 覆盖它等于把过去某天的权益改掉，且没人知道发生过。
    if a.cost_tag:
        if a.out == LEDGER:
            a.out = LEDGER.replace(".jsonl", "_%s.jsonl" % a.cost_tag)
        if a.state == STATE:
            a.state = STATE.replace(".json", "_%s.json" % a.cost_tag)

    BUY_C, SELL_C = HC.rates(a.comm, a.slip)
    TOT_BP = HC.total_bp(a.comm, a.slip)

    axis_all, iclos, mom = load_index()
    asof = a.asof or axis_all[-1]
    if asof not in iclos:
        raise SystemExit("as-of %s 不是交易日（指数无收盘）" % asof)

    sig = load_signals(asof, a.universe)
    if not sig:
        raise SystemExit("as-of %s 无可用信号" % asof)

    d_lo = min(r["entry_date"] for r in sig)
    axis = [d for d in axis_all if d_lo <= d <= asof]
    apx = {d: i for i, d in enumerate(axis)}
    last_ax = len(axis) - 1

    def reg(d):
        m = mom.get(d)
        if m is None:
            return None
        return "弱" if m < -TH else ("强" if m > TH else "中")

    P = load_price_panel(sorted({r["code"] for r in sig}), TDX)
    rows = []
    for r in sig:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None or r["entry_date"] not in apx:
            continue
        rows.append({"r": r, "b": b, "i": i, "reg": reg(r["entry_date"])})
    if not rows:
        raise SystemExit("信号与价格面板无交集")

    say = (lambda *x, **k: None) if a.json else print
    m_now = mom.get(asof)
    g_now = reg(asof)
    say("=" * 96)
    say("影子盘 v3 ｜as-of %s｜universe %s｜信号 %d 笔 / %d 个开仓日｜势 %s（20日 %+.2f%%）"
        % (asof, a.universe, len(rows), len({x["r"]["entry_date"] for x in rows}),
           g_now, (m_now or 0) * 100))
    say("=" * 96)

    # ---- 逐本书跑 ----
    books = {}
    for cfg in BOOKS:
        picks, rej = build_picks(rows, cfg, axis, apx, last_ax,
                                 buy_cost=BUY_C, sell_cost=SELL_C)
        v = Engine(axis, hold=cfg["hold"], buy_cost=BUY_C, sell_cost=SELL_C).run(
            picks, per_name_cap=cfg["cap"], record_daily=True)
        n_pick = sum(len(x) for x in picks.values())
        n_open = sum(1 for xs in picks.values() for p in xs
                    if p.tag["status"] in ("data_end", "halted"))
        books[cfg["k"]] = {
            "cfg": cfg, "picks": picks, "rej": rej, "res": v,
            "n_pick": n_pick, "n_open": n_open,
            "curve": v["curve"],
        }

    # ---- 账户对照 ----
    say("\n【账户对照】同一引擎、同一日历、成本 %.1fbp 双边（买 %.1fbp / 卖 %.1fbp）"
        % (TOT_BP, BUY_C * 1e4, SELL_C * 1e4))
    say("  %-10s %6s %6s %9s %8s %8s %7s %7s %6s" %
        ("口径", "笔数", "开仓日", "累计", "cagr", "mdd", "sharpe", "util", "在途"))
    for cfg in BOOKS:
        b = books[cfg["k"]]
        r = b["res"]
        say("  %-10s %6d %6d %+8.2f%% %+7.2f%% %+7.2f%% %+7.2f %6.1f%% %6d"
            % (cfg["label"], b["n_pick"], len(b["picks"]), r["total"],
               r["cagr"] or 0, r["mdd"], r["sharpe"], r["util"], b["n_open"]))
    say("  注：util 是时间加权资金占用；util 差一倍的 cagr 不可直接比大小。")
    say("      在途 = 数据到日仍未走完的仓位，按 as-of 收盘价并扣卖出费估值。")

    # ---- 可执行化：碎单闸门 ----
    mt = {}
    if a.min_ticket:
        say("\n【可执行化】单笔目标金额 < %.2f%% 权益的批次整批不投（100 万本金约 %.0f 元）"
            % (a.min_ticket * 100, a.min_ticket * 1e6))
        say("  %-10s %9s %9s %8s %8s %7s %6s"
            % ("口径", "原累计", "加闸门", "Δ", "util", "碎单笔", "成交批次"))
        for cfg in BOOKS:
            b = books[cfg["k"]]
            v2 = Engine(axis, hold=cfg["hold"], buy_cost=BUY_C,
                        sell_cost=SELL_C).run(
                b["picks"], per_name_cap=cfg["cap"], min_ticket=a.min_ticket)
            mt[cfg["k"]] = {"total": v2["total"], "util": v2["util"],
                            "n_tiny": v2["n_tiny"], "n_batch": v2["n_batch"],
                            "delta_pp": round(v2["total"] - b["res"]["total"], 2)}
            say("  %-10s %+8.2f%% %+8.2f%% %+7.2fpp %7.1f%% %7d %6d"
                % (cfg["label"], b["res"]["total"], v2["total"],
                   v2["total"] - b["res"]["total"], v2["util"],
                   v2["n_tiny"], v2["n_batch"]))
        say("  注：碎单不是『少赚』，是『根本没成交』——原曲线里那些 0.1%% 的仓位"
            "只存在于回测。".replace("%%", "%"))

    # ---- 同类口径下的“每单位占用”效率：避免用 util 差异掩盖问题 ----
    say("\n【资金效率】累计收益 / 平均占用（同一 util 口径下的粗略可比量）")
    for cfg in BOOKS:
        b = books[cfg["k"]]
        u = b["res"]["util"]
        eff = (b["res"]["total"] / u) if u > 0 else None
        say("  %-10s 累计 %+7.2f%%  util %5.1f%%  →  每 100%% 占用 %s"
            % (cfg["label"], b["res"]["total"], u,
               ("%+.2f%%" % eff) if eff is not None else "n/a"))

    # ---- 近 20 日与逐月 ----
    say("\n【区间】最近 20 个交易日收益（两套规则的差距在这里最直观）")
    for cfg in BOOKS:
        say("  %-10s %+7.2f%%" % (cfg["label"], tail_stats(books[cfg["k"]]["curve"], 20)))

    say("\n【逐月】月末累计净值（相对期初）")
    for cfg in BOOKS:
        mc = month_curve(axis, books[cfg["k"]]["curve"])
        say("  %-10s %s" % (cfg["label"],
                            " ".join("%s:%+.1f%%" % (k[4:], v) for k, v in sorted(mc.items()))))

    # ---- 今日状态 ----
    say("\n" + "-" * 96)
    say("【%s 收盘状态】在途仓位按收盘价估值（已扣卖出费），不是可再投的现金" % asof)
    say("-" * 96)
    for cfg in BOOKS:
        b = books[cfg["k"]]
        day = b["res"]["daily"][-1]
        n_halt = sum(1 for xs in b["picks"].values() for p in xs
                     if p.tag["status"] == "halted")
        last_e = max(b["picks"]) if b["picks"] else "—"
        say("  %-10s 权益 %.4f｜持仓市值 %5.1f%%｜在途 %3d 笔（停牌锁死 %d）"
            "｜今日开 %d 平 %d｜最后一次开仓 %s"
            % (cfg["label"], day["eq"], day["util"] * 100, day["n_live"], n_halt,
               len(day["open"]), len(day["close"]), last_e))
    cur = books["cur"]["res"]["daily"][-1]
    v3 = books["v3"]["res"]["daily"][-1]
    say("  → 持仓占比：v3 %.1f%% vs 现行 %.1f%%（低 %.1fpp，这是 v3 的择势在起作用）"
        % (v3["util"] * 100, cur["util"] * 100,
           (cur["util"] - v3["util"]) * 100))

    # ---- 被规则挡掉的 ----
    say("\n【规则挡掉的候选】（整段区间累计）")
    for cfg in BOOKS[1:]:
        b = books[cfg["k"]]
        d = b["rej"]
        say("  %-10s 类型挡 %d｜势挡 %d｜无后续K线 %d｜实际成交 %d"
            % (cfg["label"], d["type"], d["regime"], d["nobar"], b["n_pick"]))

    # ---- 在途明细 ----
    say("\n【在途仓位】截至 %s 仍未走完（按收盘价估值，非已实现）" % asof)
    for cfg in BOOKS:
        say("  %-10s %d 笔" % (cfg["label"], books[cfg["k"]]["n_open"]))
    show_k = max(BOOKS, key=lambda c: books[c["k"]]["n_open"])["k"]
    open_lst = [(p.tag, p) for xs in books[show_k]["picks"].values() for p in xs
                if p.tag["status"] in ("data_end", "halted")]
    open_lst.sort(key=lambda z: z[0]["entry"])
    if open_lst:
        say("  —— %s 明细（按开仓日）——" % books[show_k]["cfg"]["label"])
        say("  %-12s %-8s %-4s %-10s %-10s %5s %8s" %
            ("代码", "名称", "类型", "开仓", "原定到期", "已持", "浮动"))
        for tg, _ in open_lst[:10]:
            held = apx[asof] - apx[tg["entry"]]
            b = P.get(tg["code"])
            upnl = 0.0
            if b is not None and tg["exit_i"] < len(b["c"]) and tg["hold_i"] < len(b["c"]):
                upnl = net_of(b["c"][tg["hold_i"]], b["c"][tg["exit_i"]],
                              BUY_C, SELL_C) * 100
            say("  %-12s %-8s %-4s %-10s %-10s %4d日 %+7.2f%%"
                % (tg["code"], tg["name"][:8], tg["type"], tg["entry"],
                   tg["exp"] or ("T+%d" % tg["h"]), held, upnl))
        if len(open_lst) > 10:
            say("  … 另 %d 笔" % (len(open_lst) - 10))

    # ---- 单票上限自证 ----
    worst = 0.0
    for i in range(len(axis)):
        for cfg in BOOKS:
            if cfg["cap"] is None:
                continue
            day = books[cfg["k"]]["res"]["daily"][i]
            for o in day["open"]:
                worst = max(worst, o["w_pct"])
    say("\n【约束核验】设了单票上限的书，历史上单笔最大权重 %.2f%%（上限 %.0f%%）"
        % (worst, 5.0))

    # ---- 落盘 ----
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    lines = []
    for i, d in enumerate(axis):
        rec = {"d": d, "regime": reg(d), "books": {}}
        for cfg in BOOKS:
            day = books[cfg["k"]]["res"]["daily"][i]
            rec["books"][cfg["k"]] = {
                "eq": day["eq"], "cash": day["cash"], "util": day["util"],
                "n_live": day["n_live"],
                "open": day["open"], "close": day["close"],
                "skip": day["skip"], "drop": day["drop"],
                "pos": day["pos"],
            }
        rec["gap_pp"] = round((books["v3"]["curve"][i] - books["cur"]["curve"][i]) * 100, 4)
        lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True))

    # 历史不可改写：先算出全量，再与旧流水逐日比对。过去某天的权益若变了，
    # 那是数据回补/口径改动造成的「重述」，必须显式 --rebuild 才许覆盖，
    # 否则自动重写会让昨天的报告和今天的流水对不上，而且没人知道发生过。
    old = {}
    if os.path.exists(a.out):
        for ln in open(a.out, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                old[json.loads(ln)["d"]] = ln
            except (ValueError, KeyError):
                continue
    new = {json.loads(ln)["d"]: ln for ln in lines}
    restated = []
    for d in sorted(new):
        if d in old and old[d] != new[d]:
            restated.append(d)
    if restated and not a.rebuild:
        print("\n【拒绝写入】%d 个历史交易日的数字与旧流水不同（最近 %s）。"
              % (len(restated), restated[-1]))
        print("  这说明数据回补或口径被改过；确认要重述请加 --rebuild。")
        return 2
    body = "\n".join(new[d] for d in sorted(new)) + "\n"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(body)
    dig = hashlib.sha1(body.encode("utf-8")).hexdigest()
    if restated:
        print("\n【历史重述】%d 天被覆盖（最近 %s），流水 sha1 %s"
              % (len(restated), restated[-1], dig[:12]))

    state = {
        "asof": asof, "universe": a.universe, "regime": g_now,
        "idx": iclos.get(asof), "mom20_pct": round((m_now or 0) * 100, 4),
        "n_signal": len(rows), "n_entry_days": len({x["r"]["entry_date"] for x in rows}),
        "ledger": os.path.basename(a.out), "sha1": dig,
        # 第十八轮：净值对成本极度敏感，状态里必须写清这一份是哪个档算的。
        # 不写的话，两份 state 并排放着看不出差别，只能靠文件名猜。
        "cost": {
            "comm_bp": round(TOT_BP - 2 * a.slip, 1),
            "slip_bp": a.slip,
            "total_bp": round(TOT_BP, 1),
            "buy_rate": BUY_C, "sell_rate": SELL_C,
            "tag": a.cost_tag,
            "caliber": HC.describe(a.comm, a.slip),
        },
        "books": {},
    }
    for cfg in BOOKS:
        b = books[cfg["k"]]
        r = b["res"]
        state["books"][cfg["k"]] = {
            "label": cfg["label"], "hold": cfg["hold"], "cap": cfg["cap"],
            "total": r["total"], "cagr": r["cagr"], "mdd": r["mdd"],
            "sharpe": r["sharpe"], "util": r["util"], "n_batch": r["n_batch"],
            "n_pick": b["n_pick"], "n_open": b["n_open"],
            "rejected": b["rej"],
            "min_ticket": mt.get(cfg["k"]),
            "open_pos": [{"code": p.tag["code"], "name": p.tag["name"],
                          "type": p.tag["type"], "entry": p.tag["entry"],
                          "exp": p.tag["exp"], "h": p.tag["h"],
                          "status": p.tag["status"]}
                         for xs in b["picks"].values() for p in xs
                         if p.tag["status"] in ("data_end", "halted")],
        }
    with open(a.state, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    # 运行留痕：同 as-of + 同内容不重复追加
    last = None
    if os.path.exists(RUNLOG):
        with open(RUNLOG, encoding="utf-8") as f:
            ls = [ln for ln in f if ln.strip()]
        if ls:
            try:
                last = json.loads(ls[-1])
            except ValueError:
                last = None
    if not a.no_log and not (last and last.get("asof") == asof and last.get("sha1") == dig):
        with open(RUNLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "asof": asof, "universe": a.universe, "sha1": dig,
                "n_signal": len(rows), "regime": g_now,
                "rows": len(open(a.out, encoding="utf-8").readlines()),
            }, ensure_ascii=False) + "\n")

    if not a.json:
        print("\n流水 -> %s（%d 行，sha1 %s）" % (a.out, len(old) + len(lines), dig[:12]))
        print("状态 -> %s" % a.state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
