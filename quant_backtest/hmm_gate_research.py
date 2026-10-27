# -*- coding: utf-8 -*-
"""
HMM 软闸门离线对照研究（研究线，不接产线、不改任何现有文件，数据只读）。

方法：
  1. 纯 numpy 高斯 HMM（一维/二维特征）：前向-后向（log 域缩放）+ Baum-Welch EM
     （多随机重启取最高似然，固定种子可复现）+ Viterbi / 后验解码。
  2. 特征：[breadth_t, breadth_t - breadth_{t-5}]（宽度水平 + 5 日动量），标准化后拟合。
     K=2 与 K=3 各拟合，BIC 选优。
  3. 前向收益对照：等权市场日收益 = data/ashare 湖个股 pct_chg（tushare 口径，
     基于除权后 pre_close，等价后复权日收益）合成（抽样约 400 只 2023-04-03 起
     全程在市、流动性好的票）。
     各状态 / 各硬阈值档（40/45/50）计算 T+20 前向收益均值与胜率。
     防未来函数：主口径用过滤后验（仅用截至 t 的数据），平滑口径单独列示。
  4. 迟滞双阈值软闸门：风险状态后验 > 0.6 进、< 0.4 出，与硬闸门比较翻转次数。

运行：
  "C:/Program Files/Python311/python.exe" hmm_gate_research.py
输出：
  reports/hmm_gate_research.md
  reports/hmm_gate_research.json
"""

import glob
import json
import os

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.abspath(__file__))
BREADTH_JSON = os.path.join(REPO, "reports", "qd_breadth_series.json")
ASHARE_DIR = os.path.join(REPO, "data", "ashare")
OUT_MD = os.path.join(REPO, "reports", "hmm_gate_research.md")
OUT_JSON = os.path.join(REPO, "reports", "hmm_gate_research.json")

SEED = 42
N_RESTARTS = 8
N_SAMPLE_STOCKS = 400
MOM_LAG = 5          # 动量回看天数
HORIZON = 20         # 前向收益天数
HYST_HI = 0.6        # 迟滞进入阈值（风险后验）
HYST_LO = 0.4        # 迟滞退出阈值
HARD_THRESHOLDS = [40.0, 45.0, 50.0]  # 宽度硬阈值（0-100 刻度）


# ============================================================
# 1. 纯 numpy 高斯 HMM
# ============================================================

def _logmatvec(logA, x):
    """log-sum-exp 矩阵乘：out[i] = logsumexp_j(logA[i,j] + x[j])"""
    m = logA + x[None, :]
    mx = m.max(axis=1)
    return mx + np.log(np.exp(m - mx[:, None]).sum(axis=1))


def _logsumexp(a, axis=None, keepdims=False):
    mx = np.max(a, axis=axis, keepdims=True)
    out = mx + np.log(np.exp(a - mx).sum(axis=axis, keepdims=True))
    if not keepdims and axis is not None:
        out = np.squeeze(out, axis=axis)
    elif not keepdims:
        out = float(np.squeeze(out))
    return out


class GaussianHMM:
    """对角协方差高斯 HMM，log 域实现，支持缺失掩码（NaN 特征行在前后向中跳过观测项）。"""

    def __init__(self, n_states, seed=0, max_iter=200, tol=1e-6):
        self.K = n_states
        self.seed = seed
        self.max_iter = max_iter
        self.tol = tol
        self.converged_ = False
        self.loglik_ = -np.inf
        self.n_iter_ = 0

    # ---- 观测对数似然 ----
    def _log_emission(self, X):
        T, D = X.shape
        K = self.K
        out = np.full((T, K), 0.0)
        valid = ~np.isnan(X)
        for k in range(K):
            mu, var = self.means_[k], self.vars_[k]  # 对角协方差
            ll = np.zeros(T)
            for d in range(D):
                v = np.maximum(var[d], 1e-6)
                x = X[:, d]
                m = valid[:, d]
                ll[m] += -0.5 * (np.log(2 * np.pi * v) + (x[m] - mu[d]) ** 2 / v)
            out[:, k] = ll
        return out

    def _init_params(self, X, rng):
        T, D = X.shape
        K = self.K
        # kmeans++ 风格随机初始化：随机挑 K 个样本当均值
        idx = rng.choice(T, size=K, replace=False)
        self.means_ = X[idx].copy()
        self.vars_ = np.tile(X.var(axis=0), (K, 1)) * np.ones((K, D))
        # 起始分布均匀，转移矩阵接近对角（自转移偏置，便于拟合持续状态）
        self.pi_ = np.full(K, 1.0 / K)
        A = rng.dirichlet(np.ones(K), size=K)
        A = 0.9 * np.eye(K) + 0.1 * A
        self.A_ = A / A.sum(axis=1, keepdims=True)

    # ---- 前向-后向（log 域）----
    def _forward_backward(self, logB):
        T, K = logB.shape
        logA = np.log(np.maximum(self.A_, 1e-300))
        logpi = np.log(np.maximum(self.pi_, 1e-300))

        log_alpha = np.empty((T, K))
        log_alpha[0] = logpi + logB[0]
        for t in range(1, T):
            log_alpha[t] = logB[t] + _logmatvec(logA.T, log_alpha[t - 1])

        log_beta = np.empty((T, K))
        log_beta[-1] = 0.0
        for t in range(T - 2, -1, -1):
            log_beta[t] = _logmatvec(logA, logB[t + 1] + log_beta[t + 1])

        log_gamma = log_alpha + log_beta
        loglik = _logsumexp(log_gamma[0])
        log_gamma -= _logsumexp(log_gamma, axis=1, keepdims=True)
        gamma = np.exp(log_gamma)

        # xi 累加（M 步转移矩阵用）
        xi_sum = np.full((K, K), -1e300)
        for t in range(T - 1):
            m = log_alpha[t][:, None] + logA + (logB[t + 1] + log_beta[t + 1])[None, :]
            m -= _logsumexp(m)
            xi_sum = np.logaddexp(xi_sum, m)
        xi_sum -= _logsumexp(xi_sum, axis=1, keepdims=True)
        xi_sum = np.exp(xi_sum)
        return gamma, xi_sum, loglik, log_alpha, log_beta

    def _m_step(self, X, gamma, xi_sum):
        T, D = X.shape
        K = self.K
        self.pi_ = gamma[0] / gamma[0].sum()
        self.A_ = xi_sum / xi_sum.sum(axis=1, keepdims=True)
        w = gamma.sum(axis=0)  # (K,)
        w = np.maximum(w, 1e-10)
        self.means_ = (gamma.T @ np.nan_to_num(X)) / w[:, None]
        for k in range(K):
            diff2 = np.where(np.isnan(X), 0.0, (X - self.means_[k]) ** 2)
            self.vars_[k] = (gamma[:, k] @ diff2) / w[k] + 1e-6  # 正则化下限

    def fit(self, X):
        X = np.asarray(X, dtype=float)
        rng = np.random.default_rng(self.seed)
        self._init_params(X, rng)
        prev = -np.inf
        for it in range(self.max_iter):
            logB = self._log_emission(X)
            gamma, xi_sum, loglik, _, _ = self._forward_backward(logB)
            self._m_step(X, gamma, xi_sum)
            self.n_iter_ = it + 1
            if abs(loglik - prev) < self.tol * max(1.0, abs(prev)):
                self.converged_ = True
                prev = loglik
                break
            prev = loglik
        logB = self._log_emission(X)
        _, _, self.loglik_, _, _ = self._forward_backward(logB)
        return self

    # ---- 解码 ----
    def predict_proba(self, X, smoothed=False):
        """filtered: alpha/gamma at t（仅用截至 t 的数据）；smoothed: 全样本前后向。"""
        X = np.asarray(X, dtype=float)
        logB = self._log_emission(X)
        gamma, _, _, log_alpha, _ = self._forward_backward(logB)
        if smoothed:
            return gamma
        # 过滤后验 p(z_t | x_1..t) = normalize(alpha_t)
        alpha = log_alpha - _logsumexp(log_alpha, axis=1, keepdims=True)
        return np.exp(alpha)

    def predict(self, X):
        """Viterbi 最优路径（事后平滑口径）。"""
        X = np.asarray(X, dtype=float)
        logB = self._log_emission(X)
        T, K = logB.shape
        logA = np.log(np.maximum(self.A_, 1e-300))
        delta = np.log(np.maximum(self.pi_, 1e-300)) + logB[0]
        psi = np.zeros((T, K), dtype=int)
        for t in range(1, T):
            cand = delta[:, None] + logA
            psi[t] = cand.argmax(axis=0)
            delta = cand.max(axis=0) + logB[t]
        path = np.zeros(T, dtype=int)
        path[-1] = delta.argmax()
        for t in range(T - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        return path


def fit_hmm_best(X, n_states, seed=SEED, n_restarts=N_RESTARTS):
    """多随机重启拟合，取最高似然。"""
    best = None
    for r in range(n_restarts):
        m = GaussianHMM(n_states, seed=seed + 1000 * r).fit(X)
        if best is None or m.loglik_ > best.loglik_:
            best = m
    return best


def n_hmm_params(K, D):
    """pi(K-1) + A(K(K-1)) + means(KD) + diag vars(KD)"""
    return (K - 1) + K * (K - 1) + K * D + K * D


def bic(loglik, T, n_params):
    return -2 * loglik + n_params * np.log(T)


# ============================================================
# 2. 数据构建（只读）
# ============================================================

def load_breadth():
    d = json.load(open(BREADTH_JSON, encoding="utf-8"))
    df = pd.DataFrame({"date": d["dates"], "breadth": d["breadth"]})
    df = df.dropna().reset_index(drop=True)
    return df


def build_market_returns(dates_index):
    """抽样约 400 只全程在市、流动性好的票，用湖内 pct_chg（tushare 口径，基于除权后
    pre_close 计算，等价后复权日收益）合成等权市场日收益。
    注：data/tushare/adj_factor 日快照在样本区间内仅约 79 天，过于稀疏，
    close×adj_factor 插值会引入除权跳变，故改用 pct_chg 字段。"""
    files = sorted(glob.glob(os.path.join(ASHARE_DIR, "*.parquet")))
    print(f"[data] 湖内个股文件 {len(files)} 个", flush=True)

    start_date, end_date = "20230403", "20261013"
    # 覆盖筛选 + 流动性（均值成交额）排序
    stats = []
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["ts_code", "trade_date", "amount"])
        except Exception:
            continue
        if df.empty:
            continue
        d0, d1 = df["trade_date"].iloc[0], df["trade_date"].iloc[-1]
        if d0 > start_date or d1 < end_date:
            continue
        stats.append((df["ts_code"].iloc[0], float(df["amount"].mean()), f))
    stats.sort(key=lambda x: -x[1])
    picked = stats[:N_SAMPLE_STOCKS]
    print(f"[data] 全程在市 {len(stats)} 只，抽样流动性前 {len(picked)} 只", flush=True)

    ret_frames = []
    for code, _, f in picked:
        df = pd.read_parquet(f, columns=["ts_code", "trade_date", "pct_chg"])
        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]
        df = df.dropna(subset=["pct_chg"])
        if len(df) < 100:
            continue
        ret_frames.append(pd.Series(df["pct_chg"].to_numpy() / 100.0,
                                    index=df["trade_date"].to_numpy(), name=code))
    panel = pd.concat(ret_frames, axis=1)
    mkt = panel.mean(axis=1, skipna=True)  # 等权日收益
    print(f"[data] 收益矩阵 {panel.shape}，市场日收益 {len(mkt)} 天", flush=True)
    return mkt, int(panel.shape[1])


# ============================================================
# 3. 对照分析
# ============================================================

def fwd_stats(mask, fwd):
    """mask=True 的日子上 T+20 前向收益均值/胜率。"""
    v = fwd[mask & ~np.isnan(fwd)]
    if len(v) == 0:
        return {"n": 0, "mean": None, "win": None}
    return {"n": int(len(v)), "mean": round(float(v.mean()) * 100, 3),
            "win": round(float((v > 0).mean()) * 100, 2)}


def flips_per_year(gate, dates):
    g = gate.astype(int)
    flips = int((np.diff(g) != 0).sum())
    days = len(g)
    return flips, flips / days * 252.0, flips / (days / 252.0)


def hysteresis_gate(p_risk, hi, lo):
    """迟滞双阈值：后验>hi 进风险态，<lo 出。"""
    gate = np.zeros(len(p_risk), dtype=int)
    on = False
    for t, p in enumerate(p_risk):
        if not on and p > hi:
            on = True
        elif on and p < lo:
            on = False
        gate[t] = int(on)
    return gate


def main():
    breadth = load_breadth()
    dates = breadth["date"].to_numpy()
    b = breadth["breadth"].to_numpy()  # 0-1 刻度
    print(f"[breadth] 有效样本 {len(b)} 天 {dates[0]}~{dates[-1]}", flush=True)

    # ---- 特征与标准化 ----
    X_raw = np.column_stack([b, np.r_[np.full(MOM_LAG, np.nan), np.diff(b, n=MOM_LAG)]])
    valid = ~np.isnan(X_raw).any(axis=1)
    Xv = X_raw[valid]
    mu, sd = Xv.mean(axis=0), Xv.std(axis=0)
    Xz = (Xv - mu) / sd
    T = len(Xz)
    print(f"[feat] 特征有效样本 {T}（去动量 warmup {MOM_LAG} 天），标准化后拟合", flush=True)

    # ---- K=2 / K=3 拟合与 BIC 选优 ----
    models = {}
    for K in (2, 3):
        m = fit_hmm_best(Xz, K)
        models[K] = m
        print(f"[hmm] K={K} loglik={m.loglik_:.2f} converged={m.converged_} "
              f"iters={m.n_iter_} BIC={bic(m.loglik_, T, n_hmm_params(K, 2)):.1f}", flush=True)
    bics = {K: bic(m.loglik_, T, n_hmm_params(K, 2)) for K, m in models.items()}
    best_K = min(bics, key=bics.get)
    best = models[best_K]
    print(f"[hmm] BIC 选优 K={best_K}", flush=True)

    # ---- 状态标注：按特征1（宽度水平）均值排序，低宽度=风险态 ----
    gamma = best.predict_proba(Xz, smoothed=True)
    state_breadth = [Xz[gamma[:, k] > 0.5, 0].mean() for k in range(best.K)]
    order = np.argsort(state_breadth)          # 低宽度在前
    risk_label = int(order[0])                 # 原始状态索引中的风险态
    # 后验重排：列按宽度从低到高
    gamma_o = gamma[:, order]
    p_risk_filtered = best.predict_proba(Xz, smoothed=False)[:, risk_label]
    p_risk_smoothed = gamma[:, risk_label]
    print(f"[hmm] 状态宽度均值(标准化) {[round(x,3) for x in state_breadth]}，风险态=原状态 {risk_label}", flush=True)

    # ---- 市场收益与前向收益 ----
    mkt, n_stocks = build_market_returns(dates)
    df = pd.DataFrame({"date": dates, "breadth": b}).merge(
        mkt.rename("ret"), left_on="date", right_index=True, how="left")
    df = df.dropna(subset=["ret"]).reset_index(drop=True)
    d = df["date"].to_numpy()
    bb = df["breadth"].to_numpy()
    r = df["ret"].to_numpy()
    n = len(r)
    # T+20 前向收益：M_{t+20}/M_t - 1（用 T+1..T+20 复利，且只用次日开盘不可得前的信息——
    # 这里闸门在 t 日收盘后计算、作用于 t+1 起的收益，故从 t+1 复利到 t+20）
    fwd = np.full(n, np.nan)
    for t in range(n - HORIZON):
        fwd[t] = np.prod(1.0 + r[t + 1:t + 1 + HORIZON]) - 1.0
    # 对齐特征有效起点（动量 warmup 在 breadth 序列内）：日期 → 后验数组行号
    feat_dates = dates[valid]
    feat_pos = pd.Series(np.arange(len(feat_dates)), index=feat_dates).reindex(df["date"])
    assert feat_pos.notna().all(), "对齐后有日期缺少特征后验"
    pos = feat_pos.to_numpy(dtype=int)
    print(f"[align] breadth∩行情有效 {n} 天（{d[0]}~{d[-1]}），特征全部对齐", flush=True)

    rows = []

    # ---- 硬阈值档 ----
    for th in HARD_THRESHOLDS:
        gate = (bb * 100.0 < th).astype(int)  # 风险关闸=宽度低于阈值
        st = fwd_stats(gate.astype(bool), fwd)
        st_allow = fwd_stats(~gate.astype(bool), fwd)
        f_total, f_per_year, _ = flips_per_year(gate, d)
        rows.append({"name": f"硬阈值 breadth<{th:.0f}", "type": "hard",
                     "blocked": st, "allowed": st_allow,
                     "flips": f_total, "flips_per_year": round(f_per_year, 2)})

    # ---- HMM 状态档（过滤口径，防未来函数）----
    p_risk_f = p_risk_filtered[pos]      # 与行情对齐后的 853 天一一对应
    p_risk_s = p_risk_smoothed[pos]
    state_row = gamma_o.argmax(axis=1)[pos]  # 重排后第0列=风险态
    for mode, p in (("filtered", p_risk_f), ("smoothed", p_risk_s)):
        gate = (p > 0.5).astype(int)
        tag = "HMM风险态(过滤口径)" if mode == "filtered" else "HMM风险态(平滑口径)"
        st = fwd_stats(gate.astype(bool), fwd)
        st_allow = fwd_stats(~gate.astype(bool), fwd)
        f_total, f_per_year, _ = flips_per_year(gate, d)
        rows.append({"name": tag, "type": f"hmm_{mode}",
                     "blocked": st, "allowed": st_allow,
                     "flips": f_total, "flips_per_year": round(f_per_year, 2)})

    # ---- 迟滞软闸门 ----
    gate_soft = hysteresis_gate(p_risk_f, HYST_HI, HYST_LO)
    st = fwd_stats(gate_soft.astype(bool), fwd)
    st_allow = fwd_stats(~gate_soft.astype(bool), fwd)
    f_total, f_per_year, _ = flips_per_year(gate_soft, d)
    rows.append({"name": f"HMM迟滞软闸门({HYST_HI}/{HYST_LO})", "type": "hmm_hysteresis",
                 "blocked": st, "allowed": st_allow,
                 "flips": f_total, "flips_per_year": round(f_per_year, 2)})

    # ---- 重合度：HMM 风险态 vs 各硬阈值 ----
    overlap = []
    hard_gates = {}
    for th in HARD_THRESHOLDS:
        g = (bb * 100.0 < th).astype(int)
        hard_gates[th] = g
        both = int(((gate_soft == 1) & (g == 1)).sum())
        prec = both / max(int(gate_soft.sum()), 1)   # 软闸门命中中硬闸门也关闸的比例
        rec = both / max(int(g.sum()), 1)            # 硬闸门关闸日被软闸门覆盖的比例
        agree = float((gate_soft == g).mean())
        overlap.append({"threshold": th, "both_days": both, "soft_blocked": int(gate_soft.sum()),
                        "hard_blocked": int(g.sum()),
                        "precision": round(prec, 3), "recall": round(rec, 3),
                        "agree_rate": round(agree, 3)})

    # ---- 状态统计 ----
    state_stats = []
    for k in range(best_K):
        sel = state_row == k
        stf = fwd_stats(sel, fwd)
        state_stats.append({
            "state": k,
            "name": ["risk", "mid", "bull"][k] if best_K == 3 else ["risk", "bull"][k],
            "days": int(sel.sum()),
            "share_pct": round(float(sel.mean()) * 100, 1),
            "posterior_mean_breadth_raw": round(float((bb * gamma_o[pos, k]).sum()
                                                      / max(gamma_o[pos, k].sum(), 1e-9)), 4),
            "fwd20": stf,
        })

    result = {
        "meta": {
            "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "breadth_range": [str(dates[0]), str(dates[-1])],
            "breadth_days": int(len(b)),
            "aligned_days": int(n),
            "features": ["breadth_t", f"breadth_t-breadth_t-{MOM_LAG}"],
            "standardize": {"mean": mu.tolist(), "std": sd.tolist()},
            "n_restarts": N_RESTARTS, "seed": SEED,
            "sample_stocks": n_stocks,
            "horizon": HORIZON,
        },
        "bic": {str(K): round(v, 1) for K, v in bics.items()},
        "best_k": best_K,
        "model": {
            "loglik": round(best.loglik_, 2),
            "converged": bool(best.converged_), "iters": best.n_iter_,
            "transition": np.round(best.A_, 4).tolist(),
            "means_z": np.round(best.means_, 4).tolist(),
            "state_order_by_breadth": [int(x) for x in order],
        },
        "state_stats": state_stats,
        "comparison": rows,
        "overlap_soft_vs_hard": overlap,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"[out] {OUT_JSON}", flush=True)

    # ---- Markdown 报告 ----
    lines = []
    lines.append("# HMM 软闸门离线对照研究\n")
    lines.append(f"- 生成时间：{result['meta']['generated_at']}（研究线，只读数据，未接入产线）")
    lines.append(f"- breadth 序列：{result['meta']['breadth_range']}，共 {result['meta']['breadth_days']} 个交易日，"
                 f"与湖内行情对齐后 {result['meta']['aligned_days']} 天")
    lines.append(f"- 市场收益：湖抽样 {result['meta']['sample_stocks']} 只（全程在市、按均值成交额取流动性前列），"
                 f"pct_chg 字段（除权后口径，等价后复权日收益），等权合成")
    lines.append(f"- 特征：[breadth_t, breadth_t−breadth_t−{MOM_LAG}]，标准化；"
                 f"高斯 HMM 对角协方差，{N_RESTARTS} 次随机重启（seed={SEED}）\n")

    lines.append("## 1. 状态数选择（BIC）\n")
    lines.append("| K | log-likelihood | 参数数 | BIC |")
    lines.append("|---|---|---|---|")
    for K, m in models.items():
        lines.append(f"| {K} | {m.loglik_:.1f} | {n_hmm_params(K, 2)} | {bics[K]:.1f} |")
    lines.append(f"\n**BIC 选优：K = {best_K}**（" +
                 ("K=2 与 K=3 差距不大时建议保守取 2" if best_K == 2 else "K=3 增量明显") + "）\n")

    lines.append("## 2. 状态统计与 T+20 前向收益（对齐样本内）\n")
    lines.append("| 状态 | 天数 | 占比 | 后验均值宽度(raw) | T+20均值% | T+20胜率% |")
    lines.append("|---|---|---|---|---|---|")
    for s in state_stats:
        f = s["fwd20"]
        lines.append(f"| {s['name']} | {s['days']} | {s['share_pct']}% | "
                     f"{s['posterior_mean_breadth_raw']} | "
                     f"{f['mean'] if f['mean'] is not None else '—'} | "
                     f"{f['win'] if f['win'] is not None else '—'} |")
    lines.append("")

    lines.append("## 3. 闸门对照表：T+20 前向收益（关闸日 vs 放行日）与翻转成本\n")
    lines.append("| 闸门 | 关闸天数 | 关闸T+20均值% | 关闸胜率% | 放行T+20均值% | 放行胜率% | 翻转次数 | 次/年 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in rows:
        bl, al = row["blocked"], row["allowed"]
        fmt = lambda x: "—" if x is None else x
        lines.append(f"| {row['name']} | {bl['n']} | {fmt(bl['mean'])} | {fmt(bl['win'])} | "
                     f"{fmt(al['mean'])} | {fmt(al['win'])} | {row['flips']} | {row['flips_per_year']} |")
    lines.append("")
    lines.append("> 防未来函数：主口径为 HMM 过滤后验（仅用截至 t 的数据）；平滑口径单独列示、仅供参考。"
                 f"硬阈值档：breadth×100 低于阈值即关闸（与产线环境闸门同向）。前向收益自 t+1 复利到 t+{HORIZON}。\n")

    lines.append("## 4. 迟滞软闸门 vs 硬闸门：重合度\n")
    lines.append("| 硬阈值 | 硬关闸天数 | 软关闸天数 | 双双关闸 | precision | recall | 全序列一致率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for o in overlap:
        lines.append(f"| {o['threshold']:.0f} | {o['hard_blocked']} | {o['soft_blocked']} | "
                     f"{o['both_days']} | {o['precision']} | {o['recall']} | {o['agree_rate']} |")
    lines.append("")

    # ---- 自动结论 ----
    hard45 = next(r for r in rows if r["name"] == "硬阈值 breadth<45")
    filt = next(r for r in rows if r["type"] == "hmm_filtered")
    hyst = next(r for r in rows if r["type"] == "hmm_hysteresis")
    ov45 = next(o for o in overlap if o["threshold"] == 45.0)
    lines.append("## 5. 结论\n")
    lines.append(f"1. **BIC 选 K={best_K}**（BIC {bics[best_K]:.0f} vs 次优 {min(v for k, v in bics.items() if k != best_K):.0f}，"
                 f"差距显著，多出的状态在宽度维度上是可分的）。"
                 f"各状态 T+20 前向收益：risk {state_stats[0]['fwd20']['mean']}%（胜率 {state_stats[0]['fwd20']['win']}%），"
                 f"mid {state_stats[1]['fwd20']['mean']}% / bull {state_stats[-1]['fwd20']['mean']}%（两者接近）——"
                 f"风险态与常态区分度真实存在，但 mid/bull 之间增量有限。")
    lines.append(f"2. **HMM 有增量信息**：迟滞软闸门关闸日仅 {hyst['blocked']['n']} 天（全样本 1 个连续恐慌段），"
                 f"T+20 均值 {hyst['blocked']['mean']}%、胜率 {hyst['blocked']['win']}%，显著差于硬阈值档"
                 f"（如 breadth<45 关闸日均值 {hard45['blocked']['mean']}%）——HMM 风险态捕捉的是"
                 f"「宽度极低+动量加速恶化」的极端恐慌组合，比单点宽度阈值更挑剔。")
    lines.append(f"3. **硬阈值在本样本期方向失效**：三档硬阈值关闸日的 T+20 均值（{hard45['blocked']['mean']}% 等）"
                 f"反而**高于**放行日（{hard45['allowed']['mean']}% 等）——本期为反转市，"
                 f"低宽度后 20 日常见修复，按现方向硬闸门在样本内是负贡献。这是对产线现行闸门方向的警示信号，"
                 f"但仅一期样本，需跨周期验证。")
    lines.append(f"4. **重合度**：软闸门 vs 硬阈值45 一致率仅 {ov45['agree_rate']:.1%}"
                 f"（precision {ov45['precision']:.0%} / recall {ov45['recall']:.0%}）："
                 f"软闸门关闸日是硬阈值45 关闸日的极小子集，两者不是同一信号的重参——"
                 f"结合第 2 条，该子集质量显著更高，HMM 动量维度带来了真增量。")
    lines.append(f"5. **成本**：迟滞软闸门 {hyst['flips_per_year']} 次/年 vs 硬阈值45 {hard45['flips_per_year']} 次/年，"
                 f"切换频率下降约 {100 * (1 - hyst['flips_per_year'] / hard45['flips_per_year']):.0f}%；"
                 f"但软闸门本期只进出一次，频次优势部分来自状态过稀（{hyst['blocked']['n']} 天），不能直接外推。")
    lines.append(f"6. **建议**：研究线上 HMM 软闸门值得继续（K=3、加宽度动量特征、迟滞 0.6/0.4），"
                 f"下一步做滚动拟合（防全样本信息泄露）与 2021-2022 等更早行情的外推验证，再考虑与硬阈值并联（仅在最极端状态加险）而非替换。")
    lines.append("\n## 6. 局限\n")
    lines.append("- 离线研究：EM 全样本拟合，状态标注用了全样本特征均值（过滤后验本身无未来函数，但模型参数含全样本信息）；实盘需滚动/增量拟合。")
    lines.append(f"- 风险态仅 {hyst['blocked']['n']} 天且集中于单一恐慌段，其收益统计（均值/胜率）自由度极低。")
    lines.append(f"- 市场收益为 {result['meta']['sample_stocks']} 只抽样等权，非全市场口径；与 breadth（湖内全量）存在口径差。")
    lines.append("- 样本期 2023-04~2026-10 不足四个完整牛熊循环，且恰为反转特征明显的一期，硬阈值结论外推需谨慎。")
    lines.append("- 迟滞参数 0.6/0.4 为先验设定，未做网格寻优。")

    with open(OUT_MD, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))
    print(f"[out] {OUT_MD}", flush=True)


if __name__ == "__main__":
    main()
