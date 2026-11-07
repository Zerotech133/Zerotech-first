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
# 密度源必须是「同一口径、全历史重跑」的扩展池。默认指权威池 v2
# （2026-11-08 重建：修了 _find_lag 的 MIN_BARS 下限，见 chanlun_ext_backfill）。
EXT_POOL = os.path.join(QBT, "data", "chanlun_pool_ext_v2.jsonl")
SHADOW = os.environ.get("COMBO_OUT", os.path.join(QBT, "data", "combo_shadow.jsonl"))
STATS = os.environ.get("COMBO_STATS",
                       os.path.join(ROOT, "portal", "combo_shadow_stats.json"))

BUY = ("1买", "2买")          # 剔 3买
HOLD = 20
TRAIL = 0.08
SIG_WIN = 20
# sigdens 阈值：冻结常数（不让它随池子漂移），但来源必须可复现——`--show-th`
# 会从当前池子实时重算分位。
#
# 2026-11-08 重标：0.4886 → 0.4852。旧值是在 chanlun_pool_ext_daily_full 上标的，
# 那个池子有两处污染（since 字符串比较 bug 把 2023 记录全推到 2024-01-02；
# fast 模式缺 MIN_BARS 下限让上市早期的信号也"可见"），数字不可复现。
# 新值来自权威池 chanlun_pool_ext_v2（1,724 笔门限堆叠已于 2023-09-25 剔除后
# 取 2023-11 起，730 个交易日）。敏感度：全历史 0.4830 / 2023-11 起 0.4852 /
# 2024 起 0.4853 —— 三者相差 0.002，判定不随窗口选择翻转。
SIG_TH = 0.4843
SIG_TH_REF = {"p20": 0.3659, "p30": 0.4843, "p50": 0.7254, "p70": 1.0972}
SIG_TH_POOL = "chanlun_pool_ext_v2.jsonl|axis=000001.SH|since=2023-11-01"
MIN_SETTLED_DAYS = 120       # 低于此值不给仓位建议（与 env_shadow 同标准）
SEED = 20261108              # 随机对照种子（固定，保证可复现）


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
    global EXT_POOL, SHADOW
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="忽略已有清单，全量重建")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-th", action="store_true", help="打印 sigdens 分位参考")
    ap.add_argument("--pool", default="", help="密度源扩展池（默认权威池 v2）")
    ap.add_argument("--shadow", default="", help="影子清单落盘路径（默认 data/combo_shadow.jsonl）")
    a = ap.parse_args()
    if a.pool:
        EXT_POOL = a.pool
    if a.shadow:
        SHADOW = a.shadow
    say = (lambda *x, **k: None) if a.json else print

    if not os.path.exists(EXT_POOL):
        say("密度源池不存在：%s" % EXT_POOL)
        return 2

    if a.show_th:
        say("冻结常数 SIG_TH = %.4f（标定来源 %s）" % (SIG_TH, SIG_TH_POOL))
        for k, v in SIG_TH_REF.items():
            say("  %s = %.4f" % (k.upper(), v))
        # 实时重算：同一池子上必须能复现上面的常数，否则说明池子换了/口径漂了
        ax = [d for d in load_axis() if d >= "20230101"]
        c = load_ext_counts(ax)
        pv = c.shift(1)
        dn = (pv / pv.rolling(SIG_WIN).mean()).replace([np.inf, -np.inf], np.nan)
        for lo, lab in (("20231101", "2023-11 起（默认）"), ("20240102", "2024 起"),
                        (None, "全历史")):
            x = dn.dropna()
            if lo:
                x = x[[d for d in x.index if d >= lo]]
            say("  [实时] %-16s n=%4d  P20=%.4f P30=%.4f P50=%.4f P70=%.4f"
                % (lab, len(x), *[float(np.quantile(x.values, p))
                                  for p in (0.2, 0.3, 0.5, 0.7)]))
        say("  池 %s：1买+2买 日均 %.1f 笔，最大 %d 笔"
            % (os.path.basename(EXT_POOL), c.mean(), int(c.max())))
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
                   "sig_th_ref": SIG_TH_REF, "min_settled_days": MIN_SETTLED_DAYS,
                   # 池子版本必须落盘：跨版本账户层绝对数字不可比（见 2026-11-08 根因报告）
                   "pool": os.path.basename(EXT_POOL)},
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

    # ---- 日内配对：同一天里 pass 组 vs fail 组 ----
    # 为什么必须做这个：`by_pass` 的笔均是全样本混算，pass 的日子本身就可能是
    # 更好的行情日（拥挤度低 = 市场不亢奋）。那样「过档更好」会混入选日效应。
    # 只在「同一天既有过档又有未过档记录」的日子里配对，选日效应被完全消掉，
    # 剩下的就是 sigdens 的纯选股增量。
    def paired_by_day(rows, field):
        by = defaultdict(lambda: {"p": [], "f": []})
        for r in rows:
            by[r["entry_date"]]["p" if r["pass"] else "f"].append(r[field])
        d = [np.mean(v["p"]) - np.mean(v["f"])
             for v in by.values() if v["p"] and v["f"]]
        if not d:
            return None
        a = np.array(d, float)
        sd = a.std(ddof=1) if len(a) > 1 else 0.0
        return {"days": len(a),
                "diff": round(float(a.mean()) * 100, 3),
                "win": round(float((a > 0).mean()) * 100, 1),
                "t": (round(float(a.mean() / (sd / np.sqrt(len(a)))), 2)
                      if sd > 0 else None)}

    sent["by_pass"]["paired_trail"] = paired_by_day(settled, "trail_ret")
    sent["by_pass"]["paired_hold"] = paired_by_day(settled, "hold_ret")
    sent["by_pass"]["note_paired"] = (
        "同一开仓日内 pass 组均值 - fail 组均值，只取两组都非空的日子；"
        "已消掉选日效应，是 sigdens 的纯选股增量。"
        "**实测返回 None 是结构性的**：sigdens 是全市场同日同值的日级因子，"
        "同一天里所有票的 pass 完全相同，不存在横截面差异 → 只能做「日与日」比较，"
        "选时假设检验见 null（随机抽同样天数）")

    # ---- 随机对照：sigdens 只是「少交易」还是真择时 ----
    # sigdens 是日级因子，pass 天数(75) < 全部有信号天数(98)。而**少交易本身
    # 就会改变日批均的分布**，必须证明真实选出的日子优于「随机抽同样天数」，
    # 否则「组合最优」只是把仓位压到少数几天。判据同 entry_timing_3y：
    # 真实值要优于随机分布的 95% 分位。
    def null_test(rows, rule, boot=200):
        f = "trail_ret" if rule == "trail" else "hold_ret"
        by = defaultdict(list)
        for r in rows:
            by[r["entry_date"]].append(r[f])
        all_days = np.array([np.mean(v) for v in by.values()], float)
        pass_days = [d for d in {r["entry_date"] for r in rows if r["pass"]}]
        n = len(pass_days)
        if n == 0 or n >= len(all_days):
            return None
        real = float(np.mean([np.mean(by[d]) for d in pass_days]))
        rng = np.random.default_rng(SEED)
        draws = np.array([all_days[rng.choice(len(all_days), n, replace=False)].mean()
                          for _ in range(boot)], float)
        p95 = float(np.percentile(draws, 95))
        p50 = float(np.percentile(draws, 50))
        # ---- 样本量闸门（2026-11-08 老田裁定）----
        # 到期开仓日 < MIN_SETTLED_DAYS 时，p 值**不得作为结论**：
        # 单窗口 p 值随窗口选择剧烈漂移，而分年拆解已证明 2024 方向相反。
        # 这里保留数字（供观察漂移），但把 verdict 降级为描述性、usable=False，
        # 下游任何仓位决策都必须看 usable 而不是 p_value。
        enough = n >= MIN_SETTLED_DAYS
        raw_verdict = ("优于随机 95% 分位" if real > p95 else
                       ("优于随机中位但未过 5%" if real > p50 else
                        "不如随机（等同少交易）"))
        return {"days": n, "universe": int(len(all_days)),
                "real": round(real * 100, 3),
                "null_p50": round(p50 * 100, 3),
                "null_p95": round(p95 * 100, 3),
                "p_value": round(float((draws >= real).mean()), 4),
                "usable_for_decision": enough,
                "verdict": (raw_verdict if enough else
                            "【描述性·不可用于决策】" + raw_verdict),
                "gate": ("样本充足（%d ≥ %d 日）" % (n, MIN_SETTLED_DAYS) if enough else
                         "样本不足（%d < %d 日）：p 值仅描述本窗口，"
                         "不得跨窗口/跨年份外推" % (n, MIN_SETTLED_DAYS))}

    sent["null"] = {"trail": null_test(settled, "trail"),
                    "hold": null_test(settled, "hold")}
    sent["null"]["note"] = (
        "随机抽同样天数的被动组合，看日批均零分布；sigdens 是日级因子，"
        "这一节才是它唯一有效的检验")

    # ---- 记账进度与 ETA（2026-11-08 校准）----
    # 「还差 45 天」是错口径：需要的是 45 个**已到期的 pass 开仓日**。
    # 三层折算：pass 日 / 有信号日 × 有信号日 / 日历交易日。
    # **分母只能用台账实际运行期**：窗口起点 20260128 早于第一条 1买/2买 记录
    # （20260422），那段「零信号日」会把密度压低、ETA 虚高。取尾部 60 个交易日
    # 的密度更能反映当前状态（池子信号密度本身在爬升）。
    n_days_all = len({r["entry_date"] for r in recs})
    n_days_pass_all = len({r["entry_date"] for r in recs if r["pass"]})
    n_days_pass_settled = len({r["entry_date"] for r in settled if r["pass"]})
    tail_lo = sorted({r["entry_date"] for r in recs})[-60:][0]
    _cal_tail = [d for d in axis if tail_lo <= d <= d_hi]
    _bd = defaultdict(list)
    for r in recs:
        _bd[r["entry_date"]].append(r)
    _tail_sig = [d for d in sorted(_bd) if d >= tail_lo]
    _tail_pass = [d for d in _tail_sig if any(r["pass"] for r in _bd[d])]
    _sig_rate = len(_tail_sig) / max(1, len(_cal_tail))
    _pass_rate = len(_tail_pass) / max(1, len(_tail_sig))
    _per_day = _sig_rate * _pass_rate
    _gap = max(0, MIN_SETTLED_DAYS - n_days_pass_settled)
    _trad = (_gap / _per_day) if _per_day > 0 else None
    sent["progress"] = {
        "settled_pass_days": n_days_pass_settled,
        "target": MIN_SETTLED_DAYS,
        "gap": _gap,
        "rate": {"tail_from": tail_lo, "tail_cal_days": len(_cal_tail),
                 "tail_signal_days": len(_tail_sig), "tail_pass_days": len(_tail_pass),
                 "signal_day_per_cal": round(_sig_rate, 3),
                 "pass_per_signal_day": round(_pass_rate, 3),
                 "pass_per_cal": round(_per_day, 3)},
        "eta_trading_days": None if _trad is None else int(round(_trad + HOLD)),
        "eta_note": ("尾部密度下还需 %.0f 个交易日补齐缺口，再加 %d 日成熟期 "
                     "→ 合计约 %d 个交易日（≈%.1f 个月）"
                     % (_trad, HOLD, _trad + HOLD, (_trad + HOLD) / 21.0)
                     if _trad is not None else "密度未知，无法估 ETA"),
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
    sent["usable_for_position"] = bool(n_days_pass >= MIN_SETTLED_DAYS)
    sent["decision_gate"] = (
        "可用：%d 个已到期开仓日 ≥ %d" % (n_days_pass, MIN_SETTLED_DAYS)
        if sent["usable_for_position"] else
        "禁止用于仓位决策：仅 %d/%d 个已到期开仓日；"
        "本清单所有 p 值与日批均均为「窗口内描述性」，"
        "不得跨窗口外推（分年拆解已证明 2024 方向相反）"
        % (n_days_pass, MIN_SETTLED_DAYS))
    sent["warn"] = ("sigdens 阈值来自三年样本内分位点，存在样本内偏差；"
                    "本清单唯一目的就是用前推样本把它证伪或证实。"
                    "**单窗口 p 值不构成结论**，判据只认 progress 里的到期开仓日数")
    json.dump(sent, open(STATS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    say("  出场分布（移动止损口径）：止损 %d 笔 / 持满 %d 笔"
        % (sent["exit_mix_trail"]["trail"], sent["exit_mix_trail"]["hold"]))
    say("  通过档 笔均 止损 %+.3f%% 持满 %+.3f%%（n=%d）"
        % (sent["by_pass"]["pass"]["trail"]["avg"],
           sent["by_pass"]["pass"]["hold"]["avg"], sent["by_pass"]["pass"]["trail"]["n"]))
    say("  未过档 笔均 止损 %+.3f%% 持满 %+.3f%%（n=%d）"
        % (sent["by_pass"]["fail"]["trail"]["avg"],
           sent["by_pass"]["fail"]["hold"]["avg"], sent["by_pass"]["fail"]["trail"]["n"]))
    for k in ("trail", "hold"):
        v = sent["null"].get(k)
        if v:
            say("  随机对照[%s] %d/%d 日  真实 %+.3f%%  随机中位 %+.3f%%  随机95%% %+.3f%%  p=%.3f  %s"
                % (k, v["days"], v["universe"], v["real"], v["null_p50"],
                   v["null_p95"], v["p_value"], v["verdict"]))
    say("  判定：%s" % verdict)
    pg = sent["progress"]
    say("  记账进度 %d/%d 个已到期 pass 开仓日（缺口 %d）；%s"
        % (pg["settled_pass_days"], pg["target"], pg["gap"], pg["eta_note"]))
    say("  决策闸门：%s" % sent["decision_gate"])
    say("落盘 %s" % STATS)


if __name__ == "__main__":
    main()
