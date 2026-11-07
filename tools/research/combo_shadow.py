# -*- coding: utf-8 -*-
"""组合影子清单：sigdens P30 入场过滤 + 移动止损 8% 出场（K-RES-20261108-COMBO）。

为什么挂这一组
--------------
三年扩展池（2024-01~2026-10，16,637 笔剔3买）里，出场规则全试过、选股规则也全试过，
唯一同时满足「过随机对照的显著性」+「阈值全程单调」两条的是：

  入场：sigdens（前一日信号数 / 前 20 日信号数均值）>= P30
  出场：移动止损 8%（peak 回撤 8% 离场，peak 从入场价起算）

三年回测：年化 10.74%、回撤 -21.43%、夏普 0.68（裸基线是 7.09% / -27.99% / 0.40）。
落地前必须做的一件事：**用真实前推样本把 t 值养上去**。三年回测里 sigdens 的
P30 阈值是在同一批数据上算出来的分位点，天然带样本内偏差；只有在产线池上
按固定常数前推记账，才能知道它是不是真的。

口径（与 entry_timing_3y.py / exit_rules_ext.py 严格一致）
----------------------------------------------------------
- 标的：chanlun_paper.json 的 1买/2买（**剔 3买**，3买占池 54%、单笔 -1.47%）
- sigdens(D) = cnt(D-1) / mean(cnt(D-21..D-2))，cnt = 当日 1买+2买 笔数
- 阈值：SIG_TH = 0.475，是三年扩展池 sigdens 的 P30（固定常数，**不许每日重算**，
  否则就是拿当天的分位点判当天的仓，属于前视）
- 入场：台账 entry_date / entry_px（原始价）
- 移动止损：从 ei+1 起逐日判 `low <= max(entry*(1-8%), peak*(1-8%))`，peak 初值 = 入场价；
  触发则按 `min(止损位, 开盘价)` 成交（跳空不占便宜）。到期（ei+20）仍持有则收盘卖出。
- 路径价：**复权价 close*adj_factor**，用 `k = entry_px / c_adj[ei]` 归一到原始价标度。
  用原始 close 会在 5-6 月除权高峰把跳空当真实下跌，提前触发止损（已在上轮实测到）。
- 成本：买 0.075% + 卖 0.125%
- 账户层：portfolio_mtm.Engine（全交易日历 + 逐日盯市 + 资金预算 1/hold + 现金约束），
  **不用** `Π(1+日批收益/20)` 那种算术复利写法——它与下注规模负相关时会系统性虚高 21pp

产出
----
- quant_backtest/data/combo_shadow.jsonl   每 (entry_date, code, type) 一条（gitignore）
- portal/combo_shadow_stats.json            四种口径并排的账户层结果

用法
----
    python tools/research/combo_shadow.py              # 增量：只补新笔、重算结算
    python tools/research/combo_shadow.py --rebuild    # 全量重建
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from portfolio_mtm import (BUY_COST, SELL_COST, Engine, Pick,  # noqa: E402
                           build_ratio, load_price_panel)

QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
PAPER = os.path.join(QBT, "data", "chanlun_paper.json")
EXT_POOL = os.path.join(QBT, "data", "chanlun_pool_ext_daily_full.jsonl")
SHADOW = os.environ.get("COMBO_OUT", os.path.join(QBT, "data", "combo_shadow.jsonl"))
STATS = os.environ.get("COMBO_STATS",
                       os.path.join(ROOT, "portal", "combo_shadow_stats.json"))

BUY = ("1买", "2买")          # 剔 3买
HOLD = 20
TRAIL = 0.08
SIG_WIN = 20
SIG_TH = 0.4886              # 三年扩展池 sigdens P30（全部记录口径，见 --show-th）
SIG_TH_REF = {"p20": 0.3828, "p30": 0.4886, "p50": 0.7234, "p70": 1.0950}
MIN_SETTLED_DAYS = 120       # 低于此值不给仓位建议（与 env_shadow 同标准）


def _n(s):
    return str(s or "").replace("-", "")[:8]


def load_ledger():
    """→ 产能台账里的 1买/2买（open + closed），已按 entry_date 归一。"""
    d = json.load(open(PAPER, encoding="utf-8"))
    out, seen = [], set()
    for tag, arr in (("closed", d.get("closed") or []), ("open", d.get("open") or [])):
        for x in arr:
            if x.get("type") not in BUY:
                continue
            ed = _n(x.get("entry_date") or x.get("detect_date"))
            if not ed or not x.get("entry_px"):
                continue
            key = (ed, x.get("code"), x.get("type"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "entry_date": ed, "code": x.get("code"), "type": x.get("type"),
                "name": x.get("name"), "entry_px": float(x["entry_px"]),
                "sig_dt": _n(x.get("sig_dt")), "ledger": tag,
                "ledger_ret": (float(x["ret"]) if x.get("ret") is not None else None),
                "ledger_exit": _n(x.get("exit_date")),
                "idx_ret": (float(x["idx_ret"]) if x.get("idx_ret") is not None else None),
            })
    return out


def load_ext_counts(axis):
    """→ 扩展池每日 1买+2买 笔数（**不筛 status**）。

    为什么密度必须用扩展池、不能用产线台账
    --------------------------------------
    产线台账的日信号数是 0.1 → 0.3 → 1.0 → 2.9 → 12.7 → 36.5 单调爬升的
    （2026-01 全月 1 笔，2026-09 日均 36.5 笔），这是**扫描管线逐步成熟**
    （补历史、放阈、扩股票池），不是市场情绪。拿它算 sigdens = 当日/前20日均值，
    等于在测「这个月的管线比上个月成熟多少」，任何阈值都切不到东西。

    扩展池是单一固定口径对全历史重跑，日计数是市场驱动的（7~89 笔/日）。
    另一个坑：**不能只数 status=='closed'**——持满 20 日才 closed，末尾 20 个
    交易日会被右删失（实测 2026-10 从 23.4 笔/日 掉到 1.7），sigdens 因此虚低。
    """
    cnt = defaultdict(int)
    for line in open(EXT_POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY:
            continue
        ed = _n(r.get("entry_date"))
        if ed:
            cnt[ed] += 1
    return pd.Series({d: float(cnt.get(d, 0)) for d in axis})


def load_axis():
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            d["trade_date"] = (d["trade_date"].astype(str)
                               .str.replace("-", "", regex=False).str[:8])
            out.append(d)
    return sorted(set(pd.concat(out, ignore_index=True)["trade_date"]))


def sim_trail(p, ei, entry_px, hold=HOLD, trail=TRAIL):
    """移动止损。返回 (exit_i, exit_px_原始标度, reason)。价格已按 k 归一。

    peak 初值必须是 entry_px：若写成 peak=max(0, entry*(1+trail)) 之类，
    入场当天就会触发；若 peak 只从「最高价」起算，则第一根跌停会绕过止损。
    """
    k = entry_px / p["c"][ei]
    hard = min(ei + hold, len(p["c"]) - 1)
    peak = entry_px
    for j in range(ei + 1, hard + 1):
        o, hi, lo = p["o"][j] * k, p["h"][j] * k, p["l"][j] * k
        if not np.isfinite(o):
            continue
        lvl = max(entry_px * (1.0 - trail), peak * (1.0 - trail))
        if lo <= lvl:
            return j, min(lvl, o), "trail"
        peak = max(peak, hi)
    return hard, p["c"][hard] * k, ("hold" if hard == ei + hold else "eod")


def sim_hold(p, ei, entry_px, hold=HOLD):
    hard = min(ei + hold, len(p["c"]) - 1)
    k = entry_px / p["c"][ei]
    return hard, p["c"][hard] * k, ("hold" if hard == ei + hold else "eod")


def net_ret(entry_px, exit_px):
    return (exit_px * (1.0 - SELL_COST)) / (entry_px * (1.0 + BUY_COST)) - 1.0


def load_jsonl(path):
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out[(r["entry_date"], r["code"], r["type"])] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="忽略已有清单，全量重建")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-th", action="store_true", help="打印 sigdens 分位参考")
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    if a.show_th:
        say("sigdens 分位（三年扩展池标定，固定常数用 P30=%.4f）:" % SIG_TH)
        for k, v in SIG_TH_REF.items():
            say("  %s = %.4f" % (k.upper(), v))
        return

    picks0 = load_ledger()
    axis_all = load_axis()
    last_bar0 = axis_all[-1] if axis_all else ""
    if not picks0:
        say("台账里没有 1买/2买")
        return
    d_lo = min(r["entry_date"] for r in picks0)
    d_hi = max(r["entry_date"] for r in picks0)
    # 日历必须铺到数据末端，不能只铺到「最后一个开仓日」或「最晚台账退出日」：
    # 持有满 20 日会跨过这两个边界，build_ratio 里 p["d"][xi] 找不到对应 axis 下标就
    # 直接 KeyError（实测卡在 20261106）。未到期的笔由 mature 标记排除，不靠日历截断。
    axis = [d for d in axis_all if d_lo <= d <= last_bar0]
    ap_ = {d: i for i, d in enumerate(axis)}

    # ---- sigdens（密度取扩展池，严格用「昨日为止」） ----
    # 不用产线台账的笔数：那是管线成熟度曲线，不是市场拥挤度（见 load_ext_counts）
    cnt = load_ext_counts(axis)
    prev = cnt.shift(1)
    dens = prev / prev.rolling(SIG_WIN).mean()
    dens = dens.replace([np.inf, -np.inf], np.nan)

    # ---- 结算 ----
    old = {} if a.rebuild else load_jsonl(SHADOW)
    codes = {r["code"] for r in picks0}
    P = load_price_panel(codes, TDX, start="20240101", ohlc=True)
    n_nocode = n_nobar = 0
    recs = []
    for r in picks0:
        p = P.get(r["code"])
        if p is None:
            n_nocode += 1
            continue
        ei = p["pos"].get(r["entry_date"])
        if ei is None:
            n_nobar += 1
            continue
        d = r["entry_date"]
        dv = dens.get(d, np.nan)
        sig = float(dv) if np.isfinite(dv) else None
        # sigdens 只用前一日及更早，所以「当日能否算」就等于「前 21 日有没有数据」
        passed = bool(sig is not None and sig >= SIG_TH)

        xt, px_t, why_t = sim_trail(p, ei, r["entry_px"])
        xh, px_h, why_h = sim_hold(p, ei, r["entry_px"])
        mature = (ei + HOLD) <= (len(p["c"]) - 1)

        base = {
            "entry_date": d, "code": r["code"], "type": r["type"],
            "name": r["name"], "entry_px": round(r["entry_px"], 4),
            "sigdens": round(sig, 4) if sig is not None else None,
            "pass": passed, "ledger": r["ledger"],
            # 冻结：首次入清单时的判定不改写（台账修订不回改历史记录）
        }
        prev_rec = old.get((d, r["code"], r["type"]))
        if prev_rec and not a.rebuild:
            base["pass"] = prev_rec["pass"]
            base["sigdens"] = prev_rec["sigdens"]
            base["first_seen"] = prev_rec.get("first_seen")

        rec = dict(base)
        rec.update({
            "trail_exit": p["d"][xt], "trail_days": xt - ei, "trail_why": why_t,
            "trail_ret": round(net_ret(r["entry_px"], px_t), 5),
            "hold_exit": p["d"][xh], "hold_days": xh - ei, "hold_why": why_h,
            "hold_ret": round(net_ret(r["entry_px"], px_h), 5),
            "mature": bool(mature),
            "ledger_ret": r["ledger_ret"],
            "idx_ret": r["idx_ret"],
            "ei": ei, "xt": xt, "xh": xh,
            # ei/xt/xh 都是**股票序列下标**，只能用于 build_ratio；
            # 日历下标必须单独存，否则算持仓天数会混基准（实测均持算出 509 日）。
            "xt_ax": ap_[p["d"][xt]], "xh_ax": ap_[p["d"][xh]],
            "axis_pos": ap_.get(d),
        })
        if not rec.get("first_seen"):
            rec["first_seen"] = pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M:%S")
        recs.append(rec)

    with open(SHADOW, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    settled = [r for r in recs if r["mature"]]
    say("台账 1买/2买 %d 笔（无行情 %d、无入场K线 %d）→ 清单 %d 笔，已到期 %d"
        % (len(picks0), n_nocode, n_nobar, len(recs), len(settled)))
    say("窗口 %s ~ %s，日历 %d 日；sigdens 可算 %d 日，通过阈值 %d 日"
        % (d_lo, d_hi, len(axis), int(dens.notna().sum()),
           len({r["entry_date"] for r in recs if r["pass"]})))

    # ---- 账户层：四种口径并排 ----
    eng = Engine(axis, hold=HOLD)

    def to_picks(rows, rule):
        by = defaultdict(list)
        for r in rows:
            p = P[r["code"]]
            xi = r["xt"] if rule == "trail" else r["xh"]
            # 由净收益反解退出价：sim 里的 exit_px 是复权标度，这里统一回到原始价标度，
            # 再交给 build_ratio 用 k 归一路径，避免两条标度混用。
            ret = r["trail_ret"] if rule == "trail" else r["hold_ret"]
            exit_px = (1.0 + ret) * r["entry_px"] * (1 + BUY_COST) / (1 - SELL_COST)
            ratio = build_ratio(p, r["ei"], xi, r["entry_px"], exit_px, axis, ap_)
            # Pick.entry_i / exit_i 都是**日历下标**，不能直接用股票序列下标（xi）：
            # 传错了引擎永远等不到 h[2] == i，仓位永不结算，下一日盯市就 KeyError。
            by[r["entry_date"]].append(Pick(r["code"], ap_[r["entry_date"]],
                                            ap_[p["d"][xi]], ratio, ret))
        return by

    # 只用「已到期」的笔做账户层，否则末段净值被未结算仓位稀释
    # ============ 组合层：三个口径，重心放在「与暴露无关」的两个 ============
    # 为什么要这么绕：Engine 默认每日投 1/hold 权益，而移动止损把平均持仓从 20 日
    # 压到 9 日，账户常态在场率只有 15~21%（持满口径 53%）。**累计收益因此被
    # 机械压向 0**——「回撤小」很可能只是「仓位小」，跟前面「少交易就会降回撤」
    # 是同一个陷阱。试过迭代标定 budget_frac 让 util 对齐，186 天窗口上会振荡、
    # 撞现金上限，得到的数字不可信（实测同一口径在 -0.44% 与 +17% 之间跳）。
    # 所以主判据改用两个**与暴露无关**的量：
    #   - 笔均（每笔净收益，配对比较：同一批信号、只换出场规则）
    #   - 日批均（每个开仓日等权，再把日等权 —— 消掉「某天信号多」的权重失真）
    # 账户层只作参考，并强制打印 util 提示不可横向比。
    def batch_mean(rows, rule):
        f = "trail_ret" if rule == "trail" else "hold_ret"
        by = defaultdict(list)
        for r in rows:
            by[r["entry_date"]].append(r[f])
        per_day = np.array([np.mean(v) for v in by.values()], float)
        return {"days": len(per_day),
                "avg": round(float(per_day.mean()) * 100, 3),
                "med": round(float(np.median(per_day)) * 100, 3),
                "win": round(float((per_day > 0).mean()) * 100, 1),
                "std": round(float(per_day.std(ddof=1)) * 100, 2) if len(per_day) > 1 else None}

    eng = Engine(axis, hold=HOLD)
    views, batches = {}, {}
    for lab, rows, rule in (
        ("全信号 + 持满20", settled, "hold"),
        ("全信号 + 移动止损8%", settled, "trail"),
        ("sigdens P30 + 持满20", [r for r in settled if r["pass"]], "hold"),
        ("sigdens P30 + 移动止损8%", [r for r in settled if r["pass"]], "trail"),
    ):
        if not rows:
            continue
        v = eng.run(to_picks(rows, rule))
        hold_mean = float(np.mean([(r["xt_ax"] if rule == "trail" else r["xh_ax"])
                                   - ap_[r["entry_date"]] for r in rows]))
        v["hold_mean"] = round(max(1.0, hold_mean), 2)
        views[lab] = v
        batches[lab] = batch_mean(rows, rule)
        say("  %-24s 笔数%-5d 均持%4.1f日  笔均%+7.3f%%  日批均%+7.3f%%(日胜率%4.1f%%)"
            % (lab, len(rows), hold_mean,
               np.mean([r["trail_ret"] if rule == "trail" else r["hold_ret"]
                        for r in rows]) * 100,
               batches[lab]["avg"], batches[lab]["win"]))
        say("  %-26s 账户层(默认1/20预算): 累计%7.2f%% 回撤%8.2f%% 夏普%6.2f util%5.1f%%"
            % ("", v["total"], v["mdd"], v["sharpe"], v["util"]))

    # ---- 笔级统计（做配对比较） ----
    def agg(rows, field):
        v = np.array([r[field] for r in rows], float)
        return {"n": len(v), "avg": round(float(v.mean()) * 100, 3),
                "med": round(float(np.median(v)) * 100, 3),
                "win": round(float((v > 0).mean()) * 100, 1)}

    pa = [r for r in settled if r["pass"]]
    nb = [r for r in settled if not r["pass"]]
    sent = {
        "asof": {"last_entry": d_hi, "n_days": len(axis),
                 "n_settled": len(settled), "n_pending": len(recs) - len(settled)},
        "window": [d_lo, d_hi],
        "params": {"sig_th": SIG_TH, "trail": TRAIL, "hold": HOLD, "drop3": True,
                   "sig_th_ref": SIG_TH_REF, "min_settled_days": MIN_SETTLED_DAYS},
        "batch": batches,
        "views": views,
        "note_views": ("主判据是 batch（日批均，与暴露/复利无关）；views 是账户层参考值，"
                       "各口径 util 差一倍以上，累计收益与回撤**不可横向比较**"),
        "by_pass": {
            "pass": {"days": len({r["entry_date"] for r in pa}),
                     "trail": agg(pa, "trail_ret"), "hold": agg(pa, "hold_ret")},
            "fail": {"days": len({r["entry_date"] for r in nb}),
                     "trail": agg(nb, "trail_ret"), "hold": agg(nb, "hold_ret")},
        },
        "exit_mix_trail": {
            "trail": sum(1 for r in settled if r["trail_why"] == "trail"),
            "hold": sum(1 for r in settled if r["trail_why"] == "hold"),
        },
    }

    # ---- 判定 ----
    # 判定用「日批均」，与仓位、复利、在场率全都无关，是这批信号最干净的效力指标
    base = batches.get("全信号 + 持满20") or {}
    best = batches.get("sigdens P30 + 移动止损8%") or {}
    n_days_pass = len({r["entry_date"] for r in pa})
    if not best or not base:
        verdict = "样本不足，继续记账"
    elif n_days_pass < MIN_SETTLED_DAYS:
        verdict = ("样本不足：组合口径仅 %d 个已到期开仓日（需 ≥%d），继续记账，"
                   "不作任何仓位建议" % (n_days_pass, MIN_SETTLED_DAYS))
    elif best["avg"] > base["avg"] + 0.30:
        verdict = "组合日批均优于裸基线 %.2fpp → 方向复制，继续养样本" \
                  % (best["avg"] - base["avg"])
    elif best["avg"] <= base["avg"]:
        verdict = "组合日批均不优于裸基线 → 三年结论未复制，撤销影子"
    else:
        verdict = "改善不足 0.30pp → 继续观察"
    sent["baseline"] = base
    sent["combo"] = best
    sent["verdict"] = verdict
    sent["warn"] = ("sigdens 阈值来自三年样本内分位点，存在样本内偏差；"
                    "本清单唯一目的就是用前推样本把它证伪或证实")
    json.dump(sent, open(STATS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    say("  出场分布（移动止损口径）：止损 %d 笔 / 持满 %d 笔"
        % (sent["exit_mix_trail"]["trail"], sent["exit_mix_trail"]["hold"]))
    say("  通过档 笔均 止损 %+.3f%% 持满 %+.3f%%（n=%d）"
        % (sent["by_pass"]["pass"]["trail"]["avg"],
           sent["by_pass"]["pass"]["hold"]["avg"], sent["by_pass"]["pass"]["trail"]["n"]))
    say("  未过档 笔均 止损 %+.3f%% 持满 %+.3f%%（n=%d）"
        % (sent["by_pass"]["fail"]["trail"]["avg"],
           sent["by_pass"]["fail"]["hold"]["avg"], sent["by_pass"]["fail"]["trail"]["n"]))
    say("  判定：%s" % verdict)
    say("落盘 %s" % STATS)


if __name__ == "__main__":
    main()
