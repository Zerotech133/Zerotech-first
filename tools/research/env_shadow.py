# -*- coding: utf-8 -*-
"""环境分低档影子清单（K-RES-20261108-ENVSHADOW）。

为什么有这个东西
----------------
三年复核（`env_strat_3y.py` / K-RES-20261108-ENV3Y）的结论是：环境分不能做
「低于阈值禁开仓」的硬门槛，账户层方向是反的——3 年里 <40 档年化 6.53%、
夏普 1.58、回撤 -9.77%，是全部档位里最好的；≥60 档只有 2.35% / 0.52。

但那个结论不能直接拿来下单，原因有两条：
  1. <40 档的 alpha 仍是 -0.33%，赚的是超跌反弹的 beta，不是选股能力；
  2. 164 个开仓日 / 4733 笔，样本够看不够押。

所以唯一诚实的处理是挂进影子清单：每天按同一口径记账、20 日后结算，用
真实前推样本把样本量养上去。在样本够之前，它只是清单，不是加仓指令。

与 coarse_shadow 的区别
------------------------
粗信号影子记录的是「哪些股票出信号」；这里记录的是「哪些日子环境分处在低档」
——它是**日级**仓位提示，不是个股推荐。所以 jsonl 一天一条，不按 code 展开。

口径（与 portal/api_chanlun_buy._env 一致，用产线真 trend 字段）
---------------------------------------------------------------
  s_up  = min(1, 顺风占比/0.5) * 40         顺风 = pool 里 trend=="up"
  s_vol = clamp((当日买点数/前5个有买点日均值 - 0.5)/1.0) * 30
  s_pnl = clamp((截至D已平仓最近30笔均值% + 5)/10) * 30
  score = round(s_up + s_vol + s_pnl)

结算：当日买点在产线台账（chanlun_paper.json）里的已平仓 ret / alpha 等权。

产出
----
- quant_backtest/data/env_shadow.jsonl   每日一条（gitignore）
- portal/env_shadow_stats.json           分档统计，页面可直接读

用法
----
    python tools/research/env_shadow.py              # 全量重算（幂等，覆盖写）
    python tools/research/env_shadow.py --json       # 只落 JSON 不打印
"""
import argparse
import json
import os
from collections import defaultdict

import numpy as np
from scipy import stats as sps

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QBT = os.path.join(ROOT, "quant_backtest")
POOL = os.path.join(QBT, "data", "chanlun_pool.jsonl")
PAPER = os.path.join(QBT, "data", "chanlun_paper.json")
SHADOW = os.environ.get("ENVSHADOW_OUT",
                        os.path.join(QBT, "data", "env_shadow.jsonl"))
STATS = os.environ.get("ENVSHADOW_STATS",
                       os.path.join(ROOT, "portal", "env_shadow_stats.json"))

BUY = ("1买", "2买", "3买")
PNL_WIN = 30
PNL_MIN = 5
CUTS = (40, 60)
STABLE_FROM = "2026-04-15"   # 与 7 个月版 env_strat_backtest 对齐的起点
HOLD = 20
# 三年复核的账户层结果，作为影子样本的对照基准（portal/env3y_evidence.json）
HIST_REF = {"low_total": 19.68, "low_cagr": 6.53, "low_sharpe": 1.58,
            "high_total": 6.82, "high_cagr": 2.35, "high_sharpe": 0.52,
            "low_days": 164, "high_days": 317, "label": "3年扩展池 2024-01~2026-10"}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def load_pool():
    """→ {date: {'n': 买点数, 'up': 顺风数}}"""
    agg = defaultdict(lambda: {"n": 0, "up": 0})
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY or not r.get("detect_date"):
            continue
        a = agg[r["detect_date"]]
        a["n"] += 1
        if r.get("trend") == "up":
            a["up"] += 1
    return dict(agg)


def load_closed():
    """→ 已平仓买点（只留结算需要的字段）"""
    with open(PAPER, encoding="utf-8") as f:
        d = json.load(f)
    out = []
    for x in d.get("closed") or []:
        if x.get("type") not in BUY:
            continue
        if x.get("ret") is None or not x.get("detect_date"):
            continue
        out.append({
            "code": x.get("code"), "type": x["type"],
            "detect_date": x["detect_date"],
            "exit_date": x.get("exit_date") or "",
            "ret": float(x["ret"]),
            "alpha": float(x["alpha"]) if x.get("alpha") is not None else None,
            "idx_ret": float(x["idx_ret"]) if x.get("idx_ret") is not None else None,
        })
    return out


def build(pool, closed):
    dates = sorted(pool)
    done = sorted([c for c in closed if c["exit_date"]],
                  key=lambda c: c["exit_date"])
    # 当日批次：detect_date → 已结算记录
    by_day = defaultdict(list)
    for c in closed:
        if c["exit_date"]:
            by_day[c["detect_date"]].append(c)

    recs, done_i, roll = [], 0, []
    for i, d in enumerate(dates):
        while done_i < len(done) and done[done_i]["exit_date"] < d:
            roll.append(done[done_i]["ret"])
            done_i += 1
        rw = roll[-PNL_WIN:]
        avg_pnl = (sum(rw) / len(rw) * 100.0) if len(rw) >= PNL_MIN else 0.0

        n, up = pool[d]["n"], pool[d]["up"]
        share = (up / n) if n else 0.0
        s_up = min(1.0, share / 0.5) * 40.0
        prev = [pool[x]["n"] for x in dates[max(0, i - 5):i]]
        base = (sum(prev) / len(prev)) if prev else float(n or 1)
        ratio = (n / base) if base else 1.0
        s_vol = _clamp((ratio - 0.5) / 1.0) * 30.0
        s_pnl = _clamp((avg_pnl + 5.0) / 10.0) * 30.0
        score = int(round(s_up + s_vol + s_pnl))

        batch = by_day.get(d, [])
        ret = float(np.mean([x["ret"] for x in batch])) if batch else None
        al = [x["alpha"] for x in batch if x["alpha"] is not None]
        alpha = float(np.mean(al)) if al else None
        ix = [x["idx_ret"] for x in batch if x["idx_ret"] is not None]

        recs.append({
            "date": d, "score": score,
            "s_up": round(s_up), "s_vol": round(s_vol), "s_pnl": round(s_pnl),
            "n": n, "n_up": up, "up_share": round(share, 4),
            "ratio": round(ratio, 3), "avg_pnl": round(avg_pnl, 3),
            "settled": len(batch),
            "ret": round(ret, 5) if ret is not None else None,
            "alpha": round(alpha, 5) if alpha is not None else None,
            "idx_ret": round(float(np.mean(ix)), 5) if ix else None,
            "pending": ret is None,
        })
    return recs


def _band(recs, lo, hi):
    return [r for r in recs if not r["pending"] and r["ret"] is not None
            and (lo is None or r["score"] >= lo)
            and (hi is None or r["score"] < hi)]


def _stats(rs):
    if not rs:
        return {"days": 0, "n": 0}
    r = np.array([x["ret"] for x in rs], float)
    al = [x["alpha"] for x in rs if x["alpha"] is not None]
    eq, peak, mdd = 1.0, 1.0, 0.0
    curve = []
    for v in r:
        eq *= (1.0 + v / HOLD)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1.0)
        curve.append(eq)
    c = np.diff(np.log(np.asarray(curve)))
    return {
        "days": len(rs), "n": int(sum(x["settled"] for x in rs)),
        "signals": int(sum(x["n"] for x in rs)),
        "ret_avg": round(float(r.mean()) * 100, 2),
        "ret_win": round(float((r > 0).mean()) * 100, 1),
        "alpha_avg": round(float(np.mean(al)) * 100, 2) if al else None,
        "idx_avg": round(float(np.mean([x["idx_ret"] for x in rs
                                        if x["idx_ret"] is not None])) * 100, 2),
        "total": round((eq - 1) * 100, 2),
        "mdd": round(mdd * 100, 2),
        "sharpe": round(float(c.mean() / c.std(ddof=1) * np.sqrt(252)), 2)
        if len(c) > 2 and c.std(ddof=1) > 0 else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--from", dest="frm", default=STABLE_FROM,
                    help="只统计该日期之后（含）；传空串=全量")
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    pool = load_pool()
    closed = load_closed()
    if a.frm:
        pool = {d: v for d, v in pool.items() if d >= a.frm}
        closed = [c for c in closed if c["detect_date"] >= a.frm]
    recs = build(pool, closed)

    with open(SHADOW, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    done = [r for r in recs if not r["pending"]]
    low = _band(recs, None, CUTS[0])
    mid = _band(recs, CUTS[0], CUTS[1])
    high = _band(recs, CUTS[1], None)

    bands = [{"label": "<%d" % CUTS[0], **_stats(low)},
             {"label": "%d-%d" % CUTS, **_stats(mid)},
             {"label": ">=%d" % CUTS[1], **_stats(high)}]

    # 日级 Welch：低档 vs 高档
    la = [x["alpha"] for x in low if x["alpha"] is not None]
    ha = [x["alpha"] for x in high if x["alpha"] is not None]
    w = None
    if len(la) >= 2 and len(ha) >= 2:
        t, p = sps.ttest_ind(la, ha, equal_var=False)
        w = {"diff": round((float(np.mean(la)) - float(np.mean(ha))) * 100, 2),
             "t": round(float(t), 2), "p": float(p),
             "hi_n": len(la), "lo_n": len(ha)}

    # 判定：账户层累计 + 夏普，且**必须低档绝对收益为正**。
    # 只看「低档比高档好」是错的——三档全负时"相对最好"仍然是亏钱，
    # 那时候给"低档加仓"的建议等于让账户多亏（这是 7 个月版翻车的同型错误）。
    lo_t, hi_t = bands[0].get("total"), bands[2].get("total")
    lo_s, hi_s = bands[0].get("sharpe"), bands[2].get("sharpe")
    n_low = bands[0].get("days") or 0
    if lo_t is None or hi_t is None:
        verdict = "样本不足，继续观察"
    elif n_low < 120:
        verdict = ("样本不足：低档仅 %d 个已结算日（需 ≥120 日），继续记账，"
                   "不作任何仓位建议" % n_low)
    elif lo_t <= 0 or (lo_s or 0) <= 0:
        verdict = "低档绝对收益或夏普为负 → 撤销，beta 也没赚到"
    elif lo_t > hi_t + 5 and (lo_s or 0) > (hi_s or 0):
        verdict = "低档绝对收益与夏普双优 → 可作低档加仓提示"
    elif lo_t < hi_t - 5:
        verdict = "低档劣 → 三年结论未复制，撤销影子"
    else:
        verdict = "无显著差异 → 继续观察"

    stats = {
        "asof": {"last_date": recs[-1]["date"] if recs else "",
                 "last_score": recs[-1]["score"] if recs else None,
                 "n_days": len(recs), "n_settled": len(done),
                 "n_pending": len(recs) - len(done)},
        "window": [recs[0]["date"], recs[-1]["date"]] if recs else [],
        "from": a.frm or "",
        "cuts": list(CUTS),
        "bands": bands,
        "welch_alpha": w,
        "verdict": verdict,
        "hist_ref": HIST_REF,
        "warn": "低档 alpha 若仍为负，则收益是超跌反弹 beta，只能当仓位参考，不能当选股能力",
    }
    json.dump(stats, open(STATS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    say("环境分影子清单：%d 日（已结算 %d，待结算 %d）  %s~%s"
        % (len(recs), len(done), len(recs) - len(done),
           recs[0]["date"] if recs else "", recs[-1]["date"] if recs else ""))
    say("  %-8s %5s %6s %8s %8s %9s %9s %7s"
        % ("档位", "日数", "笔数", "日ret均", "日alpha", "累计", "回撤", "夏普"))
    for b in bands:
        say("  %-8s %5s %6s %7s%% %7s%% %8s%% %8s%% %7s"
            % (b["label"], b.get("days"), b.get("n"), b.get("ret_avg", "-"),
               b.get("alpha_avg", "-"), b.get("total", "-"),
               b.get("mdd", "-"), b.get("sharpe", "-")))
    if w:
        say("  低档-高档 alpha 差 %+.2fpp  t=%.2f  p=%.3f" % (w["diff"], w["t"], w["p"]))
    say("  判定：%s" % verdict)
    say("  对照（%s）：低档累计 %+.2f%%/夏普 %.2f，高档 %+.2f%%/夏普 %.2f"
        % (HIST_REF["label"], HIST_REF["low_total"], HIST_REF["low_sharpe"],
           HIST_REF["high_total"], HIST_REF["high_sharpe"]))
    say("落盘 %s" % STATS)


if __name__ == "__main__":
    main()
