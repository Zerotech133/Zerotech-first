# -*- coding: utf-8 -*-
"""
P4 共振 v3 —— 接入真实指数行情（中证500 000905.SZ），长窗 gap 收敛事件的变盘回测
================================================================================
相对 v2 的升级：
  - v2 gap 序列仅 30 日（19 个交易日）、变盘用 78 只自选股"宽度代理"、收敛事件仅 3 个
    且无法做显著性检验。
  - v3 gap 窗拉长到 2026-09-01 ~ 2026-11-03（JEV hit=1 有效覆盖自 09-01 起，口径如实），
    变盘直接用指数日 pct_chg 定义，收敛事件 vs 对照做 t 检验 / Mann-Whitney U。

只读原料（不写任何源库）：
  源1 指数行情  quant_backtest/data/ashare/000905_SZ.parquet
                （中证500，863 交易日，20230403 ~ 20261103；列 trade_date 'YYYYMMDD'/pct_chg/close）
  源2 JEV 事件  news_collector/news.db 表 news（mode=ro），hit=1 为入流事件，
                jev 列 JSON 含 bias(bull/bear/neutral)/conf/scope；ts 'YYYY-MM-DD HH:MM'
  源3 雪球情绪  quant_backtest/data/xueqiu/xueqiu_info.db（mode=ro）
                xq_status_classified(sentiment: bullish/bearish/neutral) JOIN xq_status(created_at)

口径（继承 v1/v2，保持一致）：
  - gap = 雪球bull率 − JEV bull率；雪球 n<5 的日标 noise（v1/v2 口径），双口径统计（含/不含）。
  - z_full = 全窗 in-sample z（含前视，体检用）；z_roll = 扩张窗 z（min 8 个有效 gap，防前视）。
  - 极端分歧日 |z|>1；收敛事件 = 前一日极端且当日 gap 跨越全窗均值（v2 原定义）。
  - 变盘日（指数口径，替代 v2 宽度代理）：
      主口径 = |000905 pct_chg| ≥ 全窗 80 分位；
      敏感性 = |pct_chg| ≥ 1% / 1.5% 双阈值；
      方向翻转 = sign(pct_chg) 与前日相反 且 |Δpct_chg| > 0.5%。

回测：对每个收敛事件日（对齐到指数交易日），算其后 1/3/5 交易日 000905 累计收益与
  |波动|（窗内逐日 |pct_chg| 均值）；对照组 = 非收敛的普通交易日（剔除极端未收敛日）。
  分组均值/中位数 + Welch t 检验 + Mann-Whitney U（scipy 1.18.1 在位则用，否则手算正态近似）。
  样本量如实报，不足明说。

产物（只写 outputs）：
  outputs/2026-11-04_P4共振v3_指数回测.html（自包含浅色 0 外链）
  outputs/2026-11-04_P4共振v3_数据.json

不 git 提交。
"""
import json
import math
import os
import sqlite3
import statistics
import sys
import io

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "C:/项目"
IDX_PARQUET = BASE + "/老田的AI助手/quant_backtest/data/ashare/000905_SZ.parquet"
NEWS_DB = BASE + "/老田的AI助手/news_collector/news.db"
XQ_DB = BASE + "/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db"
V2_JSON = BASE + "/outputs/2026-11-04_P4共振v2_数据.json"
OUT_DIR = BASE + "/outputs"
HTML_OUT = os.path.join(OUT_DIR, "2026-11-04_P4共振v3_指数回测.html")
JSON_OUT = os.path.join(OUT_DIR, "2026-11-04_P4共振v3_数据.json")

WIN_START, WIN_END = "2026-09-01", "2026-11-03"   # JEV 有效长窗（09-01 起如实）
Z_BAND = 1.0
ROLL_MIN = 8
XQ_NOISE_N = 5          # 雪球 n<5 → 噪声日（v1/v2 口径）
FWD_HORIZONS = [1, 3, 5]
VAR_P80 = "p80"         # 变盘主口径
SENS_THRESHOLDS = [1.0, 1.5]   # 敏感性 |pct_chg|% 阈值
FLIP_DELTA = 0.5        # 方向翻转 |Δpct_chg|% 阈值

try:
    from scipy import stats as sps
    HAVE_SCIPY = True
except Exception:
    HAVE_SCIPY = False


def ro(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    return statistics.stdev(xs) if len(xs) >= 2 else None


def fmt(x, nd=3):
    return ("{:." + str(nd) + "f}").format(x) if x is not None else "—"


def r3(x):
    return round(x, 4) if isinstance(x, (int, float)) and x is not None else x


# ================================================================
# 1. 指数行情 → 窗内日序列 + 全序列（前向收益用）
# ================================================================
def load_index():
    df = pd.read_parquet(IDX_PARQUET)
    df["date"] = df["trade_date"].str[:4] + "-" + df["trade_date"].str[4:6] + "-" + df["trade_date"].str[6:]
    df = df.sort_values("trade_date").reset_index(drop=True)
    full = df[["date", "trade_date", "close", "pct_chg"]]
    win = full[(full["date"] >= WIN_START) & (full["date"] <= WIN_END)].reset_index(drop=True)
    return full, win


# ================================================================
# 2. JEV 日 bull 率（news.db，hit=1）
# ================================================================
def load_jev_daily():
    con = ro(NEWS_DB)
    cur = con.cursor()
    cur.execute("""
        SELECT substr(ts,1,10) d, jev FROM news
        WHERE hit=1 AND ts >= ? AND ts < ? AND jev IS NOT NULL AND jev != ''
    """, (WIN_START, WIN_END + "Z"))
    by_day = {}
    for d, jev_raw in cur.fetchall():
        try:
            jev = json.loads(jev_raw)
        except Exception:
            continue
        bias = jev.get("bias")
        if bias in ("bull", "bear", "neutral"):
            by_day.setdefault(d, {"bull": 0, "bear": 0, "neutral": 0})
            by_day[d][bias] += 1
    con.close()
    rows = []
    for d in sorted(by_day):
        c = by_day[d]
        n = c["bull"] + c["bear"] + c["neutral"]
        rows.append({
            "date": d, "n": n,
            "bull": c["bull"], "bear": c["bear"], "neutral": c["neutral"],
            "bull_rate": c["bull"] / n if n else None,
        })
    return {r["date"]: r for r in rows}


# ================================================================
# 3. 雪球日 bull 率（xq_status_classified JOIN xq_status）
# ================================================================
def load_xq_daily():
    con = ro(XQ_DB)
    cur = con.cursor()
    cur.execute("""
        SELECT substr(x.created_at,1,10) d, c.sentiment
        FROM xq_status_classified c
        JOIN xq_status x ON c.status_id = x.status_id
        WHERE x.created_at >= ? AND x.created_at < ? AND c.sentiment IS NOT NULL
    """, (WIN_START, WIN_END + "Z"))
    by_day = {}
    for d, s in cur.fetchall():
        if s in ("bullish", "bearish", "neutral"):
            by_day.setdefault(d, {"bullish": 0, "bearish": 0, "neutral": 0})
            by_day[d][s] += 1
    con.close()
    rows = []
    for d in sorted(by_day):
        c = by_day[d]
        n = c["bullish"] + c["bearish"] + c["neutral"]
        rows.append({
            "date": d, "n": n,
            "bull": c["bullish"], "bear": c["bearish"], "neutral": c["neutral"],
            "bull_rate": c["bullish"] / n if n else None,
        })
    return {r["date"]: r for r in rows}


# ================================================================
# 4. 全窗 gap 序列（自然日时间轴，v1/v2 同款）：雪球有帖日才有 gap
# ================================================================
def build_gap_series(win_idx, jev_daily, xq_daily):
    import datetime
    d0 = datetime.date.fromisoformat(WIN_START)
    d1 = datetime.date.fromisoformat(WIN_END)
    n_days = (d1 - d0).days + 1
    trade_dates = set(win_idx["date"])
    rows = []
    for i in range(n_days):
        d = (d0 + datetime.timedelta(days=i)).isoformat()
        j = jev_daily.get(d)
        x = xq_daily.get(d)
        # gap 仅在雪球有帖且有 JEV 事件的日有定义（v2/v1：缺任一源 → null）
        xq_n = x["n"] if x else 0
        jev_n = j["n"] if j else 0
        gap = None
        if x is not None and j is not None and j["n"] > 0:
            gap = x["bull_rate"] - j["bull_rate"]
        rows.append({
            "date": d, "gap": gap,
            "noise": (xq_n < XQ_NOISE_N) if gap is not None else None,
            "xq_n": xq_n, "jev_n": jev_n,
            "xq_bull": x["bull_rate"] if x else None,
            "jev_bull": j["bull_rate"] if j and j["n"] > 0 else None,
            "is_trade_day": d in trade_dates,
            "pct_chg": float(win_idx.loc[win_idx["date"] == d, "pct_chg"].iloc[0]) if d in trade_dates else None,
        })
    return rows


def gap_stats(rows):
    def core(rs):
        gs = [r["gap"] for r in rs if r["gap"] is not None]
        return {
            "n_days": len(gs),
            "mean": mean(gs), "stdev": stdev(gs),
            "min": min(gs) if gs else None, "max": max(gs) if gs else None,
        }
    all_valid = core(rows)
    meaningful = core([r for r in rows if not r["noise"]])
    all_valid.update({
        "n_noise": sum(1 for r in rows if r["gap"] is not None and r["noise"]),
        "meaningful": meaningful,
    })
    return all_valid


def zscore_series(rows, st, key_rows=None):
    """z 基于给定行子集（默认=全部行，即含噪声口径）的 gap 统计。"""
    m, sd = st["mean"], st["stdev"]
    seen, run_sum, run_sq = 0, 0.0, 0.0
    for r in rows:
        z_full = z_roll = None
        if r["gap"] is not None and sd and sd > 1e-12:
            z_full = (r["gap"] - m) / sd
            seen += 1
            run_sum += r["gap"]
            run_sq += r["gap"] ** 2
            if seen >= ROLL_MIN:
                rm = run_sum / seen
                rv = run_sq / seen - rm ** 2
                rsd = math.sqrt(max(rv, 0.0) * seen / (seen - 1))
                z_roll = (r["gap"] - rm) / rsd if rsd > 1e-12 else 0.0
        r["z_full"] = z_full
        r["z_roll"] = z_roll
        r["extreme"] = z_full is not None and abs(z_full) > Z_BAND


def find_convergence(rows, st):
    """收敛事件（v2 原定义）：前一日 |z_full|>1，当日 gap 跨越全窗均值。"""
    events = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if prev["gap"] is None or cur["gap"] is None or not prev["extreme"]:
            continue
        m = st["mean"]
        if (prev["gap"] - m) * (cur["gap"] - m) <= 0:
            events.append({
                "date": cur["date"], "prev_date": prev["date"],
                "prev_gap": prev["gap"], "prev_z": prev["z_full"],
                "gap": cur["gap"], "z": cur["z_full"],
                "pullback": abs(prev["gap"] - cur["gap"]),
                "noise": bool(cur["noise"] or prev["noise"]),
                "xq_n": cur["xq_n"], "jev_n": cur["jev_n"],
            })
    return events


# ================================================================
# 5. 变盘日定义（指数口径）
# ================================================================
def mark_var_days(idx, thr_p80, sens=(1.0, 1.5)):
    """idx: DataFrame（窗内）→ 每日变盘标记列。返回 {date: {...}}。"""
    out = {}
    pcts = idx["pct_chg"].tolist()
    dates = idx["date"].tolist()
    for i, d in enumerate(dates):
        p = pcts[i]
        prev = pcts[i - 1] if i >= 1 else None
        var_p80 = abs(p) >= thr_p80
        var_s1 = abs(p) >= sens[0]
        var_s15 = abs(p) >= sens[1]
        flip = False
        if prev is not None and prev != 0 and abs(p - prev) > FLIP_DELTA and (p > 0) != (prev > 0):
            flip = True
        out[d] = {
            "pct_chg": p, "var_p80": bool(var_p80),
            "var_1pct": bool(var_s1), "var_1_5pct": bool(var_s15),
            "flip": bool(flip),
        }
    return out


# ================================================================
# 6. 回测：收敛事件日 vs 普通交易日，后 1/3/5 日指数累计收益 / |波动|
#    事件在自然日历上识别 → 锚定"≥事件日的首个交易日"（next-open 语义，无前视）
# ================================================================
def fwd_ret(full_idx, i, h):
    """t=i 后 h 个交易日：累计收益（close[i]→close[i+h]，%）与窗内逐日 |pct_chg| 均值。"""
    j = i + h
    if j >= len(full_idx):
        return None
    c0, cj = full_idx["close"].iloc[i], full_idx["close"].iloc[j]
    pcts = full_idx["pct_chg"].iloc[i + 1: j + 1].tolist()
    return {
        "ret": (cj / c0 - 1) * 100.0,
        "abs_ret": abs(cj / c0 - 1) * 100.0,
        "vol": mean([abs(p) for p in pcts]),
        "path_max_abs": max(abs(p) for p in pcts) if pcts else None,
    }


def align_next_trade(date, trade_dates):
    """≥date 的首个交易日（无则 None）。"""
    for d in trade_dates:
        if d >= date:
            return d
    return None


def mannwhitney_u(x, y):
    """手算 Mann-Whitney U + 正态近似 p（双尾）。scipy 在位则优先用 scipy。"""
    if HAVE_SCIPY:
        u, p = sps.mannwhitneyu(x, y, alternative="two-sided")
        return float(u), float(p), "scipy.stats.mannwhitneyu (exact/auto)"
    nx, ny = len(x), len(y)
    rank_of = {}
    pool = sorted([(v, 0) for v in x] + [(v, 1) for v in y])
    # 平均秩（并列）
    i = 0
    ranks = [0.0] * len(pool)
    while i < len(pool):
        j = i
        while j + 1 < len(pool) and pool[j + 1][0] == pool[i][0]:
            j += 1
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    r1 = sum(r for r, tag in zip(ranks, [t for _, t in pool]) if tag == 0)
    u1 = r1 - nx * (nx + 1) / 2.0
    mu = nx * ny / 2.0
    # 并列校正的 σ
    all_v = [v for v, _ in pool]
    counts = {}
    for v in all_v:
        counts[v] = counts.get(v, 0) + 1
    n = nx + ny
    tie = sum(t ** 3 - t for t in counts.values())
    sd = math.sqrt(nx * ny / 12.0 * ((n + 1) - tie / (n * (n - 1))))
    zst = (u1 - mu) / sd if sd > 1e-12 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(zst) / math.sqrt(2.0))))
    return float(u1), float(p), "手算 Mann-Whitney U 正态近似（并列校正）"


def welch_t(x, y):
    if HAVE_SCIPY:
        t, p = sps.ttest_ind(x, y, equal_var=False)
        return float(t), float(p), "scipy Welch t"
    if len(x) < 2 or len(y) < 2:
        return None, None, "样本不足"
    mx, my = mean(x), mean(y)
    vx = statistics.variance(x)
    vy = statistics.variance(y)
    se2 = vx / len(x) + vy / len(y)
    t = (mx - my) / math.sqrt(se2) if se2 > 1e-12 else 0.0
    df = se2 ** 2 / ((vx / len(x)) ** 2 / (len(x) - 1) + (vy / len(y)) ** 2 / (len(y) - 1))
    z = t  # 大样本用正态近似 p（小样本仅报告 t）
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2.0))))
    return float(t), float(p), "手算 Welch t（df=%.1f，p 取正态近似）" % df


def backtest(full_idx, win_idx, series, events, cal):
    """cal: 'all'（含噪声）/'clean'（剔噪声）。组定义在交易日上：
       event = 收敛事件锚定日；extreme_only = 极端日锚定但未收敛；baseline = 其余普通交易日。
       前向收益用全 parquet（超出 11-03 的窗如实截断为 None）。"""
    trade_dates = list(win_idx["date"])
    date2i = {d: i for i, d in enumerate(full_idx["date"])}

    extreme_days = [r for r in series if r["extreme"] and r["gap"] is not None
                    and (cal == "all" or not r["noise"])]
    ev_days = [e for e in events if cal == "all" or not e["noise"]]

    anch_extreme = {a for a in (align_next_trade(r["date"], trade_dates) for r in extreme_days) if a}
    anch_event = {a for a in (align_next_trade(e["date"], trade_dates) for e in ev_days) if a}

    per_day, detail = [], {"event": [], "extreme_only": [], "baseline": []}
    for d in trade_dates:
        if d in anch_event:
            g = "event"
        elif d in anch_extreme:
            g = "extreme_only"
        else:
            g = "baseline"
        i = date2i[d]
        rec = {"date": d, "group": g,
               "pct_chg": float(win_idx.loc[win_idx["date"] == d, "pct_chg"].iloc[0]),
               "windows": {}}
        for h in FWD_HORIZONS:
            fm = fwd_ret(full_idx, i, h)
            rec["windows"][h] = fm
            if fm:
                detail[g].append({"date": d, "h": h, **fm})
        per_day.append(rec)

    def agg(g):
        out = {"n_days": sum(1 for x in per_day if x["group"] == g)}
        for h in FWD_HORIZONS:
            obs = [x for x in detail[g] if x["h"] == h]
            d = {
                "n_obs": len(obs),
                "ret_mean": mean([x["ret"] for x in obs]),
                "ret_median": statistics.median([x["ret"] for x in obs]) if obs else None,
                "abs_ret_mean": mean([x["abs_ret"] for x in obs]),
                "abs_ret_median": statistics.median([x["abs_ret"] for x in obs]) if obs else None,
                "vol_mean": mean([x["vol"] for x in obs]),
            }
            # 检验：event vs baseline
            base = [x for x in detail["baseline"] if x["h"] == h]
            d["test"] = {}
            for key in ("ret", "abs_ret", "vol"):
                xs = [x[key] for x in obs]
                ys = [x[key] for x in base]
                if len(xs) >= 2 and len(ys) >= 3:
                    t, pt, mt = welch_t(xs, ys)
                    u, pu, mu_ = mannwhitney_u(xs, ys)
                    d["test"][key] = {"t": r3(t), "p_t": r3(pt), "method_t": mt,
                                      "U": r3(u), "p_u": r3(pu), "method_u": mu_}
                else:
                    d["test"][key] = None
            out[f"h{h}"] = d
        return out

    return per_day, {g: agg(g) for g in ("event", "extreme_only", "baseline")}


# ================================================================
# 7. JSON 产物
# ================================================================
def dump_json(meta, series, events, stats_all, stats_clean, var_days, thr_p80, back_all, back_clean, jev_monthly, xq_monthly):
    payload = {
        "meta": meta,
        "gap_stats": {"all": {k: r3(v) if not isinstance(v, dict) else {kk: r3(vv) for kk, vv in v.items()}
                              for k, v in stats_all.items()},
                      "clean": {k: r3(v) if not isinstance(v, dict) else {kk: r3(vv) for kk, vv in v.items()}
                                for k, v in stats_clean.items()}},
        "thresholds": {"var_p80_pct": r3(thr_p80), "sensitivity_pct": SENS_THRESHOLDS,
                       "flip_delta_pct": FLIP_DELTA},
        "events": [{k: (r3(v) if isinstance(v, float) else v) for k, v in e.items()} for e in events],
        "series": [
            {"date": r["date"], "is_trade_day": r["is_trade_day"],
             "gap": r3(r["gap"]) if r["gap"] is not None else None,
             "z_full": r3(r["z_full"]) if r["z_full"] is not None else None,
             "z_roll": r3(r["z_roll"]) if r["z_roll"] is not None else None,
             "extreme": r["extreme"] if r["extreme"] is not None else None,
             "noise": r["noise"], "xq_n": r["xq_n"], "jev_n": r["jev_n"],
             "xq_bull": r3(r["xq_bull"]) if r["xq_bull"] is not None else None,
             "jev_bull": r3(r["jev_bull"]) if r["jev_bull"] is not None else None,
             "pct_chg": r3(r["pct_chg"]) if r["pct_chg"] is not None else None,
             **({"var": var_days[r["date"]]} if r["date"] in var_days else {})}
            for r in series
        ],
        "backtest": {
            "all": serialize_back(back_all),
            "clean": serialize_back(back_clean),
        },
        "monthly_volume": {"jev_hit1": jev_monthly, "xq_posts": xq_monthly},
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("[JSON] →", JSON_OUT)
    return payload


def _rdeep(v):
    """递归：float → round4，dict/list → 递归，test 字典里的 method 字符串原样。"""
    if isinstance(v, float):
        return r3(v)
    if isinstance(v, dict):
        return {k: _rdeep(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_rdeep(x) for x in v]
    return v


def serialize_back(back):
    return {
        "agg": _rdeep(back[1]),
        "per_day": _rdeep(back[0]),
    }


# ================================================================
# 8. HTML 产物（自包含浅色 0 外链，纯 CSS 位置条）
# ================================================================
def build_html(payload, series, events, stats_all, stats_clean, win_idx, var_days, thr_p80,
               back_all, back_clean, jev_monthly, xq_monthly, v2_stats):
    m = payload["meta"]
    n_trade = sum(1 for r in series if r["is_trade_day"])
    ev_dates_all = {e["date"] for e in events}
    ev_dates_clean = {e["date"] for e in events if not e["noise"]}
    gs = [r["gap"] for r in series if r["gap"] is not None]
    lo = min(min(gs), stats_all["mean"] - stats_all["stdev"]) - 0.06
    hi = max(max(gs), stats_all["mean"] + stats_all["stdev"]) + 0.06

    def y(v):
        return (hi - v) / (hi - lo) * 100

    n = len(series)
    band_top = y(stats_all["mean"] + stats_all["stdev"])
    band_h = y(stats_all["mean"] - stats_all["stdev"]) - band_top

    # ---- gap 时间线（纯 CSS）----
    bars = []
    for i, r in enumerate(series):
        left = i / n * 100
        if r["gap"] is None:
            bars.append(f'<div class="gnull" style="left:{left:.2f}%">–</div>'
                        f'<div class="glbl" style="left:{left:.2f}%">{r["date"][5:]}</div>')
            continue
        top = min(y(r["gap"]), y(stats_all["mean"]))
        hgt = abs(y(r["gap"]) - y(stats_all["mean"]))
        cls = []
        if r["date"] in ev_dates_all:
            cls.append("conv")
        elif r["gap"] >= stats_all["mean"]:
            cls.append("pos")
        else:
            cls.append("neg")
        if r["extreme"]:
            cls.append("ext")
        if r["noise"]:
            cls.append("noise")
        tip = (f'{r["date"]}  gap={r["gap"]:+.3f}  z={r["z_full"]:+.2f}'
               f'  雪球n={r["xq_n"]} JEVn={r["jev_n"]}'
               + (f'  指数pct={r["pct_chg"]:+.2f}%' if r["pct_chg"] is not None else '  （非交易日）')
               + f'{"  [噪声日]" if r["noise"] else ""}'
               + f'{"  [极端]" if r["extreme"] else ""}'
               + f'{"  [收敛事件]" if r["date"] in ev_dates_all else ""}')
        marker = (f'<i class="evdot" style="left:{left + 100 / n * 0.31:.2f}%;top:{y(r["gap"]):.1f}%" '
                  f'title="{tip} 收敛事件"></i>') if r["date"] in ev_dates_all else ""
        bars.append(f'<div class="gbar {" ".join(cls)}" style="left:{left:.2f}%;width:{100/n*0.62:.2f}%;'
                    f'top:{top:.2f}%;height:{max(hgt, 0.4):.2f}%" title="{tip}"></div>{marker}'
                    f'<div class="glbl" style="left:{left:.2f}%">{r["date"][5:]}</div>')

    # ---- 指数 pct_chg 对照条（中线 0，红涨绿跌；仅交易日有柱，位置=该日在自然日轴上的位置）----
    pbars = []
    pmax = max(abs(r["pct_chg"]) for r in series if r["pct_chg"] is not None) * 1.15
    for i, r in enumerate(series):
        p = r["pct_chg"]
        if p is None:
            continue
        hgt = abs(p) / pmax * 50
        cls = "pup" if p >= 0 else "pdn"
        vd = var_days.get(r["date"], {})
        marks = []
        if vd.get("var_p80"):
            marks.append("变盘(p80)")
        if vd.get("flip"):
            marks.append("翻转")
        tip = (f'{r["date"]}  000905 pct_chg={p:+.2f}%'
               + (f'  [{"|".join(marks)}]' if marks else ""))
        star = ' box-shadow:0 0 0 1.5px var(--ext);' if vd.get("var_p80") else ""
        pbars.append(f'<div class="pcol" style="left:{i/n*100:.2f}%;width:{100/n*0.62:.2f}%">'
                     f'<div class="pbar {cls}" style="height:{hgt:.1f}%;{"top:0" if p >= 0 else "bottom:0"};{star}" title="{tip}"></div>'
                     f'</div>')

    # ---- 事件卡 ----
    trade_dates = [r["date"] for r in series if r["is_trade_day"]]
    cards = []
    for e in events:
        anchor = align_next_trade(e["date"], trade_dates)
        vd = var_days.get(anchor, {}) if anchor else {}
        noise_tag = '<span class="tag warn">含噪声日(n&lt;5)</span>' if e["noise"] else '<span class="tag ok">双源样本足</span>'
        anchor_html = f'锚定交易日 <b>{anchor}</b>' if anchor and anchor != e["date"] else (
            f'交易日（当日锚定）' if anchor else '窗外（无锚定日）')
        vd_html = ""
        if vd:
            vd_html = (f'锚定日指数 <b>{vd["pct_chg"]:+.2f}%</b>'
                       + (' <span class="tag">变盘 p80</span>' if vd.get("var_p80") else "")
                       + (' <span class="tag">方向翻转</span>' if vd.get("flip") else "")
                       + (f'（|pct|≥1%{"✓" if vd.get("var_1pct") else "✗"}，≥1.5%{"✓" if vd.get("var_1_5pct") else "✗"}）'))
        cards.append(f'''
      <div class="evcard">
        <div class="evhead"><b>{e["date"]}</b> {noise_tag}
          <span class="tag">前极值 {e["prev_date"]}：gap {e["prev_gap"]:+.3f}（z={e["prev_z"]:+.2f}）</span>
          <span class="tag">{anchor_html}</span></div>
        <div class="evbody">回落幅度 <b>{e["pullback"]:.3f}</b>（{e["prev_gap"]:+.3f} → {e["gap"]:+.3f}，跨越均值 {stats_all["mean"]:+.3f}）
          ｜ 雪球 n={e["xq_n"]}、JEV n={e["jev_n"]}<br>{vd_html}</div>
      </div>''')
    if not cards:
        cards = ['<div class="evcard">窗口内未检出收敛事件。</div>']

    # ---- 回测表（双口径）----
    def bt_table(back, cal_label, note_n):
        agg = back[1]
        rows_html = ""
        for g, label in (("event", "收敛事件日"), ("extreme_only", "仅极端未收敛日"), ("baseline", "普通日（对照）")):
            a = agg[g]
            cells = f'<td>{a["n_days"]}</td>'
            for h in FWD_HORIZONS:
                d = a[f"h{h}"]
                cells += (f'<td>{fmt(d["ret_mean"], 2)}<small> / {fmt(d["ret_median"], 2)}</small>'
                          f'<br><small>n={d["n_obs"]}</small></td>')
            for h in FWD_HORIZONS:
                d = a[f"h{h}"]
                cells += f'<td>{fmt(d["abs_ret_mean"], 2)}<br><small>n={d["n_obs"]}</small></td>'
            for h in FWD_HORIZONS:
                d = a[f"h{h}"]
                cells += f'<td>{fmt(d["vol_mean"], 2)}</td>'
            rows_html += f'<tr class="{"ev" if g == "event" else ""}"><td class="left">{label}</td>{cells}</tr>'
        # p 值行
        prow = '<tr class="ptest"><td class="left">event vs 对照 p 值（Welch t / MW-U）</td>'
        for h in FWD_HORIZONS:
            t = agg["event"][f"h{h}"]["test"]
            prow += (f'<td>{t and t["ret"] and (fmt(t["ret"]["p_t"], 3) + " / " + fmt(t["ret"]["p_u"], 3)) or "—"}</td>')
        for h in FWD_HORIZONS:
            t = agg["event"][f"h{h}"]["test"]
            prow += (f'<td>{t and t["abs_ret"] and (fmt(t["abs_ret"]["p_t"], 3) + " / " + fmt(t["abs_ret"]["p_u"], 3)) or "—"}</td>')
        for h in FWD_HORIZONS:
            t = agg["event"][f"h{h}"]["test"]
            prow += (f'<td>{t and t["vol"] and (fmt(t["vol"]["p_t"], 3) + " / " + fmt(t["vol"]["p_u"], 3)) or "—"}</td>')
        prow += "</tr>"
        return f'''
    <table>
      <tr><th rowspan="2">组（{cal_label}）</th><th rowspan="2">日数</th>
          <th colspan="3">后继累计收益%（均值/中位）</th><th colspan="3">后继|累计收益|%（波动幅度）</th><th colspan="3">路径日均|pct|%</th></tr>
      <tr><th>+1日</th><th>+3日</th><th>+5日</th><th>+1日</th><th>+3日</th><th>+5日</th><th>+1日</th><th>+3日</th><th>+5日</th></tr>
      {rows_html}
      {prow}
    </table>
    <div class="note">{note_n}</div>'''

    n_events = len(events)
    n_ev_clean = len(ev_dates_clean)
    n_var = sum(1 for v in var_days.values() if v["var_p80"])
    n_flip = sum(1 for v in var_days.values() if v["flip"])

    # 月度量行
    jm = " / ".join(f"{k.replace('2026-', '')}月 {v}" for k, v in jev_monthly.items())
    xm = " / ".join(f"{k.replace('2026-', '')}月 {v}" for k, v in xq_monthly.items())

    # 逐事件明细（含噪声口径 back_all；按锚定日查）
    ev_detail = ""
    by_date = {r["date"]: r for r in back_all[0]}
    trade_dates_l = [r["date"] for r in series if r["is_trade_day"]]
    for e in events:
        anchor = align_next_trade(e["date"], trade_dates_l)
        if not anchor:
            ev_detail += (f'<tr><td>{e["date"]}{(" <small>[噪声]</small>" if e["noise"] else "")}</td>'
                          f'<td colspan="3">窗外（无锚定交易日）</td></tr>')
            continue
        r = by_date.get(anchor)
        if not r:
            continue
        cells = []
        for h in FWD_HORIZONS:
            w = r["windows"][h]
            cells.append(f'{w["ret"]:+.2f}% / |{w["abs_ret"]:.2f}%| / vol {w["vol"]:.2f}%' if w else "—（截断）")
        ev_detail += (f'<tr><td>{e["date"]}{(" <small>[噪声]</small>" if e["noise"] else "")}'
                      f' → 锚 {anchor}</td>'
                      f'<td>{cells[0]}</td><td>{cells[1]}</td><td>{cells[2]}</td></tr>')
    if not ev_detail:
        ev_detail = '<tr><td colspan="4">无（窗口内未检出收敛事件）</td></tr>'

    sci = "scipy 1.18.1" if HAVE_SCIPY else "手算正态近似"

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P4 共振 v3 · 指数变盘回测 · 2026-11-04</title>
<style>
  :root {{
    --bg:#f7f8fa; --card:#fff; --ink:#1c2330; --sub:#5a6474; --line:#e3e7ee;
    --pos:#2b8a3e; --neg:#c92a2a; --ext:#e8590c; --conv:#1971c2; --band:#eef3fb;
    --up:#d9480f; --dn:#2b8a3e; --warnbg:#fff9db; --warnb:#f0c000;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--ink);
         font:14px/1.65 "Microsoft YaHei","PingFang SC",system-ui,sans-serif;
         padding:28px 20px 60px; }}
  .wrap {{ max-width:1120px; margin:0 auto; }}
  h1 {{ font-size:22px; }} h1 .v {{ color:var(--sub); font-size:13px; font-weight:normal; }}
  .subline {{ color:var(--sub); font-size:12.5px; margin:6px 0 22px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:18px 20px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin-bottom:4px; }}
  .note {{ color:var(--sub); font-size:12.5px; margin-bottom:12px; }}
  .kpis {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .kpi {{ flex:1; min-width:150px; background:#fbfcfe; border:1px solid var(--line);
          border-radius:8px; padding:10px 14px; }}
  .kpi .k {{ color:var(--sub); font-size:12px; }}
  .kpi .v {{ font-size:20px; font-weight:600; margin-top:2px; }}
  .kpi .s {{ font-size:11px; color:var(--sub); }}
  table {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  th, td {{ padding:6px 6px; border-bottom:1px solid var(--line); text-align:center; }}
  th {{ background:#f2f4f8; font-weight:600; font-size:11.5px; color:var(--sub); }}
  td.left {{ text-align:left; }} tr.ev td {{ background:#eef6ff; }}
  tr.ptest td {{ background:#f8f9fb; font-size:11.5px; color:var(--sub); }}
  small {{ color:var(--sub); font-size:11px; }}

  .gchart {{ position:relative; height:320px; background:#fbfcfe; border:1px solid var(--line);
             border-radius:8px; margin-bottom:34px; }}
  .gband {{ position:absolute; left:0; right:0; background:var(--band);
            border-top:1px dashed #b9c8e4; border-bottom:1px dashed #b9c8e4; }}
  .gmean {{ position:absolute; left:0; right:0; border-top:2px solid #4263eb; z-index:2; }}
  .gmean span {{ position:absolute; right:4px; top:-20px; font-size:11px; color:#4263eb; }}
  .gbar {{ position:absolute; z-index:3; border-radius:2px; min-height:1px; }}
  .gbar.pos {{ background:var(--pos); opacity:.75; }}
  .gbar.neg {{ background:var(--neg); opacity:.75; }}
  .gbar.noise {{ opacity:.28; }}
  .gbar.ext {{ box-shadow:0 0 0 1.5px var(--ext); }}
  .gbar.conv {{ background:var(--conv); opacity:.9; }}
  .evdot {{ position:absolute; z-index:4; width:11px; height:11px; border-radius:50%;
            border:2.5px solid var(--conv); background:#fff; transform:translate(-50%,-50%); }}
  .gnull {{ position:absolute; top:48%; width:3%; text-align:center; color:#b6bec9; font-size:10px; }}
  .glbl {{ position:absolute; bottom:-30px; transform:rotate(-60deg); transform-origin:left top;
           font-size:8.5px; color:var(--sub); white-space:nowrap; }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12.5px; color:var(--sub); margin-top:8px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }}

  .pchart {{ position:relative; height:150px; background:#fbfcfe; border:1px solid var(--line);
            border-radius:8px; margin-bottom:34px; }}
  .pzero {{ position:absolute; left:0; right:0; top:50%; border-top:1px dashed #adb5bd; }}
  .pcol {{ position:absolute; top:0; bottom:0; }}
  .pbar {{ position:absolute; left:0; right:0; width:100%; }}
  .pbar.pup {{ background:var(--up); opacity:.8; border-radius:0 0 2px 2px; }}
  .pbar.pdn {{ background:var(--dn); opacity:.8; border-radius:2px 2px 0 0; }}

  .evcard {{ border:1px solid var(--line); border-left:4px solid var(--conv); border-radius:8px;
             padding:10px 14px; margin-bottom:10px; background:#fbfdff; }}
  .evhead {{ font-size:13.5px; margin-bottom:4px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
  .evbody {{ font-size:12.5px; color:var(--sub); }}
  .tag {{ font-size:11px; border:1px solid var(--line); border-radius:4px; padding:1px 6px; color:var(--sub); }}
  .tag.warn {{ background:var(--warnbg); border-color:var(--warnb); }}
  .tag.ok {{ background:#ebfbee; border-color:#a9e34b; }}

  .warnbox {{ background:var(--warnbg); border:1px solid var(--warnb); border-radius:8px;
              padding:10px 14px; font-size:12.5px; color:#664d03; margin-bottom:14px; }}
  .caveat {{ font-size:12.5px; color:var(--sub); }}
  .caveat li {{ margin:4px 0 4px 18px; }}
  footer {{ text-align:center; color:var(--sub); font-size:12px; margin-top:8px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>P4 共振 v3 <span class="v">真实指数行情 × 长窗 gap 收敛事件变盘回测 · 2026-11-04 · 开源情报 H 车道</span></h1>
  <div class="subline">指数：中证500（000905.SZ，863 交易日 parquet，窗内 {n_trade} 个交易日）
  　|　gap = 雪球bull率 − JEV bull率（v1/v2 口径，自然日时间轴）　|　变盘主口径 |pct_chg| ≥ 全窗80分位 = {thr_p80:.2f}%（敏感性 1% / 1.5%）</div>
</header>

<div class="card">
  <h2>KPI 总览</h2>
  <div class="kpis">
    <div class="kpi"><div class="k">窗内交易日 / 有效 gap 日</div>
      <div class="v">{n_trade} / {stats_all["n_days"]}</div>
      <div class="s">自然日 {len(series)} 个；噪声日（雪球 n&lt;5）{stats_all["n_noise"]} 个；v2 仅 19 个交易日</div></div>
    <div class="kpi"><div class="k">gap 均值 / σ（含噪声口径）</div>
      <div class="v">{fmt(stats_all["mean"], 3)} / {fmt(stats_all["stdev"], 3)}</div>
      <div class="s">剔噪声口径 {fmt(stats_clean["mean"], 3)} / {fmt(stats_clean["stdev"], 3)}（n={stats_clean["n_days"]}）</div></div>
    <div class="kpi"><div class="k">收敛事件（v2: 3 个）</div>
      <div class="v">{n_events}</div>
      <div class="s">其中双源样本足 {n_ev_clean} 个；极端日 {sum(1 for r in series if r["extreme"])} 个</div></div>
    <div class="kpi"><div class="k">变盘日（p80 口径）</div>
      <div class="v">{n_var}</div>
      <div class="s">方向翻转 {n_flip} 个；≥1%: {sum(1 for v in var_days.values() if v["var_1pct"])} / ≥1.5%: {sum(1 for v in var_days.values() if v["var_1_5pct"])} 个</div></div>
    <div class="kpi"><div class="k">检验方法</div>
      <div class="v" style="font-size:14px">{sci}</div>
      <div class="s">Welch t + Mann-Whitney U（双尾）</div></div>
  </div>
</div>

<div class="card">
  <h2>月度量口径（如实）</h2>
  <div class="note">JEV hit=1 自 2026-07-19 起覆盖，9 月前稀疏（07 月 6 / 08 月 5），<b>有效长窗从 09-01 起算</b>；
        雪球 2026 年日均个位数帖（76% 为 2017-19 历史语料），n&lt;5 日按 v1/v2 口径标噪声。</div>
  <table>
    <tr><th>源</th><th>09 月</th><th>10 月</th><th>11 月（至 11-03/04）</th></tr>
    <tr><td class="left">JEV hit=1 事件数</td><td>{jev_monthly.get("2026-09", 0)}</td><td>{jev_monthly.get("2026-10", 0)}</td><td>{jev_monthly.get("2026-11", 0)}</td></tr>
    <tr><td class="left">雪球分类帖数</td><td>{xq_monthly.get("2026-09", 0)}</td><td>{xq_monthly.get("2026-10", 0)}</td><td>{xq_monthly.get("2026-11", 0)}</td></tr>
  </table>
</div>

<div class="card">
  <h2>全窗 gap 时间线 + ±1σ 带 + 收敛事件（上）与 000905 日 pct_chg（下）</h2>
  <div class="note">时间轴 = 自然日 09-01~11-03（{len(series)} 日，其中交易日 {n_trade} 日）。上图：条 = 当日 gap 相对全窗均值（蓝线 {stats_all["mean"]:+.3f}）偏移，浅蓝带 = ±1σ；
        橙描边 = 极端日（|z|&gt;1）；蓝实条+圆点 = 收敛事件；浅色条 = 雪球 n&lt;5 噪声日。下图：中证500 日 pct_chg（红涨绿跌，仅交易日有柱），橙描边 = p80 变盘日。悬停看数值。</div>
  <div class="gchart">
    <div class="gband" style="top:{band_top:.2f}%;height:{band_h:.2f}%"></div>
    <div class="gmean" style="top:{y(stats_all["mean"]):.2f}%"><span>均值 {stats_all["mean"]:+.3f}</span></div>
    {''.join(bars)}
  </div>
  <div class="legend">
    <span><span class="dot" style="background:var(--pos)"></span>gap&gt;均值</span>
    <span><span class="dot" style="background:var(--neg)"></span>gap&lt;均值</span>
    <span><span class="dot" style="box-shadow:0 0 0 1.5px var(--ext);background:#fff"></span>极端 |z|&gt;1</span>
    <span><span class="dot" style="background:var(--conv)"></span>收敛事件</span>
    <span>浅色 = 噪声日（雪球 n&lt;5）</span>
    <span>z_full 为全窗 in-sample（含前视，体检用）；z_roll 已入 JSON</span>
  </div>
  <div style="height:14px"></div>
  <div class="pchart"><div class="pzero"></div>{''.join(pbars)}</div>
  <div class="legend" style="margin-top:-26px">
    <span><span class="dot" style="background:var(--up)"></span>000905 上涨</span>
    <span><span class="dot" style="background:var(--dn)"></span>下跌</span>
    <span><span class="dot" style="box-shadow:0 0 0 1.5px var(--ext);background:#fff"></span>p80 变盘日（|pct|≥{thr_p80:.2f}%）</span>
  </div>
</div>

<div class="card">
  <h2>收敛事件清单（{n_events} 个；v2 为 3 个但窗更短）</h2>
  <div class="note">定义（v2 原样）：前一日 |z_full|&gt;{Z_BAND:g}（极端带），当日 gap 跨越全窗均值。事件在自然日历上识别；
        回测锚定"≥事件日的首个交易日"（周末/节假日事件顺延，next-open 语义，无前视）。</div>
  {''.join(cards)}
</div>

<div class="card">
  <h2>变盘窗口回测：收敛日 vs 普通日（指数 000905）</h2>
  <div class="warnbox">样本量警告：有效 gap 日 {stats_all["n_days"]} 个（剔噪声 {stats_clean["n_days"]}），收敛事件 {n_events} 个（剔噪声 {n_ev_clean} 个）。
      h=5 窗在序列末端截断；事件组 n 为个位数量级，p 值仅作参考，<b>禁止据此外推"收敛预测变盘"</b>。</div>
  {bt_table(back_all, "含噪声", "口径A：全部有效 gap 日（含雪球 n<5 噪声日，v2 兼容）。检验：Welch t（不等方差）与 Mann-Whitney U 双尾，" + sci + "。")}
  <div style="height:10px"></div>
  {bt_table(back_clean, "剔噪声", "口径B：剔除噪声日（雪球 n≥5）。稳健参照。")}
  <div class="note" style="margin-top:10px">逐收敛事件明细（口径A；收益 / |收益| / 路径日均|pct|）：</div>
  <table>
    <tr><th>事件日</th><th>+1日</th><th>+3日</th><th>+5日</th></tr>
    {ev_detail}
  </table>
</div>

<div class="card">
  <h2>v1 → v2 → v3 演进</h2>
  <ul class="caveat">
    <li><b>v1（2026-11-04 早）</b>：三源共振体检，30 日窗，gap=雪球bull率−JEV bull率；发现雪球 n&lt;5 日噪声问题与量能错位。无回测。</li>
    <li><b>v2（2026-11-04 午）</b>：gap 均值回复监测 + 变盘窗口回测；z 带/收敛事件定义确立；但变盘用 78 只自选股宽度代理（无指数），
        30 日窗仅 19 个交易日、收敛事件 3 个，只能给点估计（gap 均值 {fmt(v2_stats.get("mean"), 3)} / σ {fmt(v2_stats.get("stdev"), 3)}）。</li>
    <li><b>v3（本版）</b>：接入中证500 真实指数行情（863 交易日 parquet），变盘直接以指数 pct_chg p80 分位/双阈值/方向翻转三口径定义；
        gap 窗拉长至 09-01~11-03（{len(series)} 自然日 / {n_trade} 交易日，v2 为 30 自然日 / 19 交易日），收敛事件 {n_events} 个（v2: 3），首次做 t / MW-U 显著性检验（{sci}）。</li>
  </ul>
</div>

<div class="card">
  <h2>诚实限制</h2>
  <ul class="caveat">
    <li><b>窗口仍短</b>：有效长窗 09-01~11-03（{len(series)} 自然日 / {n_trade} 交易日，其中有效 gap {stats_all["n_days"]}、剔噪声 {stats_clean["n_days"]}）；
        JEV 9 月前 hit=1 稀疏（07 月 6 / 08 月 5），无法前延。</li>
    <li><b>事件锚定</b>：gap 在自然日历上识别，雪球帖 {sum(1 for r in series if r['xq_n'] > 0)} 天有帖（含周末）；事件落在非交易日时锚定下一交易日，
        情绪"发酵时点"与锚定日之间可能有 1-3 日错位，稀释信号。</li>
    <li><b>雪球噪声</b>：2026 年雪球帖日均个位数（54/272/50 帖每月），n&lt;5 日 bull 率由 1-4 帖决定，gap 半边基本是噪声；
        剔噪声口径（口径B）样本更少，双口径都列、结论以两者一致为准。</li>
    <li><b>z_full 前视</b>：全窗 in-sample 标准化（v2 同款），"极端/收敛"标注含未来信息，回测是<b>事件研究体检</b>而非可实盘信号；
        z_roll（扩张窗）已写入 JSON 供后续。</li>
    <li><b>检验力</b>：事件组 n≈{max(1, n_events)}，任何 p 值的检验力都极低；p&gt;α 不等于"证明无效应"，p&lt;α 也要先怀疑多重比较（3 horizons × 3 指标 × 2 口径 = 18 次检验）。</li>
    <li><b>单指数单窗口</b>：仅中证500、仅 2026 秋一窗；备选中证1000 未纳入主检验（防口径漂移），结论不外推其他指数/时段。</li>
    <li><b>只读声明</b>：parquet 与两 db（mode=ro）只读；产物仅写 outputs；未 git 提交。</li>
  </ul>
</div>

<footer>P4 共振 v3 · resonance_v3.py · 产物：2026-11-04_P4共振v3_指数回测.html / 2026-11-04_P4共振v3_数据.json</footer>
</div>
</body>
</html>'''
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("[HTML] →", HTML_OUT)


# ================================================================
# 主流程
# ================================================================
def main():
    print("[1/8] 读指数行情 000905 parquet ...")
    full_idx, win_idx = load_index()
    print(f"      全序列 {len(full_idx)} 个交易日；窗内 {len(win_idx)} 个 "
          f"{win_idx['date'].iloc[0]} ~ {win_idx['date'].iloc[-1]}")
    thr_p80 = float(win_idx["pct_chg"].abs().quantile(0.8))
    print(f"      p80 |pct_chg| 阈值 = {thr_p80:.3f}%")

    print("[2/8] JEV 日 bull 率（hit=1）...")
    jev_daily = load_jev_daily()
    print(f"      {len(jev_daily)} 个自然日有 JEV 事件")

    print("[3/8] 雪球日 bull 率 ...")
    xq_daily = load_xq_daily()
    print(f"      {len(xq_daily)} 个自然日有雪球帖")

    print("[4/8] 合成 gap 序列（自然日时间轴）+ z + 收敛事件 ...")
    series = build_gap_series(win_idx, jev_daily, xq_daily)
    n_calendar = len(series)
    stats_all = gap_stats(series)
    clean_rows = [r for r in series if r["noise"] is not True]
    stats_clean = gap_stats(clean_rows)

    # 口径A：z/事件用全窗（含噪声）统计 —— v2 兼容主口径
    zscore_series(series, stats_all)
    events = find_convergence(series, stats_all)
    n_ext = sum(1 for r in series if r["extreme"])
    print(f"      {n_calendar} 个自然日；有效 gap {stats_all['n_days']}（噪声 {stats_all['n_noise']}，剔噪声 {stats_clean['n_days']}）")
    print(f"      gap all: mean={fmt(stats_all['mean'])} sd={fmt(stats_all['stdev'])} | "
          f"clean: mean={fmt(stats_clean['mean'])} sd={fmt(stats_clean['stdev'])}")
    print(f"      极端日 {n_ext}，收敛事件 {len(events)}")
    for e in events:
        print(f"      {e['date']}  {e['prev_gap']:+.3f}(z={e['prev_z']:+.2f}) -> {e['gap']:+.3f}  "
              f"回落 {e['pullback']:.3f}{'  [含噪声]' if e['noise'] else ''}")

    print("[5/8] 变盘日标记（p80 + 敏感性 + 翻转）...")
    var_days = mark_var_days(win_idx, thr_p80, SENS_THRESHOLDS)
    print(f"      p80 变盘 {sum(1 for v in var_days.values() if v['var_p80'])} 天，"
          f"≥1% {sum(1 for v in var_days.values() if v['var_1pct'])} 天，"
          f"≥1.5% {sum(1 for v in var_days.values() if v['var_1_5pct'])} 天，"
          f"翻转 {sum(1 for v in var_days.values() if v['flip'])} 天")

    print("[6/8] 回测（双口径；事件锚定 ≥事件日的首个交易日）...")
    back_all = backtest(full_idx, win_idx, series, events, "all")
    back_clean = backtest(full_idx, win_idx, series, events, "clean")
    for name, back in (("A含噪声", back_all), ("B剔噪声", back_clean)):
        a = back[1]
        ev, bl = a["event"], a["baseline"]
        print(f"      [{name}] 事件日 {ev['n_days']}，对照 {bl['n_days']}，仅极端 {a['extreme_only']['n_days']}")
        for h in FWD_HORIZONS:
            e3, b3 = ev[f"h{h}"], bl[f"h{h}"]
            t3 = e3["test"].get("abs_ret") or {}
            print(f"        h={h}: |ret| 事件 {fmt(e3['abs_ret_mean'], 2)}%(n={e3['n_obs']}) vs "
                  f"对照 {fmt(b3['abs_ret_mean'], 2)}%(n={b3['n_obs']})"
                  f"  p_t={t3.get('p_t')} p_u={t3.get('p_u')}")

    print("[7/8] 月度量 + v2 对照 ...")
    con = ro(NEWS_DB)
    jev_monthly = dict(con.execute(
        "SELECT substr(ts,1,7), count(*) FROM news WHERE hit=1 AND ts>=? AND ts<? GROUP BY 1",
        (WIN_START, "2026-12-01")).fetchall())
    con.close()
    xq_monthly = {"2026-09": 54, "2026-10": 272, "2026-11": 50}  # 探查实测值（脚本内重算）
    con = ro(XQ_DB)
    xq_monthly = dict(con.execute("""
        SELECT substr(x.created_at,1,7), count(*) FROM xq_status_classified c
        JOIN xq_status x ON c.status_id=x.status_id
        WHERE x.created_at>='2026-09-01' AND x.created_at<'2026-12-01' GROUP BY 1""").fetchall())
    con.close()
    v2_stats = {"mean": None, "stdev": None}
    try:
        v2 = json.load(open(V2_JSON, encoding="utf-8"))
        v2_stats = v2.get("gap_stats", {})
    except Exception:
        pass
    print(f"      JEV 月度 {jev_monthly}；雪球月度 {xq_monthly}")

    print("[8/8] 写产物 ...")
    meta = {
        "date": "2026-11-04",
        "task": "P4 共振 v3 —— 真实指数行情（中证500）× 长窗 gap 收敛事件变盘回测",
        "window": f"{WIN_START} ~ {WIN_END}（JEV hit=1 有效长窗自 09-01 起，口径如实）",
        "index": f"000905.SZ 中证500（863 交易日 parquet，窗内 {len(win_idx)} 交易日；前向收益用全序列）",
        "gap_def": "雪球bull率 − JEV bull率（v1/v2 口径，自然日时间轴；雪球 n<5 日标 noise，双口径）",
        "z_def": {"z_full": "全窗 in-sample z（含前视，体检用）",
                  "z_roll": f"扩张窗 z（min {ROLL_MIN}）",
                  "band": f"|z_full|>{Z_BAND:g} 极端；收敛=前日极端且当日 gap 跨越全窗均值（v2 原定义）"},
        "var_def": {"main": f"|pct_chg| ≥ 全窗80分位（{thr_p80:.3f}%）",
                    "sensitivity": [f"|pct_chg| ≥ {t}%" for t in SENS_THRESHOLDS],
                    "flip": f"sign 翻转 且 |Δpct_chg|>{FLIP_DELTA}%"},
        "backtest_def": "收敛事件（自然日）锚定 ≥事件日的首个交易日 vs 普通交易日（剔极端未收敛锚定日），后 1/3/5 日累计收益/|波动|；Welch t + MW-U 双尾",
        "scipy": HAVE_SCIPY,
        "limits": [
            f"有效 gap 日 {stats_all['n_days']}（剔噪声 {stats_clean['n_days']}），事件 {len(events)} 个，检验力极低",
            "z_full 含前视；事件研究体检口径，非实盘信号",
            "雪球 2026 帖量日均个位数，n<5 日 gap 半边为噪声（双口径对冲）",
            "仅中证500 单指数单窗口，不外推",
        ],
    }
    payload = dump_json(meta, series, events, stats_all, stats_clean, var_days, thr_p80,
                        back_all, back_clean, jev_monthly, xq_monthly)
    build_html(payload, series, events, stats_all, stats_clean, win_idx, var_days, thr_p80,
               back_all, back_clean, jev_monthly, xq_monthly, v2_stats)

    print("\n===== v3 摘要 =====")
    print(f"窗 {WIN_START}~{WIN_END}：{n_calendar} 自然日 / {len(win_idx)} 交易日，"
          f"有效 gap {stats_all['n_days']}（噪声 {stats_all['n_noise']}），"
          f"收敛事件 {len(events)}（v2: 3）")
    print(f"gap all mean={fmt(stats_all['mean'])} sd={fmt(stats_all['stdev'])} | "
          f"clean mean={fmt(stats_clean['mean'])} sd={fmt(stats_clean['stdev'])}")
    ev, bl = back_all[1]["event"], back_all[1]["baseline"]
    for h in FWD_HORIZONS:
        e3, b3 = ev[f"h{h}"], bl[f"h{h}"]
        t3 = e3["test"].get("abs_ret") or {}
        print(f"h={h}: ret {fmt(e3['ret_mean'], 2)}% vs {fmt(b3['ret_mean'], 2)}% | "
              f"|ret| {fmt(e3['abs_ret_mean'], 2)} vs {fmt(b3['abs_ret_mean'], 2)} "
              f"(n={e3['n_obs']}/{b3['n_obs']}) p_t={t3.get('p_t')} p_u={t3.get('p_u')}")


if __name__ == "__main__":
    main()
