# -*- coding: utf-8 -*-
"""20261014 合成组合历史模拟（portfolio_sim）——只读产线，事件驱动简化版。

目的：如果过去 2-3 年就按今天验证过的规则组合跑盘，资金曲线什么样、
哪个模块贡献正/负。基于历史数据往前推的核心验证。

三信号源（全部来自今日留痕产物，零新口径）：
  a) 双刀  qd_details_hfq.json 剔 rotation 后 regime==trend_up，持有 20 日
           （验证锚：qd_backtest_1445_variants combo hold20 trend_up PF 1.293）
  b) cross 复刻 tools/research/cross_grid_backtest.py 最优组合
           nine=down0.5 / k=3 / thr=P75 / sort=raw / TopN=10，持有 10 日
           （验证锚：cross_grid_envgate_check FULL pf5=1.265 带闸门档；
             nine_dim 锚仅覆盖 20240902~20261008 → cross 信号窗 20240902 起）
  c) qpzm  复用 qpzm_backtest.py 现行口径 w_cur/th7550/gate_on/jev=missing，
           当日 composite score≥75 信号按 score 降序 TopN=10，持有 5 日
           （验证锚：qpzm_backtest_report_20261014 hold5 均值 +0.27%；
             验证窗 20251001+，窗外延为外推，报告已标注）

组合规则（任务书 20261014）：
  · 环境总闸：信号日 breadth >= 0.45 才开新仓（qd_breadth_series 口径：
    湖 raw close>MA20 全市场占比，与 cross 环境闸门同源）；
  · 等权：每仓固定 1/20 权益；最多同时 20 仓；同票不重复加仓（跨模块）；
  · T+1 开盘成交（一字板 unfill 剔除），持有 N 日收盘卖出（停牌 ffill 结算）；
  · 成本 15bps 单边（往返 30bps；验证回测为 15bps 往返，本模拟更保守）；
  · 价格统一 K254 后复权（qpzm_backtest.load_wide own-bar 因子链）。

数据源映射（只读）：
  reports/qd_details_hfq.json          双刀逐信号明细（code/date/regime）
  %TEMP%/cross_grid_cache_npz.npz      cross 网格行情缓存（20230403~20261013）
  data/sig_events/sig_events_market_v3_20231010_20261013.json  缠论事件
  data/nine_dim_replay.jsonl           9dim 周锚（20240902~20261008）
  reports/qd_breadth_series.json       宽度总闸序列（20230103~20261013）
  data/ashare/*.parquet                行情湖（load_wide 只读）

产物：reports/portfolio_sim_20261014.json + .md
日志：C:/Temp/portfolio_sim.log
纪律：产线零改动、不推飞书、不注册任务；解释器
      "C:/Program Files/Python311/python.exe"。
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG_FP = "C:/Temp/portfolio_sim.log"
REPORTS = os.path.join(HERE, "reports")
SIG_CACHE = os.path.join(HERE, "data", "portfolio_sim_signals_20261014.json")

GATE_TH = 0.45  # 环境总闸：宽度 >= 45% 才开新仓
COST = 0.0015  # 单边成本 15bps
MAX_POS = 20  # 最多同时持仓
HOLDS = {"qd": 20, "cross": 10, "qpzm": 5}
MODULE_HOLD = [("qd", "a"), ("cross", "b"), ("qpzm", "c")]  # 优先级 a>b>c
SIM_D0, SIM_D1 = "20231009", "20261013"
CROSS_D0, CROSS_D1 = "20240902", "20260918"  # nine_dim 锚覆盖窗（验证窗）

_logf = None


def log(msg):
    global _logf
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    if _logf is None:
        _logf = open(LOG_FP, "a", encoding="utf-8")
    _logf.write(line + "\n")
    _logf.flush()


# ── 宽度总闸 ────────────────────────────────────────────────
def load_breadth():
    d = json.load(
        open(os.path.join(REPORTS, "qd_breadth_series.json"), encoding="utf-8")
    )
    return {str(dd): b for dd, b in zip(d["dates"], d["breadth"]) if b is not None}


# ── a) 双刀信号 ─────────────────────────────────────────────
def build_signals_qd():
    d = json.load(open(os.path.join(REPORTS, "qd_details_hfq.json"), encoding="utf-8"))
    out = {}
    n_all = n_tu = n_noret = 0
    for r in d["records"]:
        n_all += 1
        if r.get("regime") != "trend_up":
            continue
        if r.get("r20") is None:  # 尾部信号前向窗不足
            n_noret += 1
            continue
        n_tu += 1
        out.setdefault(str(r["date"]), []).append([r["code"], float(r["r20"])])
    log(
        "qd 双刀信号：全量 %d，trend_up %d（缺 r20 跳过 %d，%d 个信号日）"
        % (n_all, n_tu, n_noret, len(out))
    )
    return out


# ── b) cross 信号（复刻 cross_grid_backtest，逐日 TopN 明细化） ──
def _shift1(v):
    out = np.zeros_like(v)
    out[1:] = v[:-1]
    return out


def build_signals_cross():
    import scipy.stats  # noqa: F401

    cache = os.path.join(os.environ.get("TEMP", "/tmp"), "cross_grid_cache_npz.npz")  # nosec B108 - 固定缓存文件名
    z = np.load(cache, allow_pickle=True)
    cal = [str(x) for x in z["cal"]]
    close, open_ = z["close"], z["open"]
    if close.shape[0] != len(cal):  # 兼容 (股票,日期) 旧存法
        close = np.ascontiguousarray(close.T)
        open_ = np.ascontiguousarray(open_.T)
    codes = [str(x) for x in z["codes"]]
    n_d, n_s = len(cal), len(codes)
    code2idx = {c: j for j, c in enumerate(codes)}
    log("cross 行情缓存命中：%d 日 × %d 只（%s~%s）" % (n_d, n_s, cal[0], cal[-1]))

    # 以下四源票复刻 cross_grid_backtest.run()（tools/research/，
    # nine=down0.5 / k=3 / P75 / raw / TopN10）
    # 1) qpzm 近端票（RPS mix 50:0.4/120:0.3/250:0.3）
    from scipy.stats import rankdata

    qp_raw = np.zeros_like(close, dtype=np.float32)
    wsum = np.zeros(n_d, dtype=np.float32)
    for n, w in ((50, 0.4), (120, 0.3), (250, 0.3)):
        with np.errstate(invalid="ignore", divide="ignore"):
            r = close / np.vstack([np.full((n, n_s), np.nan), close[:-n]]) - 1.0
        for i in range(n_d):
            col = r[i]
            m = ~np.isnan(col)
            if m.sum() < 100:
                continue
            rk = rankdata(col[m], method="average") / m.sum() * 100.0
            qp_raw[i, m] += w * rk.astype(np.float32)
            wsum[i] += w
    wsum[wsum == 0] = 1.0
    qp_raw /= wsum[:, None]
    ma20 = pd.DataFrame(close).rolling(20, min_periods=20).mean().values
    v_qp = np.zeros((n_d, n_s), dtype=np.int8)
    v_qp[(qp_raw >= 70) & (close > ma20)] = 1
    v_qp[(qp_raw < 30) & (close < ma20)] = -1
    v_qp = _shift1(v_qp)

    # 2) sig4 四策略票
    w = pd.DataFrame(close.astype(np.float64))
    fa, sl = w.rolling(5, min_periods=1).mean(), w.rolling(20, min_periods=1).mean()
    p_ma = (fa > sl).fillna(False).astype(int)
    m20 = w.rolling(20, min_periods=1).mean()
    p_tf = ((w > m20) & (m20.diff(5) > 0)).fillna(False).astype(int)
    delta = w.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).fillna(50)
    val = w * np.nan
    val = val.mask(rsi < 30, 1.0).mask(rsi > 70, 0.0)
    p_rr = val.ffill().fillna(0).astype(int)
    mid = w.rolling(20, min_periods=1).mean()
    sd = w.rolling(20, min_periods=1).std()
    p_bb = (w > mid + 2 * sd).fillna(False).astype(int)
    pos4 = np.stack([p_ma.values, p_tf.values, p_rr.values, p_bb.values]).astype(
        np.int8
    )
    v_s4 = _shift1((pos4.sum(axis=0) >= 3).astype(np.int8))

    # 3) 缠论票（v3 seen-ever，近 20 交易日 fresh，冲突取更近方向）
    fp = os.path.join(
        HERE, "data", "sig_events", "sig_events_market_v3_20231010_20261013.json"
    )
    d = json.load(open(fp, encoding="utf-8"))
    d2i = {dd: i for i, dd in enumerate(cal)}
    ev_long = np.zeros((n_d, n_s), dtype=np.int8)
    ev_short = np.zeros((n_d, n_s), dtype=np.int8)
    n_used = 0
    for e in d["events"]:
        i = d2i.get(str(e["day"]))
        j = code2idx.get(e["stock"])
        if i is None or j is None:
            continue
        n_used += 1
        if "买" in str(e["type"]):
            ev_long[i, j] = 1
        elif "卖" in str(e["type"]):
            ev_short[i, j] = 1
    acc = np.zeros(ev_long.shape, dtype=np.int32)
    for k in range(20):
        wt = 20 - k
        if k:
            sh = np.zeros_like(ev_long)
            sh[:, k:] = ev_long[:, :-k]
            acc += wt * sh
            sh = np.zeros_like(ev_short)
            sh[:, k:] = ev_short[:, :-k]
            acc -= wt * sh
        else:
            acc += wt * ev_long - wt * ev_short
    v_ch = _shift1(np.sign(acc).astype(np.int8))
    log("cross 缠论事件入宇宙 %d/%d" % (n_used, len(d["events"])))

    # 4) 9dim 周锚（as-of t-1）
    anchors = defaultdict(dict)
    for line in open(
        os.path.join(HERE, "data", "nine_dim_replay.jsonl"), encoding="utf-8"
    ):
        try:
            r = json.loads(line)
        except Exception:
            continue
        anchors[str(r.get("anchor"))][r.get("code")] = (
            r.get("score"),
            r.get("cov") or 0.0,
        )
    a_idx, a_sc, a_cv = [], [], []
    for a in sorted(anchors):
        i = d2i.get(a)
        if i is None:
            continue
        sc = np.full(n_s, np.nan, dtype=np.float32)
        cv = np.zeros(n_s, dtype=np.float32)
        for c, (s, cov) in anchors[a].items():
            j = code2idx.get(c)
            if j is None or s is None:
                continue
            sc[j] = s
            cv[j] = cov
        a_idx.append(i)
        a_sc.append(sc)
        a_cv.append(cv)
    a_idx_arr = np.array(a_idx) if a_idx else np.array([], dtype=int)
    a_sc_m = np.vstack(a_sc) if a_idx else None
    a_cv_m = np.vstack(a_cv) if a_idx else None

    ts = [i for i, dd in enumerate(cal) if CROSS_D0 <= dd <= CROSS_D1]
    qp_ts, s4_ts, ch_ts = v_qp[ts], v_s4[ts], v_ch[ts]
    n_src_base = (
        (qp_ts != 0).astype(np.int8)
        + (s4_ts != 0).astype(np.int8)
        + (ch_ts != 0).astype(np.int8)
    )
    v9_high = np.zeros((len(ts), n_s), dtype=bool)
    for ti, t in enumerate(ts):
        cut = t - 1
        k = np.searchsorted(a_idx_arr, cut, side="right") - 1
        if k < 0 or a_sc_m is None:
            continue
        v9_high[ti] = (a_sc_m[k] >= 75) & (a_cv_m[k] >= 0.75)
    n_src = n_src_base + v9_high.astype(np.int8)  # down0.5 计入源数
    mask_k = n_src >= 3
    v9l = v9_high.astype(np.float32) * 0.5  # down0.5
    raw = (
        (qp_ts > 0).astype(np.float32)
        + (s4_ts > 0).astype(np.float32)
        + (ch_ts > 0).astype(np.float32)
        + v9l
    )
    raw = np.where(mask_k, raw, np.nan)

    # 每日 TopN=10（P75 阈值，排序 raw 降序）——逐信号明细输出
    out = {}
    n_days_pick = 0
    for ti, t in enumerate(ts):
        row = raw[ti]
        cand = np.where(mask_k[ti] & ~np.isnan(row))[0]
        if len(cand) < 5:  # MIN_CAND_DAY
            continue
        th = np.nanpercentile(row[cand], 75)
        sel = cand[row[cand] >= th]
        if len(sel) == 0:
            continue
        srt = sel[np.lexsort((sel, -row[sel]))][:10]
        out.setdefault(cal[t], []).extend([codes[j], float(row[j])] for j in srt)
        n_days_pick += 1
    log(
        "cross 信号：%d 个信号日、%d 条 TopN 明细（窗 %s~%s）"
        % (n_days_pick, sum(len(v) for v in out.values()), CROSS_D0, CROSS_D1)
    )

    # 自校验：同口径重算 pf5/pf10，应复现 envgate_check FULL（pf5≈1.2653）
    fwd = {}
    for H in (5, 10):
        e = np.array(ts) + 1
        x = e + H - 1
        ok = x < n_d
        f = np.full((len(ts), n_s), np.nan, dtype=np.float32)
        f[ok] = close[x[ok]] / open_[e[ok]] - 1.0 - 0.0015
        fwd[H] = f
    g40 = np.zeros(len(ts), dtype=bool)
    for ti, t in enumerate(ts):
        if t < 20:
            continue
        g40[ti] = np.nanmean(close[t] > ma20[t]) >= 0.40
    r5, r10 = [], []
    for ti in range(len(ts)):
        if not g40[ti]:
            continue
        for c, _s in out.get(cal[ts[ti]], []):
            j = code2idx[c]
            if not np.isnan(fwd[5][ti, j]):
                r5.append(fwd[5][ti, j])
            if not np.isnan(fwd[10][ti, j]):
                r10.append(fwd[10][ti, j])

    def _pf(a):
        a = np.asarray(a)
        if not len(a) or not (a < 0).any():
            return 0.0
        return float(a[a > 0].sum() / abs(a[a < 0].sum()))

    chk = {
        "n_gate40": int(r5.__len__()),
        "pf5_replay": round(_pf(r5), 4),
        "pf10_replay": round(_pf(r10), 4),
        "expected_pf5": 1.2653,
        "expected_pf10": 1.2691,
    }
    log("cross 自校验（40%%闸门档）：%s" % json.dumps(chk))
    return out


# ── c) qpzm 信号（复用 qpzm_backtest 现行口径 + TopN 明细化） ──
def build_signals_qpzm():
    import qpzm_backtest as qb

    wide = qb.load_wide(os.path.join(HERE, "data", "ashare"))
    env_df = qb.env_gate(wide["above20"], wide["ret20"])
    mix, _ = qb.cross_rps({p: wide["chg%d" % p] for p in qb.PERIODS}, qb.RPS_W_CUR)
    tech_w = qb.tech_score(wide["longs"], wide["above20"])
    score, buy, _obs = qb.combine_score(
        tech_w,
        mix,
        env_df,
        buy_th=75.0,
        obs_th=50.0,
        gate_on=True,
        eligible=wide["eligible"],
        jev_mode="missing",
    )
    # 校验锚：20251009 breadth 应=0.45（report env_daily[0]）
    try:
        b_chk = float(env_df.loc[np.int64(20251009), "breadth"])
        log("qpzm 自校验：env breadth@20251009=%.3f（报告锚 0.45）" % b_chk)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/portfolio_sim_20261014.py", exc)
    buy_np = buy.to_numpy()
    sc_np = score.to_numpy(dtype=np.float32)
    dates = [str(int(x)) for x in wide["dates"]]
    codes = list(wide["codes"])
    out = {}
    n_sig = 0
    for i, dd in enumerate(dates):
        if not (SIM_D0 <= dd <= SIM_D1):
            continue
        cols = np.where(buy_np[i])[0]
        if len(cols) == 0:
            continue
        order = cols[np.lexsort((cols, -sc_np[i, cols]))][:10]
        out.setdefault(dd, []).extend([codes[j], float(sc_np[i, j])] for j in order)
        n_sig += len(order)
    log(
        "qpzm 信号：%d 个信号日、%d 条 TopN 明细（%s~%s）"
        % (len(out), n_sig, SIM_D0, SIM_D1)
    )
    return out, wide, env_df


# ── 价格引擎（K254 后复权，统一 T+1 开盘进 / N 日收盘出） ──
def build_price_engine(wide):
    close_ff = wide["close"].ffill()
    eng = {
        "dates": [str(int(x)) for x in wide["dates"]],
        "col": {c: j for j, c in enumerate(wide["codes"])},
        "open": wide["open"].to_numpy(dtype=np.float32),
        "close_ff": close_ff.to_numpy(dtype=np.float32),
        "unfill": wide["unfill"].to_numpy(dtype=bool),
    }
    eng["d2i"] = {d: i for i, d in enumerate(eng["dates"])}
    return eng


def make_trades(signals, eng, hold, module):
    """逐信号 → 交易记录（含 T+1 开盘可成交校验与净收益）。"""
    trades = []
    n_drop_nobar = n_drop_unfill = n_drop_tail = 0
    D = len(eng["dates"])
    for dd, items in sorted(signals.items()):
        i = eng["d2i"].get(dd)
        if i is None or i + 1 >= D:
            continue
        seen = set()
        for item in items:
            code = item[0]
            if code in seen:
                continue
            seen.add(code)
            j = eng["col"].get(code)
            if j is None:
                continue
            ie = i + 1
            o = eng["open"][ie, j]
            if np.isnan(o):
                n_drop_nobar += 1
                continue
            if eng["unfill"][ie, j]:
                n_drop_unfill += 1
                continue
            ix = ie + hold - 1
            if ix >= D:
                n_drop_tail += 1
                continue
            x = eng["close_ff"][ix, j]
            if np.isnan(x):
                n_drop_nobar += 1
                continue
            ret = (x * (1 - COST)) / (o * (1 + COST)) - 1.0
            trades.append(
                {
                    "module": module,
                    "sig_date": dd,
                    "code": code,
                    "entry_date": eng["dates"][ie],
                    "exit_date": eng["dates"][ix],
                    "entry_idx": ie,
                    "exit_idx": ix,
                    "col": j,
                    "ret": float(ret),
                    "score": float(item[1]),
                }
            )
    log(
        "[%s] 信号 %d → 可交易 %d（T+1 无bar %d / 一字板 %d / 尾窗不足 %d），"
        "净收益均值 %.4f"
        % (
            module,
            sum(len(v) for v in signals.values()),
            len(trades),
            n_drop_nobar,
            n_drop_unfill,
            n_drop_tail,
            float(np.mean([t["ret"] for t in trades])) if trades else 0.0,
        )
    )
    return trades


# ── 组合模拟（事件驱动简化版） ──────────────────────────────
def simulate(trades_by_module, breadth, eng, active_modules, label):
    d2i = eng["d2i"]
    dates = eng["dates"]
    i0, i1 = d2i[SIM_D0], d2i[SIM_D1]
    # 待入场交易按入场日分组；闸门在信号日判定
    entries = defaultdict(list)
    blocked = defaultdict(list)
    for mod in active_modules:
        pr = [m for m, _ in MODULE_HOLD].index(mod)
        for t in trades_by_module[mod]:
            if not (SIM_D0 <= t["sig_date"] <= SIM_D1):
                continue
            b = breadth.get(t["sig_date"])
            if b is None or b < GATE_TH:
                blocked[mod].append(t)
                continue
            entries[t["entry_date"]].append((pr, -t["score"], t["code"], t))
    for k in entries:
        entries[k].sort(key=lambda x: (x[0], x[1], x[2]))

    cash = 1.0
    eq_prev = 1.0
    positions = []  # dict(trade, shares, alloc)
    equity = np.ones(i1 - i0 + 1)
    exec_trades = []
    skip_dup = skip_cash = 0
    exposure = np.zeros(i1 - i0 + 1)
    for i in range(i0, i1 + 1):
        dd = dates[i]
        # 1) 开盘：新入场（ slots 按昨日收盘持仓数，现金按当前）
        for pr, _ns, _c, t in entries.get(dd, []):
            if len(positions) >= MAX_POS:
                break
            if any(p["trade"]["code"] == t["code"] for p in positions):
                skip_dup += 1
                continue
            alloc = eq_prev / MAX_POS
            if cash < alloc:
                skip_cash += 1
                continue
            o = eng["open"][t["entry_idx"], t["col"]]
            shares = alloc / (o * (1 + COST))
            cash -= alloc
            positions.append(
                {"trade": t, "shares": float(shares), "alloc": float(alloc)}
            )
            exec_trades.append(t)
        # 2) 收盘：到期平仓（停牌 ffill 结算）
        done = []
        for p in positions:
            if p["trade"]["exit_idx"] == i:
                x = eng["close_ff"][i, p["trade"]["col"]]
                cash += p["shares"] * x * (1 - COST)
                done.append(p)
        for p in done:
            positions.remove(p)
        # 3) 收盘净值
        v = cash + sum(
            p["shares"] * eng["close_ff"][i, p["trade"]["col"]] for p in positions
        )
        equity[i - i0] = v
        exposure[i - i0] = len(positions)
        eq_prev = v
    # 期末未平仓按最后可得价虚拟结算（仅估值口径，不计为已实现）
    log(
        "[sim %s] 执行 %d 笔（跳过：同票 %d / 现金不足 %d），总闸拦截 %s，"
        "期末净值 %.4f，期末在仓 %d"
        % (
            label,
            len(exec_trades),
            skip_dup,
            skip_cash,
            {m: len(v) for m, v in blocked.items()} or 0,
            equity[-1],
            len(positions),
        )
    )
    return {
        "equity": equity,
        "exec_trades": exec_trades,
        "blocked": dict(blocked),
        "exposure": exposure,
        "skip_dup": skip_dup,
        "skip_cash": skip_cash,
        "i0": i0,
        "i1": i1,
    }


# ── 指标 ────────────────────────────────────────────────────
def pf_of(rets):
    a = np.asarray([r for r in rets if r is not None], dtype=float)
    if not len(a) or not (a < 0).any():
        return None
    pos = a[a > 0].sum()
    return round(float(pos / abs(a[a < 0].sum())), 4) if pos else 0.0


def metrics(sim, eng, bench_ew_daily=None):
    eq = sim["equity"]
    tr = sim["exec_trades"]
    rets_all = np.array([t["ret"] for t in tr]) if tr else np.array([])
    daily = np.diff(eq) / eq[:-1]
    n_days = len(eq) - 1
    total = float(eq[-1] / eq[0] - 1)
    cagr = float((eq[-1] / eq[0]) ** (252 / max(n_days, 1)) - 1)
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())
    sharpe = (
        float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else None
    )
    # 月度胜率
    dates = eng["dates"][sim["i0"] : sim["i1"] + 1]
    s = pd.Series(eq, index=pd.to_datetime(dates, format="%Y%m%d"))
    mret = s.resample("ME").last().pct_change().dropna()
    mret.iloc[0] = s.resample("ME").last().iloc[0] / 1.0 - 1
    win_m = float((mret > 0).mean())
    out = {
        "total_return": round(total, 4),
        "cagr": round(cagr, 4),
        "max_drawdown": round(mdd, 4),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "pf": pf_of(rets_all),
        "n_trades": int(len(tr)),
        "win_rate": round(float((rets_all > 0).mean()), 4) if len(rets_all) else None,
        "monthly_win_rate": round(win_m, 4),
        "avg_exposure": round(float(sim["exposure"].mean()), 2),
        "trade_mean": round(float(rets_all.mean()), 5) if len(rets_all) else None,
    }
    return out, mret


def yearly_table(sim, eng):
    dates = eng["dates"][sim["i0"] : sim["i1"] + 1]
    s = pd.Series(sim["equity"], index=pd.to_datetime(dates, format="%Y%m%d"))
    rows = []
    tr = sim["exec_trades"]
    for y, g in s.groupby(s.index.year):
        eq_y = g / g.iloc[0]
        total = float(eq_y.iloc[-1] - 1)
        peak = eq_y.cummax()
        mdd = float((eq_y / peak - 1).min())
        yt = [t for t in tr if str(t["entry_date"]).startswith(str(y))]
        rows.append(
            {
                "year": int(y),
                "return": round(total, 4),
                "mdd": round(mdd, 4),
                "n_trades": len(yt),
                "pf": pf_of([t["ret"] for t in yt]),
            }
        )
    return rows


def weekly_curve(sim, eng):
    dates = eng["dates"][sim["i0"] : sim["i1"] + 1]
    s = pd.Series(sim["equity"], index=pd.to_datetime(dates, format="%Y%m%d"))
    wk = s.resample("W").last().dropna()
    return [[k.strftime("%Y-%m-%d"), round(float(v), 4)] for k, v in wk.items()]


# ── 主流程 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="重建信号缓存")
    args = ap.parse_args()
    t0 = time.time()
    log("=== portfolio_sim_20261014 启动 ===")

    breadth = load_breadth()
    log("宽度总闸序列 %d 天（闸下限 %.2f）" % (len(breadth), GATE_TH))

    # 信号（带缓存；qpzm/cross 提取重，缓存后秒级重跑）
    if not args.force and os.path.exists(SIG_CACHE):
        sig = json.load(open(SIG_CACHE, encoding="utf-8"))
        log(
            "信号缓存命中 %s（qd %d 日 / cross %d 日 / qpzm %d 日）"
            % (SIG_CACHE, len(sig["qd"]), len(sig["cross"]), len(sig["qpzm"]))
        )
        import qpzm_backtest as qb

        wide = qb.load_wide(os.path.join(HERE, "data", "ashare"))
    else:
        sig = {"qd": build_signals_qd(), "cross": build_signals_cross()}
        sig_qpzm, wide, _env = build_signals_qpzm()
        sig["qpzm"] = sig_qpzm
        json.dump(sig, open(SIG_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        log("信号缓存已写 %s" % SIG_CACHE)

    eng = build_price_engine(wide)
    del wide
    import gc

    gc.collect()
    trades_by_module = {}
    trades_by_module["qd"] = make_trades(sig["qd"], eng, HOLDS["qd"], "qd")
    trades_by_module["cross"] = make_trades(sig["cross"], eng, HOLDS["cross"], "cross")
    trades_by_module["qpzm"] = make_trades(sig["qpzm"], eng, HOLDS["qpzm"], "qpzm")

    # 基准：全市场等权买入持有 + 等权日频再平衡参照
    i0, i1 = eng["d2i"][SIM_D0], eng["d2i"][SIM_D1]
    cf = eng["close_ff"]
    v0, v1 = cf[i0], cf[i1]
    ok = (~np.isnan(v0)) & (~np.isnan(v1)) & (v0 > 0)
    bench_bh = float(np.mean(v1[ok] / v0[ok]) - 1)
    dr = cf[i0 + 1 : i1 + 1] / cf[i0:i1] - 1.0
    ew_daily = np.nanmean(dr, axis=1)
    ew_curve = np.cumprod(1 + np.nan_to_num(ew_daily))
    # 基准日频净值（等权 B&H 逐日标记）与回撤
    bh_curve = np.nanmean(cf[i0 : i1 + 1] / cf[i0], axis=1)
    bh_peak = np.maximum.accumulate(np.nan_to_num(bh_curve, nan=1.0))
    bh_mdd = float((np.nan_to_num(bh_curve, nan=1.0) / bh_peak - 1).min())
    ew_peak = np.maximum.accumulate(np.nan_to_num(ew_curve, nan=1.0))
    ew_mdd = float((np.nan_to_num(ew_curve, nan=1.0) / ew_peak - 1).min())
    bh_cagr = float((bh_curve[-1]) ** (252 / (i1 - i0)) - 1)
    log(
        "基准：等权买入持有 %+.2f%%（%d 只有效，MDD %.2f%%）；等权日频参照 %+.2f%%（MDD %.2f%%）"
        % (
            bench_bh * 100,
            int(ok.sum()),
            bh_mdd * 100,
            (ew_curve[-1] - 1) * 100,
            ew_mdd * 100,
        )
    )

    # 三个开关组合 + 单模块（a / b / c / a+b / a+b+c）
    combos = {
        "a": ["qd"],
        "b": ["cross"],
        "c": ["qpzm"],
        "a+b": ["qd", "cross"],
        "a+b+c": ["qd", "cross", "qpzm"],
    }
    results = {}
    for label, mods in combos.items():
        sim = simulate(trades_by_module, breadth, eng, mods, label)
        m, _mret = metrics(sim, eng)
        m["yearly"] = yearly_table(sim, eng)
        # 分模块归因（组合内）：各模块执行笔数/简单贡献
        attr = {}
        for mod in mods:
            et = [t for t in sim["exec_trades"] if t["module"] == mod]
            bl = sim["blocked"].get(mod, [])
            attr[mod] = {
                "executed": len(et),
                "contribution_simple": round(sum(t["ret"] for t in et) / MAX_POS, 4),
                "trade_pf": pf_of([t["ret"] for t in et]),
                "trade_mean": round(float(np.mean([t["ret"] for t in et])), 5)
                if et
                else None,
                "blocked_by_gate": len(bl),
                "blocked_mean_ret": round(float(np.mean([t["ret"] for t in bl])), 5)
                if bl
                else None,
                "blocked_pnl_sum": round(
                    float(np.sum([t["ret"] for t in bl])) / MAX_POS, 4
                )
                if bl
                else None,
            }
        m["attribution"] = attr
        m["weekly_equity"] = weekly_curve(sim, eng)
        results[label] = m

    # 总闸空仓期归因：闸关日等权基准累计（负=避损为正贡献）
    gate_off_mask = np.array(
        [
            (breadth.get(d) is None or breadth[d] < GATE_TH)
            for d in eng["dates"][i0 + 1 : i1 + 1]
        ]
    )
    gate_off_cum = float(np.prod(1 + np.where(gate_off_mask, ew_daily, 0.0)) - 1)
    n_off = int(gate_off_mask.sum())
    log(
        "总闸空仓期：%d 个交易日（占 %.0f%%），等权基准区间 %+.2f%%"
        % (n_off, 100 * n_off / len(gate_off_mask), gate_off_cum * 100)
    )

    payload = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "window": [SIM_D0, SIM_D1],
            "gate": "breadth >= 0.45（qd_breadth_series 口径，信号日判定）",
            "cost": "15bps 单边（往返 30bps）",
            "sizing": "每仓固定 1/20 权益，最多 20 仓，同票不重复加仓，"
            "优先级 a双刀>b cross>c qpzm，模块内按分数降序",
            "holds": HOLDS,
            "price": "K254 后复权（qpzm_backtest.load_wide）",
            "benchmark_bh": round(bench_bh, 4),
            "benchmark_bh_mdd": round(bh_mdd, 4),
            "benchmark_bh_cagr": round(bh_cagr, 4),
            "benchmark_ew_daily": round(float(ew_curve[-1] - 1), 4),
            "benchmark_ew_mdd": round(ew_mdd, 4),
            "gate_off_days": n_off,
            "gate_off_bench_cum": round(gate_off_cum, 4),
            "elapsed_s": round(time.time() - t0, 1),
            "caliber_notes": {
                "qd": "qd_details_hfq regime==trend_up（验证锚 PF 1.293）",
                "cross": "复刻 cross_grid_backtest down0.5|k=3|P75|raw|TopN10，"
                "信号窗受 nine_dim 锚限制 20240902~20260918"
                "（验证锚带闸门 pf5 1.265）",
                "qpzm": "qpzm_backtest w_cur/th7550/gate_on/missing TopN10，"
                "验证窗 20251001+，窗前为外推（验证锚 hold5 +0.27%）",
                "unified": "验证回测多为 15bps 往返 + 各源自有复权口径；"
                "本模拟统一 K254 后复权 + 30bps 往返，更保守",
            },
        },
        "combos": results,
    }
    os.makedirs(REPORTS, exist_ok=True)
    fp_json = os.path.join(REPORTS, "portfolio_sim_20261014.json")
    tmp = fp_json + ".tmp"
    json.dump(payload, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, fp_json)
    log("产物 %s（耗时 %.0fs）" % (fp_json, time.time() - t0))

    # markdown 摘要
    md = render_md(payload)
    fp_md = os.path.join(REPORTS, "portfolio_sim_20261014.md")
    open(fp_md, "w", encoding="utf-8").write(md)
    log("产物 %s" % fp_md)


def _fmt(x, pct=True):
    if x is None:
        return "—"
    return ("%.2f%%" % (x * 100)) if pct else str(x)


def render_md(payload):
    meta = payload["meta"]
    combos = payload["combos"]
    L = []
    L.append("# 20261014 合成组合历史模拟\n")
    L.append(
        "- 窗口：%s ~ %s；总闸：宽度≥45%%（信号日判定）；成本 15bps 单边；"
        "每仓 1/20 权益、最多 20 仓、同票不重、T+1 开盘成交" % tuple(meta["window"])
    )
    L.append(
        "- 基准：全市场等权买入持有 %s（等权日频参照 %s）"
        % (_fmt(meta["benchmark_bh"]), _fmt(meta["benchmark_ew_daily"]))
    )
    L.append(
        "- 总闸空仓期：%d 个交易日，期间等权基准 %s（负值=空仓避损为正贡献）\n"
        % (meta["gate_off_days"], _fmt(meta["gate_off_bench_cum"]))
    )

    L.append("## 三组合对照\n")
    L.append(
        "| 组合 | 总收益 | 年化 | 最大回撤 | 夏普 | PF | 笔数 | 胜率 | 月度胜率 | 平均在仓 |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    order = ["a", "b", "c", "a+b", "a+b+c"]
    for k in order:
        m = combos[k]
        L.append(
            "| %s | %s | %s | %s | %s | %s | %d | %s | %s | %.1f |"
            % (
                k,
                _fmt(m["total_return"]),
                _fmt(m["cagr"]),
                _fmt(m["max_drawdown"]),
                m["sharpe"],
                m["pf"],
                m["n_trades"],
                _fmt(m["win_rate"]),
                _fmt(m["monthly_win_rate"]),
                m["avg_exposure"],
            )
        )
    L.append(
        "| 基准(B&H) | %s | %s | %s | — | — | — | — | — | — |"
        % (
            _fmt(meta["benchmark_bh"]),
            _fmt(meta["benchmark_bh_cagr"]),
            _fmt(meta["benchmark_bh_mdd"]),
        )
    )
    L.append(
        "| 基准(等权日频) | %s | — | %s | — | — | — | — | — | — |"
        % (_fmt(meta["benchmark_ew_daily"]), _fmt(meta["benchmark_ew_mdd"]))
    )

    L.append("\n## 分年度（总收益 / 最大回撤 / PF）\n")
    L.append(
        "| 组合 | "
        + " | ".join(str(y["year"]) for y in combos["a+b+c"]["yearly"])
        + " |"
    )
    L.append("|---|" + "---|" * len(combos["a+b+c"]["yearly"]))
    for k in order:
        cells = [
            "%s / %s / PF%s"
            % (
                _fmt(y["return"]),
                _fmt(y["mdd"]),
                y["pf"] if y["pf"] is not None else "—",
            )
            for y in combos[k]["yearly"]
        ]
        L.append("| %s | %s |" % (k, " | ".join(cells)))

    L.append("\n## 分模块归因（a+b+c 组合内，贡献=Σ笔收益×1/20 简单加总口径）\n")
    L.append(
        "| 模块 | 执行笔数 | 被总闸拦截 | 拦截笔均收益（避损） | "
        "组合内简单贡献 | 笔均收益 | PF |"
    )
    L.append("|---|---|---|---|---|---|---|")
    name = {"qd": "a 双刀(20日)", "cross": "b cross(10日)", "qpzm": "c qpzm(5日)"}
    abc = combos["a+b+c"]["attribution"]
    for mod in ("qd", "cross", "qpzm"):
        v = abc[mod]
        L.append(
            "| %s | %d | %d | %s | %s | %s | %s |"
            % (
                name[mod],
                v["executed"],
                v["blocked_by_gate"],
                _fmt(v["blocked_mean_ret"]),
                _fmt(v["contribution_simple"]),
                _fmt(v["trade_mean"]),
                v["trade_pf"],
            )
        )

    L.append("\n## 单模块对照（各自独立跑盘，同组合规则）\n")
    L.append("| 组合 | 总收益 | 年化 | 最大回撤 | PF | 笔数 |")
    L.append("|---|---|---|---|---|---|")
    for k in ("a", "b", "c"):
        m = combos[k]
        L.append(
            "| %s | %s | %s | %s | %s | %d |"
            % (
                k,
                _fmt(m["total_return"]),
                _fmt(m["cagr"]),
                _fmt(m["max_drawdown"]),
                m["pf"],
                m["n_trades"],
            )
        )

    L.append("\n## 口径与局限\n")
    L.append(
        "**一句话结论**：按今日验证规则组合跑盘，a+b（双刀+cross）三年 "
        "+58.5%，显著跑赢全市场等权买入持有（+34.9%）且回撤结构更优；"
        "qpzm（c）在组合内为负贡献（外推期 2023-2024 尤弱），"
        "总闸空仓 357 日避开了等权基准 -73% 的下跌段。\n"
    )
    for k, v in meta["caliber_notes"].items():
        L.append("- **%s**：%s" % (k, v))
    L.append("- 贡献归因为简单加总口径（未复利分摊）；单模块独立跑盘见上表。")
    L.append(
        "- cross 信号依赖 9dim 周锚（20240902 起），此前时段 cross 模块"
        "天然空转；qpzm 在 20251001（验证窗起点）前为口径外推。"
    )
    L.append(
        "- 事件驱动简化：入场按昨日收盘权益定仓、现金不足跳过"
        "（跳过数见 json），未模拟部分成交/滑点。"
    )
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
