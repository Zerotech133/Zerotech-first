# -*- coding: utf-8 -*-
"""多棱镜：一份事实 × 多种互斥解释 × 数据判定。

设计前提（与「短期测试无效」一致）：
  1) 观测单位是「一次选股」，不是「一只票」。同批 80 只共享同一个市场日，
     当成 80 个样本会把误差缩小十几倍，看起来显著其实全是假的。
  2) 任何结论必须先声明「若成立应看到什么」和「若不成立应看到什么」，
     再用数据判。事后改门槛的结论一律不算。
  3) 数据不足时输出「暂不可判」，不输出「倾向」——倾向就是编。
  4) 维度数据过期的（如板块停在 1012）必须标过期，不能当"无异常"。

输出：
  lens/latest.json      给页面用
  lens/grade_log.jsonl  每次判定追加一行（过程即证据）
"""
import os
import io
import json
import glob
import math
import datetime
import traceback

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))            # <项目根>
QS = os.path.join(ROOT, "quant_backtest", "data", "tushare")
COH = os.path.join(HERE, "cohorts")
LENS = os.path.join(HERE, "lens")


def _extra_data_dir():
    """两融/资金流/涨停/板块这几个维度来自本机另一处数据目录。

    本仓对外可见，所以不在这里写那个目录的绝对路径，也不写它的仓名。
    取值顺序：环境变量 ZQ_LENS_DATA → 同目录 lens_paths.json（本机配置，不入库）。
    两处都没有时返回 None，相关维度如实降级为「缺失」，不猜、不兜假数据。
    """
    v = os.environ.get("ZQ_LENS_DATA")
    if v and os.path.isdir(v):
        return v
    cfg = os.path.join(HERE, "lens_paths.json")
    if os.path.exists(cfg):
        try:
            p = json.load(io.open(cfg, encoding="utf-8")).get("extra_data")
            if p and os.path.isdir(p):
                return p
        except Exception:
            pass
    return None


KG = _extra_data_dir()

COST_ROUND = 0.0030      # 双边成本（佣金+滑点）
MIN_OBS = 10             # 能下结论的最少观测数
HOLDS = (1, 2, 3)
HEADLINE_HOLD = 2        # 次日开盘买、第三日收盘卖？不：hold=1 为当日收盘卖

# hold 语义：entry=信号次日开盘；exit = entry 之后第 (hold-1) 个交易日收盘
#   hold=1 → 当日收盘卖（与选股台账「1 日」同口径，可对账）
#   hold=2 → 次日收盘卖（更接近实盘可行，页面主口径）
MAIN_POOL_NOTE = "主选池只有 1~7 只票，单票权重过高，与自动池不能共用一把尺"


# ── 基础读取 ─────────────────────────────────────────────────────
_CAL = None


def trading_cal():
    global _CAL
    if _CAL is None:
        fs = glob.glob(os.path.join(QS, "daily", "d_*.parquet"))
        ds = []
        for p in fs:
            b = os.path.basename(p)
            d = b[2:10]
            if d.isdigit() and len(d) == 8:
                ds.append(d)
        _CAL = sorted(set(ds))
    return _CAL


def _p(d, sub):
    return os.path.join(QS, sub, "d_%s.parquet" % d)


def load_px(d):
    """{code: (open_adj, close_adj)}；用复权因子把两日拉到同一口径。"""
    if not os.path.exists(_p(d, "adj_factor")):
        return {}
    a = pd.read_parquet(_p(d, "adj_factor"))
    a["ts_code"] = a.ts_code.astype(str)
    am = dict(zip(a.ts_code, a.adj_factor.astype(float)))
    b = pd.read_parquet(_p(d, "daily"))
    b["ts_code"] = b.ts_code.astype(str)
    out = {}
    for r in b.itertuples(index=False):
        f = am.get(r.ts_code)
        if not f or f <= 0:
            continue
        o, c = float(r.open), float(r.close)
        if o <= 0 or c <= 0:
            continue
        out[r.ts_code] = (o * f, c * f)
    return out


def load_mv(d):
    p = _p(d, "daily_basic")
    if not os.path.exists(p):
        return {}
    b = pd.read_parquet(p, columns=["ts_code", "circ_mv"])
    b["ts_code"] = b.ts_code.astype(str)
    v = b.circ_mv.astype(float)
    return dict(zip(b.ts_code, v))


def load_index(d):
    p = _p(d, "index_daily")
    if not os.path.exists(p):
        return {}, None
    b = pd.read_parquet(p)
    b["ts_code"] = b.ts_code.astype(str)
    return {r.ts_code: (float(r.open), float(r.close)) for r in b.itertuples(index=False)}, d


def load_index_back(asof, back=5):
    """指数文件通常比个股晚一天。往前找最近一个能读到的，并把日期一起返回，
    免得「T-1 正常」被误判成「缺失」。"""
    cal = trading_cal()
    if asof not in cal:
        return {}, None
    i = cal.index(asof)
    for k in range(0, back + 1):
        if i - k < 0:
            break
        d = cal[i - k]
        m, got = load_index(d)
        if m:
            return m, got
    return {}, None


def _fmt_date(x):
    s = str(x).replace("-", "")
    return s[:8]


def margin_table():
    if not KG:
        return None
    p = os.path.join(KG, "margin_detail_delta.parquet")
    if not os.path.exists(p):
        return None
    try:
        m = pd.read_parquet(p, columns=["trade_date", "ts_code", "rzye"])
        m["d"] = m.trade_date.map(_fmt_date)
        return m
    except Exception:
        return None


def moneyflow_table():
    if not KG:
        return None
    p = os.path.join(KG, "moneyflow_delta.parquet")
    if not os.path.exists(p):
        return None
    try:
        m = pd.read_parquet(p, columns=["trade_date", "ts_code", "net_mf_amount",
                                        "buy_lg_amount", "sell_lg_amount"])
        m["d"] = m.trade_date.map(_fmt_date)
        return m
    except Exception:
        return None


# ── 观测：一次选股 = 一个观测 ────────────────────────────────────
def observations():
    cal = trading_cal()
    idx = {d: i for i, d in enumerate(cal)}
    obs = []
    for fp in sorted(glob.glob(os.path.join(COH, "cohort_*.json"))):
        try:
            c = json.load(io.open(fp, encoding="utf-8"))
        except Exception:
            continue
        asof = str(c.get("asof") or "")
        codes = c.get("codes") or []
        if asof not in idx or not codes:
            continue
        i = idx[asof]
        if i + 1 >= len(cal):
            continue
        entry = cal[i + 1]
        for hold in HOLDS:
            j = i + hold
            if j >= len(cal):
                continue
            exit_d = cal[j]
            ep, xp = load_px(entry), load_px(exit_d)
            if not ep or not xp:
                continue

            def ret_of(code):
                a, b = ep.get(code), xp.get(code)
                if not a or not b:
                    return None
                return b[1] / a[0] - 1.0

            pr = [ret_of(x) for x in codes]
            pr = [x for x in pr if x is not None]
            if not pr:
                continue
            common = [x for x in ep if x in xp]
            allr = [ret_of(x) for x in common]
            allr = [x for x in allr if x is not None]
            if len(allr) < 200:
                continue

            b_all = float(np.mean(allr))
            b_med = float(np.median(allr))

            mt = margin_table()
            b_margin = None
            if mt is not None:
                s = set(mt[(mt.d == asof) & (mt.rzye > 0)].ts_code.astype(str))
                mr = [ret_of(x) for x in common if x in s]
                mr = [x for x in mr if x is not None]
                if len(mr) >= 100:
                    b_margin = float(np.mean(mr))

            mv = load_mv(entry)
            b_size, size_q = None, None
            pv = [mv.get(x) for x in codes]
            pv = [x for x in pv if x and x > 0]
            if pv and mv:
                ac = [x for x in common if mv.get(x) and mv[x] > 0]
                if len(ac) > 200:
                    vals = np.array([mv[x] for x in ac])
                    cuts = np.quantile(vals, [0.2, 0.4, 0.6, 0.8])
                    pq = int(np.searchsorted(cuts, float(np.median(pv))))
                    bucket = [x for x in ac if int(np.searchsorted(cuts, mv[x])) == pq]
                    br = [ret_of(x) for x in bucket]
                    br = [x for x in br if x is not None]
                    if len(br) >= 30:
                        b_size, size_q = float(np.mean(br)), "Q%d" % (pq + 1)

            # 市值五分位收益（风格维度）
            quint = {}
            if mv:
                ac = [x for x in common if mv.get(x) and mv[x] > 0]
                if len(ac) > 200:
                    vals = np.array([mv[x] for x in ac])
                    cuts = np.quantile(vals, [0.2, 0.4, 0.6, 0.8])
                    qs = np.searchsorted(cuts, vals)
                    for k in range(5):
                        grp = [x for x, q in zip(ac, qs) if q == k]
                        rr = [ret_of(x) for x in grp]
                        rr = [x for x in rr if x is not None]
                        if rr:
                            quint["Q%d" % (k + 1)] = float(np.mean(rr))

            obs.append({
                "asof": asof, "pool": c.get("pool"), "hold": hold,
                "n_stocks": len(pr), "entry": entry, "exit": exit_d,
                "pool_ret": float(np.mean(pr)),
                "b_all": b_all, "b_med": b_med,
                "b_margin": b_margin, "b_size": b_size, "size_q": size_q,
                "quint": quint,
                "up_ratio": float(np.mean([1.0 if x > 0 else 0.0 for x in allr])),
                "n_all": len(allr),
            })
    return obs


# ── 汇总统计 ─────────────────────────────────────────────────────
def _mean(v):
    return float(np.mean(v)) if v else None


def _sd(v):
    return float(np.std(v, ddof=1)) if len(v) > 1 else None


def _ci(v):
    n = len(v)
    if n < 2:
        return None, None
    sd = float(np.std(v, ddof=1))
    se = sd / math.sqrt(n)
    return float(np.mean(v)) - 1.96 * se, float(np.mean(v)) + 1.96 * se


def summarize(obs, pool, hold, bkey):
    sub = [o for o in obs if o["pool"] == pool and o["hold"] == hold
           and o.get(bkey) is not None]
    v = [o["pool_ret"] - o[bkey] for o in sub]
    return {"pool": pool, "hold": hold, "bench": bkey, "n": len(v),
            "mean": _mean(v), "sd": _sd(v),
            "se": (_sd(v) / math.sqrt(len(v))) if len(v) > 1 else None,
            "ci": _ci(v),
            "pos": sum(1 for x in v if x > 0)}


def need_obs(sd, eff):
    if not sd or sd <= 0 or eff <= 0:
        return None
    return int(math.ceil((1.96 * sd / eff) ** 2))


# ── 维度状态（全维度进货）────────────────────────────────────────
def dim_freshness(name, path, expect):
    if not os.path.exists(path):
        return {"name": name, "status": "missing", "latest": None,
                "note": "数据文件不存在，本维度不参与判定"}
    try:
        if path.endswith(".parquet"):
            df = pd.read_parquet(path)
            col = None
            for c in ("trade_date", "date"):
                if c in df.columns:
                    col = c
                    break
            if col is None:
                return {"name": name, "status": "unknown", "latest": None,
                        "note": "无日期列，无法判断新鲜度"}
            latest = max(df[col].map(_fmt_date))
        else:
            latest = None
    except Exception as e:
        return {"name": name, "status": "error", "latest": None,
                "note": "读取失败：%s" % str(e)[:60]}
    lag = None
    cal = trading_cal()
    if latest in cal and expect in cal:
        lag = cal.index(expect) - cal.index(latest)
    ok = (latest == expect) or (lag is not None and 0 <= lag <= 1)
    return {"name": name, "status": "fresh" if ok else "stale",
            "latest": latest, "lag_days": lag,
            "note": ("最新 %s，可用" % latest) if ok
                    else ("最新 %s，落后 %s 个交易日 → 不能当证据" % (latest, lag))}


def session_breadth(asof):
    """当日全市场宽度：不依赖有没有选股记录，直接由日线算。"""
    cal = trading_cal()
    if asof not in cal:
        return None
    i = cal.index(asof)
    if i == 0:
        return None
    prev = cal[i - 1]
    a, b = load_px(prev), load_px(asof)
    if not a or not b:
        return None
    mv = load_mv(asof)
    rets, codes = [], []
    for c in a:
        if c not in b:
            continue
        rets.append(b[c][1] / a[c][1] - 1.0)
        codes.append(c)
    if len(rets) < 200:
        return None
    arr = np.array(rets)
    out = {"up_ratio": float(np.mean(arr > 0)),
           "median": float(np.median(arr)),
           "mean": float(np.mean(arr)),
           "n": int(len(arr))}
    codes = np.array(codes, dtype=object)
    if mv:
        vals = np.array([mv.get(x, 0.0) or 0.0 for x in codes])
        ok = vals > 0
        if ok.sum() > 200:
            v, r = vals[ok], arr[ok]
            cuts = np.quantile(v, [0.2, 0.4, 0.6, 0.8])
            qs = np.searchsorted(cuts, v)
            quint = {}
            for k in range(5):
                sel = r[qs == k]
                if len(sel):
                    quint["Q%d" % (k + 1)] = float(np.mean(sel))
            out["quint"] = quint
    return out


def _fresh_or_missing(name, fname, asof):
    """数据源没配置时，如实说「缺失」，不让维度凭空消失。"""
    if not KG:
        return {"key": fname.split("_")[0], "name": name, "status": "missing",
                "latest": None, "note": "本机未配置该数据源（ZQ_LENS_DATA 或 lens_paths.json）"}
    d = dim_freshness(name, os.path.join(KG, fname), asof)
    d["key"] = fname.split("_")[0]
    return d


def market_dimensions(asof):
    cal = trading_cal()
    dims = []
    # 1) 大盘与宽基（指数常比个股晚一天，往前找并如实标日期）
    try:
        cur_i, idx_date = load_index_back(asof)
        lag = (cal.index(asof) - cal.index(idx_date)) if idx_date else None
        prev_i = {}
        if idx_date and idx_date in cal:
            k = cal.index(idx_date)
            if k > 0:
                prev_i, _ = load_index(cal[k - 1])
        want = [("000300.SH", "沪深300"), ("000905.SH", "中证500"),
                ("000852.SH", "中证1000"), ("932000.CSI", "中证2000")]
        day = {}
        for code, nm in want:
            a, b = prev_i.get(code), cur_i.get(code)
            if a and b:
                day[nm] = b[1] / a[1] - 1.0
        status = "missing" if not day else ("fresh" if lag == 0 else "lag")
        dims.append({"key": "index", "name": "大盘方向", "status": status,
                     "value": day, "asof": idx_date, "lag_days": lag,
                     "note": ("%s ｜ %s" % ("｜".join("%s %+.2f%%" % (k, v * 100)
                                                    for k, v in day.items()),
                                           idx_date)) if day else "读不到指数"})
    except Exception as e:
        dims.append({"key": "index", "name": "大盘方向", "status": "error",
                     "value": {}, "note": str(e)[:80]})

    # 2) 市场宽度（当日全市场，不依赖选股记录）
    try:
        sb = session_breadth(asof)
        if sb:
            q = sb.get("quint") or {}
            spread = (q["Q1"] - q["Q5"]) if ("Q1" in q and "Q5" in q) else None
            dims.append({"key": "breadth", "name": "市场宽度", "status": "fresh",
                         "value": q, "up_ratio": sb["up_ratio"],
                         "size_spread": spread,
                         "note": "%d 只，上涨占 %.0f%%，中位数 %+.2f%%，小盘−大盘 %s"
                                 % (sb["n"], sb["up_ratio"] * 100, sb["median"] * 100,
                                    ("%+.2fpp" % (spread * 100)) if spread is not None else "—")})
        else:
            dims.append({"key": "breadth", "name": "市场宽度", "status": "missing",
                         "value": {}, "note": "当日日线不足，宽度不可算"})
    except Exception as e:
        dims.append({"key": "breadth", "name": "市场宽度", "status": "error",
                     "value": {}, "note": str(e)[:80]})

    # 3) 市值风格（五分位，最新可得观测日）
    try:
        cand = [o for o in observations() if o["hold"] == 1 and o.get("quint")]
        cand.sort(key=lambda x: x["asof"])
        if cand:
            o = cand[-1]
            lag = (cal.index(asof) - cal.index(o["asof"])) if (asof in cal and o["asof"] in cal) else None
            q = o["quint"]
            dims.append({"key": "style", "name": "市值风格",
                         "status": "fresh" if (lag is not None and lag <= 1) else "lag",
                         "value": q, "asof": o["asof"], "lag_days": lag,
                         "note": "小盘 %+.2f%% ｜大盘 %+.2f%%（%s%s）"
                                 % (q.get("Q1", 0) * 100, q.get("Q5", 0) * 100, o["asof"],
                                    "，T-1 正常" if lag == 1 else "")})
        else:
            dims.append({"key": "style", "name": "市值风格", "status": "missing",
                         "value": {}, "note": "无市值数据"})
    except Exception as e:
        dims.append({"key": "style", "name": "市值风格", "status": "error",
                     "value": {}, "note": str(e)[:80]})

    # 4) 两融杠杆（T-1 发布属正常，不算过期）
    try:
        mt = margin_table()
        if mt is not None:
            g = mt.groupby("d").rzye.sum()
            ds = sorted(g.index)
            use = [d for d in ds if d <= asof]
            if use:
                cur = use[-1]
                k = ds.index(cur)
                if k > 0:
                    chg = g[cur] / g[ds[k - 1]] - 1.0
                    lag = (cal.index(asof) - cal.index(cur)) if (asof in cal and cur in cal) else None
                    dims.append({"key": "margin", "name": "两融杠杆",
                                 "status": "fresh" if (lag is not None and lag <= 1) else "stale",
                                 "value": float(chg), "latest": cur, "lag_days": lag,
                                 "note": "融资余额环比 %+.2f%%（%s%s）"
                                         % (chg * 100, cur,
                                            "，T-1 正常" if lag == 1 else "")})
                else:
                    dims.append({"key": "margin", "name": "两融杠杆", "status": "stale",
                                 "value": None, "latest": cur, "note": "只有一天，算不出环比"})
            else:
                dims.append({"key": "margin", "name": "两融杠杆", "status": "missing",
                             "value": None, "note": "无覆盖当日之前的记录"})
        else:
            dims.append({"key": "margin", "name": "两融杠杆", "status": "missing",
                         "value": None, "note": "两融明细缺失"})
    except Exception as e:
        dims.append({"key": "margin", "name": "两融杠杆", "status": "error",
                     "value": None, "note": str(e)[:80]})

    # 5) 资金流（大单，T-1 发布属正常）
    try:
        mf = moneyflow_table()
        if mf is not None:
            ds = sorted(mf.d.unique())
            use = [d for d in ds if d <= asof]
            if use:
                cur = use[-1]
                s = mf[mf.d == cur]
                net_lg = float((s.buy_lg_amount - s.sell_lg_amount).sum()) / 1e4
                lag = (cal.index(asof) - cal.index(cur)) if (asof in cal and cur in cal) else None
                dims.append({"key": "moneyflow", "name": "大单资金",
                             "status": "fresh" if (lag is not None and lag <= 1) else "stale",
                             "value": net_lg, "latest": cur, "lag_days": lag,
                             "note": "大单净流入 %+.1f 亿元（%s%s）"
                                     % (net_lg, cur, "，T-1 正常" if lag == 1 else "")})
            else:
                dims.append({"key": "moneyflow", "name": "大单资金", "status": "missing",
                             "value": None, "note": "无覆盖当日之前的记录"})
        else:
            dims.append({"key": "moneyflow", "name": "大单资金", "status": "missing",
                         "value": None, "note": "资金流缺失"})
    except Exception as e:
        dims.append({"key": "moneyflow", "name": "大单资金", "status": "error",
                     "value": None, "note": str(e)[:80]})

    # 6) 涨停情绪（数据可能落后，如实标）
    dims.append(_fresh_or_missing("涨停情绪", "limit_list_d_full.parquet", asof))

    # 7) 板块轮动（数据可能落后）
    dims.append(_fresh_or_missing("板块轮动", "sector_daily.parquet", asof))

    for i, d in enumerate(dims):
        d.setdefault("key", "dim%d" % i)
    return dims


# ── 假设 ─────────────────────────────────────────────────────────
def pv(x, d=3):
    """百分比展示。None 显示为 —，不显示 0.000%。"""
    if x is None:
        return "—"
    return ("%+." + str(d) + "f%%") % (x * 100.0)


def pa(x, d=3):
    """不带正负号的百分比（标准误、成本线这类）。"""
    if x is None:
        return "—"
    return ("%." + str(d) + "f%%") % (x * 100.0)


def build_hypotheses(auto_stats, main_stats, obs, asof):
    """5 条互斥解释。全部先声明判据，再拿数据判，事后不改门槛。"""
    a_size = auto_stats["b_size"]
    a_marg = auto_stats["b_margin"]
    a_all = auto_stats["b_all"]
    n = a_size["n"]
    m = a_size["mean"]
    se = a_size["se"]
    lo, hi = a_size["ci"]

    # 基准敏感度：换成哪条基准，均值最多动多少
    means = [s["mean"] for s in (a_size, a_marg, a_all) if s["mean"] is not None]
    bench_range = (max(means) - min(means)) if len(means) >= 2 else None
    sign_flip = None
    if means:
        sign_flip = (min(means) < 0 < max(means))

    # 风格：池所在市值档相对全市场同期差
    diffs = [o["b_size"] - o["b_all"] for o in obs
             if o["pool"] == "auto" and o["hold"] == HEADLINE_HOLD
             and o.get("b_size") is not None and o.get("b_all") is not None]
    size_tilt = _mean(diffs)

    # 小盘相对大盘（同期）
    sp = []
    for o in obs:
        if o["pool"] != "auto" or o["hold"] != HEADLINE_HOLD:
            continue
        q = o.get("quint") or {}
        if q.get("Q1") is not None and q.get("Q5") is not None:
            sp.append(q["Q1"] - q["Q5"])
    size_spread = _mean(sp)

    out = []

    # H1 风格逆风
    if m is None or n < 2:
        st, vd = "open", "证据不足"
    elif size_tilt is not None and size_tilt <= -0.005 and size_spread is not None and size_spread < 0:
        st, vd = "confirmed", "风格逆风成立"
    elif size_spread is not None and size_spread > 0.005:
        st, vd = "refuted", "小盘同期跑赢大盘，逆风不成立"
    else:
        st, vd = "open", "风格影响不到 0.5 个百分点，解释不了"
    out.append({
        "id": "H1", "claim": "风格逆风：池子偏中小市值，这几天小盘整体走弱，收益被风格拖累",
        "expect_true": "池所在市值档同期跑输全市场 ≥0.5pp，且小盘跑输大盘",
        "expect_false": "小盘同期跑赢大盘 ≥0.5pp——风格是顺风，解释不了亏损",
        "status": st, "verdict": vd,
        "numbers": [("池所在档 − 全市场", pv(size_tilt)), ("小盘 Q1 − 大盘 Q5", pv(size_spread))],
        "decision": "成立则做市值中性化（池内按市值分层抽），不成立则不必动因子结构"})

    # H5 基准错
    if bench_range is None:
        st, vd = "open", "证据不足"
    elif sign_flip or bench_range > 0.005:
        st, vd = "confirmed", "换基准后符号翻转/差异 >0.5pp，原结论依赖基准选择"
    else:
        st, vd = "refuted", "换四种基准，超额最多只动 %.2fpp，翻不了符号" % (bench_range * 100)
    out.append({
        "id": "H5", "claim": "基准错：现有对照是两融标的池（偏大中盘），把小盘选股的贡献压低了",
        "expect_true": "换到同市值档/全市场等权后，超额改善 >0.5pp 或符号翻转",
        "expect_false": "各基准下超额差异 <0.5pp——基准不是问题所在",
        "status": st, "verdict": vd,
        "numbers": [("四基准极差", pv(bench_range)), ("是否存在符号翻转", "是" if sign_flip else "否")],
        "decision": "成立则先修基准再谈策略；不成立则基准沿用，注意力转向因子本身"})

    # H3 成本吃掉
    if m is None or n < 2:
        st, vd = "open", "证据不足"
    elif 0 < m < COST_ROUND and lo is not None and hi > 0:
        st, vd = "blocked", "毛超额为正但点估计 < 成本线，需更多观测确认是否真的为正"
    elif m >= COST_ROUND:
        st, vd = "refuted", "毛超额已高于成本线，不是成本问题"
    elif m <= 0 and hi is not None and hi < COST_ROUND:
        st, vd = "refuted", "毛超额连成本线都够不到，扣成本前就已经是负的"
    else:
        st, vd = "open", "方向未定"
    out.append({
        "id": "H3", "claim": "成本吃掉：选股本身有小幅正优势，被 0.3% 双边成本吃平了",
        "expect_true": "毛超额落在 0 ~ 0.3% 区间，且置信区间跨过 0",
        "expect_false": "毛超额 ≥0.3%（成本不是瓶颈）或 ≤0（扣成本前就输了）",
        "status": st, "verdict": vd,
        "numbers": [("同市值毛超额均值", pv(m)), ("95% 区间下界", pv(lo)), ("成本线", pv(COST_ROUND))],
        "decision": "成立则把成本压下去（降换手/改市价单/降频率）比换因子更值"})

    # H2 无 alpha
    if m is None or n < 2:
        st, vd = "open", "证据不足"
    elif n < MIN_OBS:
        st, vd = "blocked", "观测 %d 个，门槛 %d 个——现在说「无 alpha」和说「有 alpha」一样没有根据" % (n, MIN_OBS)
    elif abs(m) < 0.002 and lo is not None and lo < 0 < hi:
        st, vd = "confirmed", "同市值同窗口下超额≈0，选股没提供增量信息"
    elif m > 0 and lo is not None and lo > 0:
        st, vd = "refuted", "同市值超额下界 >0，选股有增量"
    else:
        st, vd = "open", "方向未定"
    out.append({
        "id": "H2", "claim": "无 alpha：把市值和窗口对齐之后，选股没有增量信息",
        "expect_true": "同市值超额 |均值| <0.2pp 且区间跨 0，且观测数已过门槛",
        "expect_false": "同市值超额区间下界 >0",
        "status": st, "verdict": vd,
        "numbers": [("同市值毛超额均值", pv(m)), ("观测数", "%d 个" % n), ("门槛", "%d 个" % MIN_OBS)],
        "decision": "成立则停掉日频选股这条线，把资源转到「何时不交易」的择时闸门"})

    # H4 纯噪声
    if m is None or se is None:
        st, vd = "open", "证据不足"
    elif abs(m) < se:
        st, vd = "confirmed", "效应 %.2fpp 小于标准误 %.2fpp，现在这个数字读不出方向" % (m * 100, se * 100)
    elif abs(m) >= 2 * se:
        st, vd = "refuted", "效应已达 %.1f 倍标准误，不是纯噪声" % (abs(m / se))
    else:
        st, vd = "open", "介于 1~2 倍标准误，还判不了"
    out.append({
        "id": "H4", "claim": "纯噪声：样本太短，现在的超额是几天的市场噪声，不承载信息",
        "expect_true": "|均值| < 标准误",
        "expect_false": "|均值| ≥ 2 倍标准误，噪声解释不了",
        "status": st, "verdict": vd,
        "numbers": [("同市值毛超额均值", pv(m)), ("标准误", pa(se)),
                    ("还需观测数", "%d 个" % (need_obs(a_size["sd"], COST_ROUND) or 0))],
        "decision": "成立则禁止任何调参与排名，只继续记账——调整本身就是噪声驱动的过拟合"})

    # H6 衰减（事后提出：由「换持有窗口」的单调性看到，post_hoc 必须标）
    w = {}
    for h in HOLDS:
        st_ = summarize(obs, "auto", h, "b_size")
        w[h] = st_
    vals = [w[h]["mean"] for h in HOLDS if w[h]["mean"] is not None]
    mono = (len(vals) >= 3 and all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)))
    drop = (vals[0] - vals[-1]) if len(vals) >= 2 else None
    n_min = min([w[h]["n"] for h in HOLDS] or [0])
    if len(vals) < 2:
        st, vd = "open", "证据不足"
    elif mono and drop is not None and drop > 0.005 and n_min >= 5:
        st, vd = "confirmed", "超额随持有期单调下滑 %.2fpp，alpha 只在当日有效" % (drop * 100)
    elif mono and drop is not None and drop > 0.005:
        st, vd = "blocked", "形状成立（持有越久越差 %.2fpp），但最短窗口只有 %d 个观测，样本不够" % (drop * 100, n_min)
    elif not mono:
        st, vd = "refuted", "换窗口不单调，没有稳定的衰减形状"
    else:
        st, vd = "open", "方向未定"
    out.append({
        "id": "H6", "claim": "衰减：超额只在买入当日存在，隔夜就被吃掉——所以「选股」其实是「选当日强弱」",
        "post_hoc": True,
        "expect_true": "持有 1→2→3 日超额单调下滑，且首尾差 >0.5pp（事后假设，门槛加倍：每组 ≥5 个观测）",
        "expect_false": "换窗口不单调，或首尾差 <0.5pp",
        "status": st, "verdict": vd,
        "numbers": [("持有 1 日", pv(w[1]["mean"])), ("持有 2 日", pv(w[2]["mean"])),
                    ("持有 3 日", pv(w[3]["mean"])), ("首尾差", pv(drop))],
        "decision": "成立则交易规则改成当日了结，不留隔夜——这会直接改写买卖与仓位规则"})

    # 存活组合
    alive = [h["id"] for h in out if h["status"] in ("confirmed", "blocked", "open")]
    dead = [h["id"] for h in out if h["status"] == "refuted"]
    return out, {"alive": alive, "dead": dead,
                 "joint": joint_text(out),
                 "action": joint_action(out),
                 "headline": headline_text(out, n, m)}


def joint_text(hyps):
    """把「同时活着的几条」合成一句话——这才是组合方式，不是并列清单。"""
    d = {h["id"]: h for h in hyps}
    parts = []
    if d["H4"]["status"] == "confirmed":
        parts.append("现在的超额连一个标准误都没超过，本质读不出方向")
    elif d["H4"]["status"] == "open":
        parts.append("超额只有 1~2 倍标准误，还读不出方向")
    if d["H6"]["status"] == "blocked":
        parts.append("「持有越久越差」的形状已经出现，但最短窗口只有 2~4 个观测")
    elif d["H6"]["status"] == "confirmed":
        parts.append("持有越久越差已成立，alpha 只在当日")
    if d["H2"]["status"] == "blocked":
        parts.append("选股到底有没有增量，样本还判不了")
    elif d["H2"]["status"] == "confirmed":
        parts.append("样本已够且增量≈0，日频选股这条线可以停")
    if d["H3"]["status"] == "confirmed":
        parts.append("毛超额落在成本线以内，问题在成本不在选股")
    if not parts:
        return "当前没有同时成立的解释组合。"
    return "；".join(parts) + "。"


def joint_action(hyps):
    d = {h["id"]: h for h in hyps}
    if d["H2"]["status"] == "confirmed":
        return "停掉日频选股，把资源转到「何时不交易」的闸门与仓位规则上。"
    acts = ["继续记账，不加仓、不调参（调参就是拿噪声当依据）"]
    if d["H6"]["status"] in ("blocked", "confirmed"):
        acts.append("把「当日了结」列成唯一值得测的变体，等样本够再判")
    if d["H1"]["status"] == "refuted" and d["H5"]["status"] == "refuted":
        acts.append("市值中性化与换基准这两件事可以从待办里划掉——数据已经说了不用")
    return "；".join(acts) + "。"


def headline_text(hyps, n, m):
    d = {h["id"]: h for h in hyps}
    dead = [k for k in ("H1", "H5", "H3", "H4", "H2", "H6") if d[k]["status"] == "refuted"]
    if d["H4"]["status"] == "confirmed":
        return ("唯一站得住的是「纯噪声」：%d 个观测、同市值毛超额 %+.2f%%，比标准误还小。"
                "已判死 %s。剩下的要等样本。" % (n, (m or 0) * 100, "、".join(dead) or "无"))
    if d["H2"]["status"] == "confirmed":
        return "样本已够且超额≈0：选股没有增量，该停了。已判死 %s。" % ("、".join(dead) or "无")
    if d["H2"]["status"] == "refuted":
        return "同市值超额下界已 >0，选股有增量，可以进入下一步。"
    alive = [h["id"] for h in hyps if h["status"] in ("confirmed", "blocked", "open")]
    return ("%d 个观测：已判死 %s，还剩 %s 要等数据——现在就下结论，无论偏向哪边都是编的。"
            % (n, "、".join(dead) or "无", "、".join(a for a in alive if a != "H4") or "无"))


# ── 主流程 ───────────────────────────────────────────────────────
def main():
    os.makedirs(LENS, exist_ok=True)
    cal = trading_cal()
    asof = cal[-1]

    print("=" * 78)
    print("多棱镜 · 一份事实 × 多种可能")
    print("=" * 78)
    print("数据日（最新交易日）:", asof)

    obs = observations()
    print("观测数:", len(obs), "（每次选股 × 每个持有窗口 = 一条）")

    auto_stats = {k: summarize(obs, "auto", HEADLINE_HOLD, k)
                  for k in ("b_size", "b_margin", "b_all", "b_med")}
    main_stats = {k: summarize(obs, "main", HEADLINE_HOLD, k)
                  for k in ("b_size", "b_margin", "b_all", "b_med")}

    print()
    sizes = [o["n_stocks"] for o in obs if o["pool"] == "auto" and o["hold"] == HEADLINE_HOLD]
    med_n = int(np.median(sizes)) if sizes else 0
    print("── 事实：自动池（每批中位 %d 只，次日开盘买，持有 %d 个交易日）"
          % (med_n, HEADLINE_HOLD))
    for k, lab in (("b_size", "同市值档"), ("b_margin", "两融池"), ("b_all", "全市场等权"),
                   ("b_med", "全市场中位")):
        s = auto_stats[k]
        if s["mean"] is None:
            continue
        ci = s["ci"]
        print("   对%s: n=%d 毛超额 %+.3f%%  区间 [%s, %s]  正号 %d/%d"
              % (lab, s["n"], s["mean"] * 100,
                 ("%+.3f" % (ci[0] * 100)) if ci[0] is not None else "-",
                 ("%+.3f" % (ci[1] * 100)) if ci[1] is not None else "-",
                 s["pos"], s["n"]))
    s = auto_stats["b_size"]
    if s["mean"] is not None:
        print("   扣 %.1f%% 双边成本后: %+.3f%%" % (COST_ROUND * 100,
                                              (s["mean"] - COST_ROUND) * 100))

    # 稳健性：换持有窗口，结论会不会变（这是第二重印证）
    windows = []
    print()
    print("── 换持有窗口（稳健性，不是换口径找好看的数字）")
    for h in HOLDS:
        ss = summarize(obs, "auto", h, "b_size")
        sa = summarize(obs, "auto", h, "b_all")
        windows.append({"hold": h, "n": ss["n"], "size": ss["mean"],
                        "all": sa["mean"], "sd": ss["sd"]})
        if ss["mean"] is None:
            continue
        print("   持有 %d 日: n=%d 对同市值 %+.3f%%  对全市场 %+.3f%%"
              % (h, ss["n"], ss["mean"] * 100, (sa["mean"] or 0) * 100))

    print()
    print("── 主选池（1~7 只，另用一把尺子）")
    for k in ("b_size", "b_margin"):
        s = main_stats[k]
        if s["mean"] is None:
            continue
        print("   对%s: n=%d 毛超额 %+.3f%%  标准差 %s"
              % (k, s["n"], s["mean"] * 100,
                 ("%.3fpp" % (s["sd"] * 100)) if s["sd"] else "-"))

    dims = market_dimensions(asof)
    print()
    print("── 全维度状态")
    for d in dims:
        tag = {"fresh": "可用", "stale": "过期", "missing": "缺失",
               "error": "异常", "lag": "滞后", "unknown": "未知"}.get(d["status"], d["status"])
        print("   %-10s [%s] %s" % (d["name"], tag, d.get("note", "")))

    hyps, combo = build_hypotheses(auto_stats, main_stats, obs, asof)
    print()
    print("── 假设判定")
    for h in hyps:
        print("   %-3s [%-9s] %s" % (h["id"], h["status"], h["verdict"]))
        print("        %s" % h["claim"])
    print()
    print("存活:", ",".join(combo["alive"]) or "无", " 已被否:", ",".join(combo["dead"]) or "无")
    print("结论:", combo["headline"])

    # 证据矩阵：假设 × 维度
    # 每个格子都要说出「这条维度对这条解释算什么证据」，不许留空。
    # 规则先写死在这里，不随当天数字临时调整。
    def cell_rule(hid, k, d):
        # 数据不可用的一律打过期，不装作没异常
        if d.get("status") in ("stale", "missing", "error"):
            return "stale", "数据不可用（%s）" % d.get("status")
        v = d.get("value")

        if k == "index":
            idx = v or {}
            hs = idx.get("沪深300")
            zz = idx.get("中证2000")
            mx_ = max([abs(x) for x in idx.values()]) if idx else None
            if hid == "H1" and hs is not None and zz is not None:
                return (("against", "中证2000 比沪深300 强，小盘是顺风")
                        if zz > hs else ("support", "中证2000 弱于沪深300，小盘逆风"))
            if hid == "H4" and mx_ is not None:
                return (("support", "单日波动 %.1f%%，几天的超额读不出方向" % (mx_ * 100))
                        if mx_ > 0.01 else ("neutral", "单日波动不足 1%"))
            if hid in ("H2", "H3", "H5", "H6"):
                return "na", "指数涨跌不构成这条解释的证据"
            return "na", "—"

        if k == "breadth":
            ur = d.get("up_ratio")
            sp = d.get("size_spread")
            if hid == "H4" and ur is not None:
                return (("support", "涨跌接近对半，方向本身就不明")
                        if 0.35 <= ur <= 0.65 else
                        ("neutral", "上涨占比 %.0f%%，当天有方向" % (ur * 100)))
            if hid == "H6" and ur is not None:
                return (("support", "当日 %.0f%% 上涨，次日回吐会吃掉隔夜收益" % (ur * 100))
                        if ur > 0.65 else
                        ("against" if ur < 0.35 else "neutral",
                         "上涨占比 %.0f%%，没有明显普涨" % (ur * 100)))
            if hid == "H1" and sp is not None:
                return (("against", "宽度看小盘还强于大盘 %.2fpp" % (sp * 100))
                        if sp > 0 else ("support", "宽度看小盘弱于大盘"))
            return "na", "宽度不直接支撑这条解释"

        if k == "style":
            q = v or {}
            q1, q5 = q.get("Q1"), q.get("Q5")
            rng = (q1 - q5) if (q1 is not None and q5 is not None) else None
            if hid == "H1" and rng is not None:
                return (("against", "小盘强于大盘 %.2fpp，逆风不成立" % (rng * 100))
                        if rng > 0.005 else
                        ("support", "小盘弱于大盘 %.2fpp，符合逆风" % (rng * 100))
                        if rng < -0.005 else ("neutral", "风格差不到 0.5pp"))
            if hid == "H5" and rng is not None:
                return (("support", "五分位极差 %.2fpp，选哪档当基准影响很大" % (rng * 100))
                        if abs(rng) > 0.01 else
                        ("against", "五分位极差只有 %.2fpp，换基准改不了结论" % (rng * 100)))
            if hid == "H2" and rng is not None:
                return (("support", "风格差 %.2fpp，市值中性化后 alpha 可能就没了" % (rng * 100))
                        if abs(rng) > 0.005 else ("neutral", "风格差很小"))
            if hid == "H6" and rng is not None:
                return (("neutral", "风格差 %.2fpp，与持有期衰减不直接相关" % (rng * 100)))
            return "na", "风格不构成这条的解释"

        if k == "margin":
            chg = v
            if chg is None:
                return "na", "无环比数据"
            # 阈值下限：±0.3% 以内属噪声，不能当杠杆增减的证据
            if abs(chg) <= 0.003:
                return "neutral", "融资余额环比 %+.2f%%，基本平，读不出方向" % (chg * 100)
            if hid == "H4":
                return (("support", "杠杆在撤 %.2f%%，风险偏好走弱，符合噪声市" % (chg * 100))
                        if chg < 0 else ("against", "杠杆在加 %.2f%%，风险偏好回升" % (chg * 100)))
            if hid == "H1":
                return (("support", "杠杆在撤 %.2f%%，弱市特征" % (chg * 100)) if chg < 0
                        else ("against", "杠杆在加 %.2f%%，不是弱市" % (chg * 100)))
            return "na", "杠杆不直接支撑这条解释"

        if k == "moneyflow":
            nlg = v
            if nlg is None:
                return "na", "无资金流数据"
            if hid == "H6":
                return (("support", "大单净流入 %.0f 亿，当日被资金推高，次日容易回吐" % nlg)
                        if nlg > 30 else
                        ("neutral", "大单净流入 %.0f 亿，推动力一般" % nlg))
            return "na", "资金流不直接支撑这条解释"

        if hid == "H6":
            return "na", "由「持有窗口」那张表判，不看市场维度"
        return "na", "—"

    matrix = []
    for h in hyps:
        row = {"id": h["id"], "cells": {}}
        for d in dims:
            mark, txt = cell_rule(h["id"], d.get("key"), d)
            row["cells"][d.get("key")] = {"mark": mark, "text": txt}
        matrix.append(row)

    # 过程留痕（只在「判定结论」变化时追加，不是每次运行都记）
    # 理由：每天跑一条、内容全同的记录会把证据链淹掉（一年 250 条重复）。
    # 记的是状态变化，才是证据；每次运行都记，那是心跳。
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sig = json.dumps({h["id"]: h["status"] for h in sorted(hyps, key=lambda x: x["id"])},
                     sort_keys=True, ensure_ascii=False) + "@n=%d" % auto_stats["b_size"]["n"]
    gp = os.path.join(LENS, "grade_log.jsonl")
    last_sig = None
    if os.path.exists(gp):
        for line in io.open(gp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                last_sig = json.loads(line).get("sig")
            except Exception:
                pass
    rec = {"graded_at": stamp, "asof": asof,
           "n_obs": auto_stats["b_size"]["n"],
           "mean_excess_size": auto_stats["b_size"]["mean"],
           "verdicts": {h["id"]: h["status"] for h in hyps},
           "sig": sig}
    appended = False
    if sig != last_sig:
        with io.open(gp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        appended = True
        print("\n判定结论变化 → 留痕追加一条")
    else:
        print("\n判定结论与上次一致 → 不重复留痕（避免心跳式冗余）")

    timeline = []
    if os.path.exists(gp):
        for line in io.open(gp, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                timeline.append(json.loads(line))
            except Exception:
                pass

    body = {
        "generated_at": stamp, "asof": asof, "cost_round": COST_ROUND,
        "min_obs": MIN_OBS, "headline_hold": HEADLINE_HOLD,
        "n_obs": auto_stats["b_size"]["n"], "med_n": med_n,
        "need_obs": need_obs(auto_stats["b_size"]["sd"], COST_ROUND),
        "windows": windows,
        "auto": auto_stats, "main": main_stats, "main_note": MAIN_POOL_NOTE,
        "dimensions": dims, "hypotheses": hyps, "combo": combo,
        "matrix": matrix, "timeline": timeline[-40:],
        "obs_detail": [{k: v for k, v in o.items() if k != "quint"}
                       for o in obs if o["pool"] == "auto" and o["hold"] == HEADLINE_HOLD],
    }
    with io.open(os.path.join(LENS, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)

    print()
    print("写入: lens/latest.json  (观测 %d, 假设 %d)" % (len(obs), len(hyps)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(2)
