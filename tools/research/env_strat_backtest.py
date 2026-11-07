# -*- coding: utf-8 -*-
"""环境分历史分层回测（K-RES-20261107-ENVSTRAT）。

问题：/bt-buy 的环境分（顺风 40 + 量能 30 + 持仓浮盈 30）目前只是"提示"。
若高分档买点事后胜率/超额显著更高，它就该升级为"硬条件"（低于阈值禁开仓）。

四层防护，任一不成立结论都要打折：
  1. 主指标用 alpha（已扣 beta×指数收益）而非 ret —— 否则环境分可能只是
     "高分日=大盘上涨"的 beta 探测器，看着有效实则没有选信号能力。
  2. 按日期聚类做显著性检验 —— 同日几十笔信号同 beta、同市场，笔级 t 检验
     会把有效样本从 ~126 天虚增到 ~1800 笔，严重高估显著性。
  3. 双口径对照 —— 全因子（页面口径）vs 二因子（纯池，不依赖持仓代理），
     两版同向才算硬。
  4. 分段稳健性 —— 前后两段各跑一遍，避免单一时期驱动。

数据窗口：2026-04-15 起。此前池每日买点仅 1-5 只（覆盖度突变：全信号 5→30），
量能因子基数极小会把 ratio 算爆、虚假满分。该窗口损失仅 10 笔 / 9 个日期。

用法：
    python tools/research/env_strat_backtest.py            # 打印报告 + 落 JSON
    python tools/research/env_strat_backtest.py --json     # 只落 JSON
"""
import argparse
import json
import os
import statistics as st
from collections import defaultdict

import numpy as np
from scipy import stats as sps

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QBT = os.path.join(ROOT, "quant_backtest")
POOL = os.path.join(QBT, "data", "chanlun_pool.jsonl")
PAPER = os.path.join(QBT, "data", "chanlun_paper.json")
# 落 portal/ 而非 quant_backtest/reports/：后者被 .gitignore 排除，
# 生产部署时页面会因缺文件而静默不渲染证据块。结论快照必须随代码走。
OUT = os.path.join(ROOT, "portal", "env_strat_evidence.json")

BUY = ("1买", "2买", "3买")
STABLE_FROM = "2026-04-15"
PNL_WIN = 30          # 持仓浮盈代理的滚动窗口（笔）
PNL_MIN = 5           # 少于该笔数时该因子取中性
BOOT = 10000
SEED = 20261107


# ---------------------------------------------------------------- 数据

def load_pool():
    """→ {date: {'n': 买点数, 'up': 顺风买点数}}，只含有买点的日期。"""
    agg = defaultdict(lambda: {"n": 0, "up": 0})
    with open(POOL, encoding="utf-8") as f:
        for line in f:
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
    """→ 已平仓买点列表（字段精简）。"""
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
            "trend": (x.get("tags") or {}).get("reg"),
        })
    return out


# ---------------------------------------------------------------- 环境分重建

def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def build_env(pool, closed):
    """按日重建环境分。

    三因子与 api_chanlun_buy._env 一致：
      s_up  = min(1, 顺风占比/0.5) * 40
      s_vol = clamp((当日买点数/前5个有买点日均值 - 0.5)/1.0) * 30
      s_pnl = clamp((滚动已实现均值 +5)/10) * 30

    差异：s_pnl 原式用"当日持仓浮盈均值"（无历史快照，不可重建）。这里用
    "截至 D 日已平仓买点最近 30 笔的平均 ret"——同样衡量"最近账本暖不暖"，
    且严格因果（只用 exit_date < D 的），是可得的最接近代理。
    """
    dates = sorted(pool)
    done = sorted([c for c in closed if c["exit_date"]],
                  key=lambda c: c["exit_date"])
    env = {}
    for i, d in enumerate(dates):
        n, up = pool[d]["n"], pool[d]["up"]
        # 顺风
        up_share = (up / n) if n else 0.0
        s_up = min(1.0, up_share / 0.5) * 40.0
        # 量能（前 5 个有买点日，因果）
        prev = [pool[x]["n"] for x in dates[max(0, i - 5):i]]
        base = (sum(prev) / len(prev)) if prev else float(n or 1)
        ratio = (n / base) if base else 1.0
        s_vol = _clamp((ratio - 0.5) / 1.0) * 30.0
        # 持仓浮盈代理（只用已平仓且早于 D 的）
        past = [c for c in done if c["exit_date"] < d]
        roll = past[-PNL_WIN:]
        avg_pnl = (sum(c["ret"] for c in roll) / len(roll) * 100.0) \
            if len(roll) >= PNL_MIN else 0.0
        s_pnl = _clamp((avg_pnl + 5.0) / 10.0) * 30.0

        env[d] = {
            "score": int(round(s_up + s_vol + s_pnl)),
            "score2": int(round((s_up + s_vol) * 100.0 / 70.0)),  # 二因子归一
            "s_up": round(s_up), "s_vol": round(s_vol), "s_pnl": round(s_pnl),
            "n": n, "up": up, "up_share": up_share, "ratio": ratio,
            "avg_pnl_proxy": avg_pnl, "pnl_n": len(roll),
        }
    return env


# ---------------------------------------------------------------- 统计

def agg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0, "win": None, "avg": None, "med": None, "std": None}
    v = np.asarray(vals, dtype=float) * 100.0
    return {"n": len(v),
            "win": round(float((v > 0).mean() * 100), 1),
            "avg": round(float(v.mean()), 2),
            "med": round(float(np.median(v)), 2),
            "std": round(float(v.std(ddof=1)) if len(v) > 1 else 0.0, 2)}


def day_level(rows):
    """按日聚合 → [(date, n, avg_ret, avg_alpha, win_rate)]。"""
    g = defaultdict(list)
    for r in rows:
        g[r["detect_date"]].append(r)
    out = []
    for d, rs in sorted(g.items()):
        out.append({
            "date": d, "n": len(rs),
            "ret": st.fmean(r["ret"] for r in rs) * 100,
            "alpha": st.fmean(r["alpha"] for r in rs if r["alpha"] is not None) * 100,
            "win": sum(1 for r in rs if r["ret"] > 0) / len(rs) * 100,
        })
    return out


def welch(a, b):
    """Welch t 检验（a vs b），返回 (diff, t, p)。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return None, None, None
    t, p = sps.ttest_ind(a, b, equal_var=False)
    return float(a.mean() - b.mean()), float(t), float(p)


def boot_diff(hi_days, lo_days, key="alpha", n=BOOT, seed=SEED):
    """日级 bootstrap：高档日均值 - 低档日均值 的 95% CI。"""
    if not hi_days or not lo_days:
        return None
    rng = np.random.default_rng(seed)
    hi = np.asarray(hi_days, float)
    lo = np.asarray(lo_days, float)
    diffs = np.empty(n)
    for i in range(n):
        diffs[i] = (rng.choice(hi, len(hi), replace=True).mean()
                    - rng.choice(lo, len(lo), replace=True).mean())
    return {"lo": round(float(np.percentile(diffs, 2.5)), 3),
            "hi": round(float(np.percentile(diffs, 97.5)), 3),
            "mid": round(float(diffs.mean()), 3),
            "p_one_sided": round(float((diffs <= 0).mean()), 4)}


def spearman(xs, ys):
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    if len(x) < 3:
        return None, None
    r, p = sps.spearmanr(x, y)
    return round(float(r), 3), float(p)


# ---------------------------------------------------------------- 分档

def band_by_threshold(rows, field, cuts):
    """按固定阈值分档 → [(label, rows)]。cuts 递增。"""
    labels = []
    edges = [None] + list(cuts) + [None]
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if lo is None and hi is None:
            lab = "全部"
        elif lo is None:
            lab = "<%d" % hi
        elif hi is None:
            lab = ">=%d" % lo
        else:
            lab = "%d-%d" % (lo, hi)
        labels.append((lab, [r for r in rows
                             if (lo is None or r[field] >= lo)
                             and (hi is None or r[field] < hi)]))
    return labels


def band_by_quantile(rows, field, qs=(1 / 3, 2 / 3)):
    vals = np.asarray([r[field] for r in rows], float)
    q1, q2 = np.quantile(vals, qs)
    return [("低段 <%.0f" % q1, [r for r in rows if r[field] < q1]),
            ("中段 %.0f-%.0f" % (q1, q2), [r for r in rows
                                           if q1 <= r[field] < q2]),
            ("高段 ≥%.0f" % q2, [r for r in rows if r[field] >= q2])], \
        (float(q1), float(q2))


def report_band(bands, tag):
    print("\n  [%s]" % tag)
    print("  %-14s %5s %8s %8s %9s %9s %9s"
          % ("档位", "笔数", "ret胜率", "ret均值", "alpha胜率", "alpha均值", "alpha中位"))
    for lab, rs in bands:
        a, al = agg(rs, "ret"), agg(rs, "alpha")
        print("  %-14s %5d %7s%% %7s%% %8s%% %8s%% %8s%%"
              % (lab, len(rs), a["win"], a["avg"], al["win"], al["avg"], al["med"]))
    return [{"label": lab, "n": len(rs),
             "ret": agg(rs, "ret"), "alpha": agg(rs, "alpha")}
            for lab, rs in bands]


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pool = load_pool()
    closed = load_closed()
    env = build_env(pool, closed)

    # 样本：稳定期 + 有环境分
    rows = []
    for c in closed:
        d = c["detect_date"]
        if d < STABLE_FROM or d not in env:
            continue
        e = env[d]
        rows.append(dict(c, **{k: e[k] for k in
                               ("score", "score2", "s_up", "s_vol", "s_pnl")}))
    rows.sort(key=lambda r: (r["detect_date"], r["code"] or ""))

    dropped = [c for c in closed if c["detect_date"] < STABLE_FROM]
    if not args.json:
        print("=" * 92)
        print("环境分历史分层回测 · K-RES-20261107-ENVSTRAT")
        print("=" * 92)
        print("样本：%d 笔买入信号 / %d 个交易日（%s → %s）"
              % (len(rows), len(day_level(rows)),
                 rows[0]["detect_date"], rows[-1]["detect_date"]))
        print("剔除：%d 笔 / %d 个日期（< %s，池覆盖度突变前，量能因子失真）"
              % (len(dropped), len({c["detect_date"] for c in dropped}),
                 STABLE_FROM))

    res = {"meta": {
        "sample": len(rows), "days": len(day_level(rows)),
        "window": [rows[0]["detect_date"], rows[-1]["detect_date"]],
        "dropped": len(dropped), "stable_from": STABLE_FROM,
        "pnl_proxy": "rolling %d closed buys, exit_date < D" % PNL_WIN,
    }}

    # ---- 环境分分布
    scores = np.asarray([r["score"] for r in rows])
    res["dist"] = {
        "score": {"min": int(scores.min()), "max": int(scores.max()),
                  "med": float(np.median(scores)),
                  "q25": float(np.percentile(scores, 25)),
                  "q75": float(np.percentile(scores, 75))},
    }
    if not args.json:
        print("\n环境分分布：min %d / q25 %.0f / 中位 %.0f / q75 %.0f / max %d"
              % (scores.min(), np.percentile(scores, 25), np.median(scores),
                 np.percentile(scores, 75), scores.max()))

    # ---- 双口径分档
    bands3, cuts3 = band_by_quantile(rows, "score")
    bands2, cuts2 = band_by_quantile(rows, "score2")
    page_bands = [("只看不动 <40", [r for r in rows if r["score"] < 40]),
                  ("轻仓精选 40-59", [r for r in rows if 40 <= r["score"] < 60]),
                  ("可试仓 >=60", [r for r in rows if r["score"] >= 60])]

    if not args.json:
        print("\n" + "-" * 92)
        print("一、笔级分档（主报告）")
        print("-" * 92)
    res["band_page"] = report_band(page_bands, "页面阈值口径（<40 / 40-59 / >=60）")
    res["band_q3"] = report_band(bands3, "三分位（全因子 score，切点 %.0f/%.0f）" % cuts3)
    res["band_q2"] = report_band(bands2, "三分位（二因子 score2，纯池无持仓依赖）切点 %.0f/%.0f" % cuts2)

    # ---- 日级聚类检验
    dl = day_level(rows)
    for r in dl:
        r["score"] = env[r["date"]]["score"]
        r["score2"] = env[r["date"]]["score2"]
    q1, q2 = cuts3
    hi_d = [r for r in dl if r["score"] >= q2]
    lo_d = [r for r in dl if r["score"] < q1]
    mid_d = [r for r in dl if q1 <= r["score"] < q2]

    for key in ("alpha", "ret"):
        d_, t_, p_ = welch([r[key] for r in hi_d], [r[key] for r in lo_d])
        ci = boot_diff([r[key] for r in hi_d], [r[key] for r in lo_d], key)
        res.setdefault("day_cluster", {})[key] = {
            "hi_days": len(hi_d), "lo_days": len(lo_d),
            "hi_avg": round(float(np.mean([r[key] for r in hi_d])), 3),
            "lo_avg": round(float(np.mean([r[key] for r in lo_d])), 3),
            "diff": None if d_ is None else round(d_, 3),
            "t": None if t_ is None else round(t_, 2),
            "p": None if p_ is None else round(p_, 4),
            "boot": ci,
        }

    rho_a, p_a = spearman([r["score"] for r in dl], [r["alpha"] for r in dl])
    rho_r, p_r = spearman([r["score"] for r in dl], [r["ret"] for r in dl])
    res["spearman"] = {"alpha": {"rho": rho_a, "p": p_a},
                       "ret": {"rho": rho_r, "p": p_r}}

    if not args.json:
        print("\n" + "-" * 92)
        print("二、日级聚类检验（126 个交易日，避免同日信号重复计数）")
        print("-" * 92)
        for key, lab in (("alpha", "超额 alpha"), ("ret", "绝对 ret")):
            c = res["day_cluster"][key]
            print("  %s：高档 %d 日 均值 %.2f%% | 低档 %d 日 均值 %.2f%% | 差 %+.2f%% "
                  "| Welch t=%.2f p=%.4f | bootstrap 95%%CI [%+.2f, %+.2f]"
                  % (lab, c["hi_days"], c["hi_avg"], c["lo_days"], c["lo_avg"],
                     c["diff"], c["t"], c["p"],
                     c["boot"]["lo"], c["boot"]["hi"]))
        print("  日级 Spearman：alpha rho=%+.3f (p=%.4f) | ret rho=%+.3f (p=%.4f)"
              % (rho_a, p_a, rho_r, p_r))

    # ---- 环境分是不是 beta 探测器
    idx = [r["idx_ret"] for r in rows if r["idx_ret"] is not None]
    if idx:
        dmap = defaultdict(list)
        for r in rows:
            if r["idx_ret"] is not None:
                dmap[r["detect_date"]].append(r["idx_ret"])
        xs = [env[d]["score"] for d in sorted(dmap)]
        ys = [st.fmean(v) * 100 for d, v in sorted(dmap.items())]
        rho_i, p_i = spearman(xs, ys)
        res["beta_check"] = {"rho": rho_i, "p": p_i,
                             "hi_idx": round(float(np.mean(
                                 [env[d]["score"] and v for d, v
                                  in zip(sorted(dmap), ys)
                                  if env[d]["score"] >= q2])), 2),
                             "lo_idx": round(float(np.mean(
                                 [v for d, v in zip(sorted(dmap), ys)
                                  if env[d]["score"] < q1])), 2)}
        if not args.json:
            print("\n  beta 检查：环境分 vs 持有期指数收益 rho=%+.3f (p=%.4f)；"
                  "高档日指数 %.2f%% vs 低档日 %.2f%%"
                  % (rho_i, p_i, res["beta_check"]["hi_idx"],
                     res["beta_check"]["lo_idx"]))

    # ---- 分段稳健性
    segs = [("前段 04-15~07-31", "2026-04-15", "2026-08-01"),
            ("后段 08-01~10-30", "2026-08-01", "2026-12-31")]
    res["segments"] = {}
    for lab, a, b in segs:
        sub = [r for r in rows if a <= r["detect_date"] < b]
        if len(sub) < 50:
            continue
        bs, _ = band_by_quantile(sub, "score")
        res["segments"][lab] = report_band(bs, lab + "（%d 笔）" % len(sub))

    # ---- 剔除 3买 后
    sub12 = [r for r in rows if r["type"] in ("1买", "2买")]
    bs12, _ = band_by_quantile(sub12, "score")
    res["excl_3buy"] = report_band(bs12, "剔除 3买 后（只剩 1买/2买，%d 笔）" % len(sub12))

    # ---- 阈值扫描（实战：用多少机会换多少胜率）
    scan = []
    for th in range(20, 96, 5):
        keep = [r for r in rows if r["score"] >= th]
        if len(keep) < 30:
            break
        a, al = agg(keep, "ret"), agg(keep, "alpha")
        scan.append({"th": th, "n": len(keep),
                     "cover": round(len(keep) / len(rows) * 100, 1),
                     "ret_win": a["win"], "ret_avg": a["avg"],
                     "alpha_win": al["win"], "alpha_avg": al["avg"]})
    res["threshold_scan"] = scan
    if not args.json and scan:
        print("\n" + "-" * 92)
        print("三、阈值扫描（若把环境分当硬门槛，机会 vs 质量）")
        print("-" * 92)
        print("  %-6s %6s %8s %9s %9s %10s %10s"
              % ("门槛", "笔数", "机会占比", "ret胜率", "ret均值", "alpha胜率", "alpha均值"))
        for s in scan:
            print("  >=%-4d %6d %7.1f%% %8s%% %8s%% %9s%% %9s%%"
                  % (s["th"], s["n"], s["cover"], s["ret_win"], s["ret_avg"],
                     s["alpha_win"], s["alpha_avg"]))

    # ---- 检验 A：高档日集中度 + 逐日 jackknife
    hi_dates = sorted(r["date"] for r in hi_d)
    lo_dates = sorted(r["date"] for r in lo_d)
    hi_n = {r["date"]: r["n"] for r in hi_d}
    jack = []
    for d in hi_dates:
        rest = [r for r in hi_d if r["date"] != d]
        if len(rest) < 3:
            continue
        diff = float(np.mean([r["alpha"] for r in rest])
                     - np.mean([r["alpha"] for r in lo_d]))
        jack.append({"drop": d, "diff": round(diff, 3),
                     "n": hi_n.get(d)})
    jack.sort(key=lambda x: x["diff"])
    res["hi_concentration"] = {
        "hi_days": len(hi_dates), "hi_buys": sum(hi_n.values()),
        "top3_share": round(sum(sorted(hi_n.values(), reverse=True)[:3])
                            / max(1, sum(hi_n.values())) * 100, 1),
        "hi_dates": hi_dates,
        "jackknife": jack,
        "jack_min": jack[0]["diff"] if jack else None,
        "jack_max": jack[-1]["diff"] if jack else None,
        "all_still_positive": all(x["diff"] > 0 for x in jack),
    }
    if not args.json:
        print("\n" + "-" * 92)
        print("四、稳健性 A：高档日集中度与逐日 jackknife")
        print("-" * 92)
        hc = res["hi_concentration"]
        print("  高档 %d 日 / %d 笔，前 3 日占 %.1f%%"
              % (hc["hi_days"], hc["hi_buys"], hc["top3_share"]))
        print("  高档日：%s" % " ".join(d[5:] for d in hi_dates))
        print("  逐日剔除后 alpha 差：最小 %+.2f%%（剔 %s）| 最大 %+.2f%% | 全部仍为正：%s"
              % (hc["jack_min"], jack[0]["drop"][5:] if jack else "-",
                 hc["jack_max"], "是" if hc["all_still_positive"] else "否"))

    # ---- 检验 B：滞后一日（环境分需收盘后才能算，实战次日才可执行）
    lag_env = {}
    prev = None
    for d in sorted(env):
        lag_env[d] = prev          # 前一个有买点的交易日
        prev = env[d]
    lag_rows = []
    for r in rows:
        e = lag_env.get(r["detect_date"])
        if e is None:
            continue
        lag_rows.append(dict(r, score_lag=e["score"]))
    lb, lcuts = band_by_quantile(lag_rows, "score_lag")
    res["lag1"] = {
        "n": len(lag_rows), "cuts": [round(x) for x in lcuts],
        "bands": report_band(lb, "滞后一日环境分（%d 笔）切点 %.0f/%.0f"
                             % (len(lag_rows), lcuts[0], lcuts[1])),
    }
    lag_hi = [r for r in lag_rows if r["score_lag"] >= lcuts[1]]
    lag_lo = [r for r in lag_rows if r["score_lag"] < lcuts[0]]
    # 日级聚类（笔级 t 会把 126 天虚增成 1800 笔，显著性失真）
    lag_score = {r["detect_date"]: r["score_lag"] for r in lag_rows}
    lag_dl = day_level(lag_rows)
    for r in lag_dl:
        r["score_lag"] = lag_score[r["date"]]
    lhi_d = [r for r in lag_dl if r["score_lag"] >= lcuts[1]]
    llo_d = [r for r in lag_dl if r["score_lag"] < lcuts[0]]
    d_l, t_l, p_l = welch([r["alpha"] for r in lhi_d], [r["alpha"] for r in llo_d])
    res["lag1"].update({
        "diff": None if d_l is None else round(d_l, 2),
        "t": None if t_l is None else round(t_l, 2),
        "p": None if p_l is None else round(p_l, 4),
        "hi_days": len(lhi_d), "lo_days": len(llo_d),
        "hi_day_alpha": round(float(np.mean([r["alpha"] for r in lhi_d])), 2),
        "lo_day_alpha": round(float(np.mean([r["alpha"] for r in llo_d])), 2),
    })
    if not args.json:
        print("  滞后一日（日级 %d/%d 日）：高档 %+.2f%% vs 低档 %+.2f%% → 差 %+.2f%%，t=%.2f p=%.4f"
              % (len(lhi_d), len(llo_d), res["lag1"]["hi_day_alpha"],
                 res["lag1"]["lo_day_alpha"], res["lag1"]["diff"], t_l, p_l))

    # ---- 检验 C：walk-forward 样本外（时间序 5 折，阈值只用历史数据定）
    srt = sorted({r["detect_date"] for r in rows})
    folds = []
    nf = 5
    for i in range(1, nf):
        cut_i = len(srt) * i // nf
        train_d = set(srt[:cut_i])
        test_d = set(srt[cut_i:len(srt) * (i + 1) // nf])
        train = [r["score"] for r in rows if r["detect_date"] in train_d]
        test = [r for r in rows if r["detect_date"] in test_d]
        if len(train) < 200 or len(test) < 100:
            continue
        q1t, q2t = np.quantile(np.asarray(train, float), (1 / 3, 2 / 3))
        th = [r for r in test if r["score"] >= q2t]
        tl = [r for r in test if r["score"] < q1t]
        if len(th) < 30 or len(tl) < 30:
            continue
        folds.append({
            "fold": "第%d折 %s→%s" % (i + 1, min(test_d), max(test_d)),
            "cut": round(float(q2t)),
            "hi_n": len(th), "lo_n": len(tl),
            "hi_alpha": agg(th, "alpha")["avg"], "lo_alpha": agg(tl, "alpha")["avg"],
            "diff": round((agg(th, "alpha")["avg"] or 0)
                          - (agg(tl, "alpha")["avg"] or 0), 2),
            "hi_win": agg(th, "alpha")["win"], "lo_win": agg(tl, "alpha")["win"],
        })
    res["walk_forward"] = folds
    if not args.json and folds:
        print("\n" + "-" * 92)
        print("五、稳健性 B：walk-forward 样本外（阈值只用该折之前的数据定）")
        print("-" * 92)
        print("  %-24s %5s %7s %9s %9s %8s"
              % ("折", "切点", "档笔数", "高档alpha", "低档alpha", "差"))
        for f in folds:
            print("  %-24s %5d %7s %8s%% %8s%% %+7.2f%%"
                  % (f["fold"], f["cut"], "%d/%d" % (f["hi_n"], f["lo_n"]),
                     f["hi_alpha"], f["lo_alpha"], f["diff"]))
        pos = sum(1 for f in folds if f["diff"] > 0)
        print("  样本外同向：%d/%d 折" % (pos, len(folds)))
        res["wf_pos"] = pos

    # ---- 检验 D：各档日期分布（诊断非单调）
    if not args.json:
        print("\n  各档日期覆盖（诊断 <40 档为何 ret 反而好于 40-59）")
        for lab, rs in page_bands:
            ds = sorted({r["detect_date"] for r in rs})
            print("    %-14s %3d 日：%s" % (lab, len(ds),
                                            " ".join(d[5:] for d in ds)[:150]))
    res["band_dates"] = {lab: sorted({r["detect_date"] for r in rs})
                         for lab, rs in page_bands}

    # ---- 检验 E：高档日内"拥挤度"——信号扎堆是否反而摊薄质量
    top3 = sorted(hi_n.items(), key=lambda kv: -kv[1])[:3]
    keep_d = [r for r in hi_d if r["date"] not in {d for d, _ in top3}]
    if len(keep_d) >= 5:
        diff_j = float(np.mean([r["alpha"] for r in keep_d])
                       - np.mean([r["alpha"] for r in lo_d]))
        t_j, p_j = sps.ttest_ind([r["alpha"] for r in keep_d],
                                 [r["alpha"] for r in lo_d], equal_var=False)
    else:
        diff_j, p_j = None, None
    med_n = float(np.median(list(hi_n.values())))
    crowded = [r for r in hi_d if r["n"] > med_n]
    sparse = [r for r in hi_d if r["n"] <= med_n]
    res["concentration"] = {
        "top3_dates": [{"date": d, "n": n} for d, n in top3],
        "top3_share": res["hi_concentration"]["top3_share"],
        "drop_top3_hi_days": len(keep_d),
        "diff_after_drop": None if diff_j is None else round(diff_j, 2),
        "p_after_drop": None if p_j is None else round(float(p_j), 4),
        "crowded_alpha": round(float(np.mean([r["alpha"] for r in crowded])), 2),
        "crowded_days": len(crowded), "crowded_n": sum(r["n"] for r in crowded),
        "sparse_alpha": round(float(np.mean([r["alpha"] for r in sparse])), 2),
        "sparse_days": len(sparse), "sparse_n": sum(r["n"] for r in sparse),
    }
    if not args.json:
        c = res["concentration"]
        print("\n  集中度诊断：高档 19 日里前 3 大日 %s 占 %.1f%% 笔数"
              % ("/".join("%s(%d笔)" % (d[5:], n) for d, n in top3),
                 c["top3_share"]))
        print("    剔除这 3 日后：剩 %d 日，alpha 差 %+.2f%% (p=%.4f)"
              % (c["drop_top3_hi_days"], c["diff_after_drop"], c["p_after_drop"]))
        print("    高档日内：拥挤日(>%d笔) %d 日/%d 笔 alpha %+.2f%% | "
              "稀疏日 %d 日/%d 笔 alpha %+.2f%%"
              % (med_n, c["crowded_days"], c["crowded_n"], c["crowded_alpha"],
                 c["sparse_days"], c["sparse_n"], c["sparse_alpha"]))

    # ---- 结论判定（四层全过才算"硬条件"）
    dc = res["day_cluster"]
    checks = {
        "alpha 日级高档>低档": (dc["alpha"]["diff"] or 0) > 0,
        "alpha 日级 p<0.05": (dc["alpha"]["p"] or 1) < 0.05,
        "alpha bootstrap CI 不含 0": bool(dc["alpha"]["boot"]) and
                                     dc["alpha"]["boot"]["lo"] > 0,
        "ret 日级高档>低档": (dc["ret"]["diff"] or 0) > 0,
        "日级 Spearman alpha 为正": (rho_a or 0) > 0,
        "二因子口径同向": (res["band_q2"][2]["alpha"]["avg"] or 0)
                          > (res["band_q2"][0]["alpha"]["avg"] or 0),
        "剔除3买后同向": (res["excl_3buy"][2]["alpha"]["avg"] or 0)
                          > (res["excl_3buy"][0]["alpha"]["avg"] or 0),
        "逐日 jackknife 全部仍为正": res["hi_concentration"]["all_still_positive"],
        "滞后一日仍同向(p<0.10)": (res["lag1"]["diff"] or 0) > 0
                                   and (res["lag1"]["p"] or 1) < 0.10,
        "walk-forward 过半折同向": res.get("wf_pos", 0) >= max(
            1, len(res.get("walk_forward") or []) / 2),
        "非 beta 探测器(指数相关不显著)": (res.get("beta_check") or {}).get("p", 0) > 0.05,
    }
    res["checks"] = {k: bool(v) for k, v in checks.items()}
    res["pass"] = sum(checks.values())
    res["verdict"] = ("升级为硬条件" if res["pass"] >= len(checks) - 1 else
                      "降级为软提示（证据打折）" if res["pass"] >= len(checks) - 3 else
                      "证伪：无可用区分度")

    if not args.json:
        print("\n" + "=" * 92)
        print("四、判定")
        print("=" * 92)
        for k, v in checks.items():
            print("  [%s] %s" % ("PASS" if v else "FAIL", k))
        print("  → %d/%d 通过 → %s" % (res["pass"], len(checks), res["verdict"]))
        print("=" * 92)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("\nJSON → %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
