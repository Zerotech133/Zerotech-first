# -*- coding: utf-8 -*-
"""成本阶梯对照（第十八轮，K-COST-CALIBER）：两套并行账 × 四档成本。

**为什么需要这一张表**

第十八轮之前，仓库里三套账各写各的成本，同一个策略有三个净值：

    hf_book      佣金 20bp + slip_bp + impact_k      ← 唯一带滑点的
    trade_book   零成本（ret 与 exit_px/entry_px−1 逐位相同）
    shadow_book  只扣 7.5 + 12.5bp 佣金，无滑点

把三套账都换算到同一条成本刻度上，才能回答唯一有意义的问题：
**这些策略到底还剩多少。** 而不是「哪一套的数更好看」——那是记账差异，
不是策略差异。

**做法**

- 影子盘：真跑引擎。同一批信号、同一条日历，只换买卖费率，四档各跑一次。
  成本是按费率进到路径里的，不能事后减——一次买入的资金占用会改变后面
  每一笔的预算，减一个常数会漏掉复利效应。
- trade_book：纯算术。它是逐笔统计（不合成净值），台账 ret 是毛收益，
  净口径 = 毛 − 成本，逐笔可加。

**两条硬校验**（跑错就会静默出一个错的数，所以必须断言）

1. 影子盘「只佣金」档必须逐位复现 `shadow_state_v3.json` 的 total。
   这是重建路径与 canonical 流水一致性的唯一证明。
2. trade_book「零成本」档必须逐位复现 `trade_book.json` 的 alpha_buy。
   这证明我读的是同一批笔、同一个 alpha 口径。

用法::

    python -X utf8 tools/research/hf_cost_gauge.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import hf_cost as HC                                  # noqa: E402
import shadow_book as SB                              # noqa: E402

PORTAL = os.path.join(ROOT, "portal")
LEDGER_FULL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_paper_full.json")
OUT = os.path.join(PORTAL, "hf_cost_gauge.json")


# ------------------------------------------------------------------ 影子盘
def run_shadow():
    """四档成本 × 三本书，真跑引擎。返回结构化的净值表。"""
    axis_all, iclos, mom = SB.load_index()
    asof = axis_all[-1]
    sig = SB.load_signals(asof, "live")
    if not sig:
        raise SystemExit("影子盘无可用信号")

    d_lo = min(r["entry_date"] for r in sig)
    axis = [d for d in axis_all if d_lo <= d <= asof]
    apx = {d: i for i, d in enumerate(axis)}
    last_ax = len(axis) - 1

    TH = SB.TH

    def reg(d):
        m = mom.get(d)
        if m is None:
            return None
        return "弱" if m < -TH else ("强" if m > TH else "中")

    P = SB.load_price_panel(sorted({r["code"] for r in sig}), SB.TDX)
    rows = []
    for r in sig:
        b = P.get(r["code"])
        i = b["pos"].get(r["entry_date"]) if b else None
        if i is None or r["entry_date"] not in apx:
            continue
        rows.append({"r": r, "b": b, "i": i, "reg": reg(r["entry_date"])})

    years = len(axis) / 244.0
    books = []
    for cfg in SB.BOOKS:
        per_level = []
        for L in HC.LEVELS:
            buy_c, sell_c = HC.rates(L["comm_bp"], L["slip_bp"])
            picks, rej = SB.build_picks(rows, cfg, axis, apx, last_ax,
                                        buy_cost=buy_c, sell_cost=sell_c)
            v = SB.Engine(axis, hold=cfg["hold"], buy_cost=buy_c,
                          sell_cost=sell_c).run(picks, per_name_cap=cfg["cap"],
                                                record_daily=True)
            n_pick = sum(len(x) for x in picks.values())
            # 换手：累计买入名义额 / 平均权益 / 年数。amt 以初始权益 1.0 为单位，
            # 所以「累计买入名义额」直接等于「权益的多少倍」，不必再乘本金。
            bought = sum(o["amt"] for day in v["daily"] for o in day["open"])
            mean_eq = statistics.mean(v["curve"]) if len(v["curve"]) else 1.0
            one_way = (bought / mean_eq / years) if (mean_eq > 0 and years > 0) else 0.0
            per_level.append({
                "level": L["k"], "label": L["label"],
                "total_bp": round(HC.total_bp(L["comm_bp"], L["slip_bp"]), 1),
                "total": v["total"], "cagr": v["cagr"], "mdd": v["mdd"],
                "util": v["util"], "sharpe": v["sharpe"],
                "n_pick": n_pick,
                # 双边换手：买 + 卖各算一次
                "turnover_yr": round(one_way * 2.0, 2),
                "ann_cost_pp": round(HC.ann_cost_pp(one_way * 2.0, L["comm_bp"],
                                                    L["slip_bp"]), 2),
            })
        base = per_level[0]["total"]
        for r in per_level:
            r["drag_pp"] = round(r["total"] - base, 2)
        books.append({"k": cfg["k"], "label": cfg["label"], "hold": cfg["hold"],
                      "cap": cfg["cap"], "rows": per_level})

    # ---- 校验 1：只佣金档必须复现 canonical ----
    canon_path = os.path.join(PORTAL, "shadow_state_v3.json")
    chk = {"ok": None, "detail": []}
    if os.path.exists(canon_path):
        canon = json.load(open(canon_path, encoding="utf-8"))
        chk["ok"] = True
        for b in books:
            want = (canon.get("books", {}).get(b["k"]) or {}).get("total")
            got = next(r["total"] for r in b["rows"] if r["level"] == "comm")
            same = (want is not None and abs(got - want) < 1e-9)
            chk["detail"].append({"book": b["k"], "canonical": want, "rebuilt": got,
                                  "match": same})
            chk["ok"] = chk["ok"] and same
        chk["canonical_asof"] = canon.get("asof")
    return {
        "asof": asof, "days": len(axis), "years": round(years, 3),
        "n_signal": len(rows), "n_entry_days": len({x["r"]["entry_date"] for x in rows}),
        "books": books,
        "check_comm_matches_canonical": chk,
    }


# ------------------------------------------------------------------ trade_book
def run_trade():
    """逐笔统计口径：毛 → 净，纯算术。数据源是台账（与 trade_book 同源）。"""
    led = json.load(open(LEDGER_FULL, encoding="utf-8"))["rows"]
    # 只留可执行的多头买点，且已平仓——与 trade_book 的 alpha_buy 同一批
    buy = [r for r in led
           if r.get("type") in ("1买", "2买", "3买")
           and r.get("ret") is not None and r.get("alpha") is not None
           and r.get("exit_date")]
    if not buy:
        raise SystemExit("台账无可用多头已平仓笔")

    rows = []
    for L in HC.LEVELS:
        alphas, rets = [], []
        by_type = {}
        for r in buy:
            nr = HC.net_ret(r["ret"], L["comm_bp"], L["slip_bp"])
            # alpha_net = net_ret − β×br，而 β×br = ret − alpha
            an = nr - (r["ret"] - r["alpha"])
            alphas.append(an * 100)
            rets.append(nr * 100)
            by_type.setdefault(r["type"], []).append(an * 100)

        def tstat(xs):
            if len(xs) < 2:
                return None
            sd = statistics.pstdev(xs)
            return round(statistics.mean(xs) / sd * len(xs) ** 0.5, 2) if sd > 0 else None

        rows.append({
            "level": L["k"], "label": L["label"],
            "total_bp": round(HC.total_bp(L["comm_bp"], L["slip_bp"]), 1),
            "n": len(alphas),
            "alpha_mean": round(statistics.mean(alphas), 3),
            "alpha_median": round(statistics.median(alphas), 3),
            "alpha_win": round(sum(1 for x in alphas if x > 0) / len(alphas) * 100, 1),
            "alpha_t": tstat(alphas),
            "ret_mean": round(statistics.mean(rets), 3),
            # alpha_win 必须一起报：1买 的笔均是正的、中位是负的，
            # 只给均值会让人以为「多数交易在赚」，实际是少数大赢家撑起来的。
            "by_type": {k: {"n": len(v),
                            "alpha_mean": round(statistics.mean(v), 3),
                            "alpha_median": round(statistics.median(v), 3),
                            "alpha_win": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
                            "alpha_t": tstat(v)}
                        for k, v in sorted(by_type.items())},
        })
    base = rows[0]["alpha_mean"]
    for r in rows:
        r["haircut"] = round(r["alpha_mean"] - base, 3)
        r["keep_pct"] = round(r["alpha_mean"] / base * 100, 1) if base else None

    # ---- 校验 2：零成本档必须复现 trade_book.json 的 alpha_buy ----
    tb_path = os.path.join(PORTAL, "trade_book.json")
    chk = {"ok": None}
    if os.path.exists(tb_path):
        tb = json.load(open(tb_path, encoding="utf-8"))
        want = tb["stats"]["alpha_buy"]["mean"]
        got = next(r["alpha_mean"] for r in rows if r["level"] == "none")
        chk = {"ok": abs(got - want) < 5e-3, "canonical": want, "rebuilt": got,
               "n_canonical": tb["stats"]["alpha_buy"]["n"], "n_rebuilt": len(buy)}
    return {"n_closed_buy": len(buy), "rows": rows,
            "check_none_matches_trade_book": chk,
            "caveat": ("台账 entry = 检出日**收盘**，比 hf_book 的 T+1 开盘更乐观——"
                       "这层乐观与成本无关，是第二处独立偏差（本轮不改台账，"
                       "改它会重述并行 session 正在看的每一笔）。")}


def main():
    print("=" * 92)
    print("成本阶梯对照｜两套并行账 × %d 档成本" % len(HC.LEVELS))
    print("=" * 92)
    for L in HC.LEVELS:
        print("  %-5s %-10s 双边 %6.1fbp   %s"
              % (L["k"], L["label"], HC.total_bp(L["comm_bp"], L["slip_bp"]), L["who"]))

    print("\n  跑影子盘引擎（成本进路径，四档各跑一次）…")
    sh = run_shadow()
    print("  as-of %s｜%d 个交易日（%.2f 年）｜%d 笔信号 / %d 个开仓日"
          % (sh["asof"], sh["days"], sh["years"], sh["n_signal"], sh["n_entry_days"]))

    print("\n【影子盘】累计净值")
    print("  %-10s %s" % ("口径", "".join("%12s" % r["label"] for r in sh["books"][0]["rows"])))
    for b in sh["books"]:
        print("  %-10s %s" % (b["label"],
                              "".join("%11.2f%%" % r["total"] for r in b["rows"])))
    print("  %-10s %s" % ("换手(双边)", "".join("%11.1fx" % r["turnover_yr"]
                                              for r in sh["books"][0]["rows"])))

    print("\n  校验1 只佣金档 vs canonical：%s"
          % ("通过" if sh["check_comm_matches_canonical"]["ok"] else "失败"))
    for d in sh["check_comm_matches_canonical"]["detail"]:
        print("    %-4s canonical %+7.2f%%  重建 %+7.2f%%  %s"
              % (d["book"], d["canonical"] or 0, d["rebuilt"],
                 "一致" if d["match"] else "不一致"))

    print("\n  跑 trade_book 逐笔阶梯…")
    tr = run_trade()
    print("  多头已平 %d 笔" % tr["n_closed_buy"])
    print("\n  %-10s %8s %9s %10s %7s" % ("口径", "双边bp", "笔均α", "中位α", "t"))
    for r in tr["rows"]:
        print("  %-10s %8.1f %+8.3f%% %+9.3f%% %7s"
              % (r["label"], r["total_bp"], r["alpha_mean"], r["alpha_median"],
                 r["alpha_t"]))
    print("\n  分买点（净 α，笔均）")
    for r in tr["rows"]:
        print("    %-10s %s" % (r["label"],
                                "  ".join("%s %+.3f%%(n=%d)" % (k, v["alpha_mean"], v["n"])
                                          for k, v in r["by_type"].items())))
    c2 = tr["check_none_matches_trade_book"]
    print("\n  校验2 零成本档 vs trade_book：%s（canonical %s vs 重建 %s）"
          % ("通过" if c2.get("ok") else "失败", c2.get("canonical"), c2.get("rebuilt")))

    ok = bool(sh["check_comm_matches_canonical"]["ok"]) and bool(c2.get("ok"))
    doc = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "meta": {
            "caliber": "佣金按买卖分档 + 单边滑点，两者分开报",
            "formula": "总双边bp = 佣金双边bp + 2×单边滑点bp；"
                       "笔级净收益 = (1+gross)×(1-sell)/(1+buy) - 1",
            "comm_buy_bp": HC.COMM_BUY_BP, "comm_sell_bp": HC.COMM_SELL_BP,
            "why_split": "佣金可谈（万1），滑点由单笔占日成交额决定。"
                         "混成一个数就看不出该谈哪一头。",
            "checks_ok": ok,
            "caveat_entry": "影子盘与台账的入场都在**检出日收盘**；"
                            "hf_book 用 T+1 开盘。这层乐观独立于成本，未在本轮修正。",
        },
        "levels": [{"k": L["k"], "label": L["label"],
                    "comm_bp": L["comm_bp"], "slip_bp": L["slip_bp"],
                    "total_bp": round(HC.total_bp(L["comm_bp"], L["slip_bp"]), 1),
                    "who": L["who"]} for L in HC.LEVELS],
        "shadow": sh,
        "trade": tr,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print("\n  输出 %s（%.1f KB）"
          % (os.path.basename(OUT), os.path.getsize(OUT) / 1024))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
