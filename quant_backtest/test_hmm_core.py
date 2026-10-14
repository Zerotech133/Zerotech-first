# -*- coding: utf-8 -*-
"""
HMM 核心实现测试：合成两状态数据（低波正收益段 + 高波负收益段拼接）。
断言：
  1. Viterbi 能恢复状态切换点（±3 日容差）
  2. EM 收敛（似然增量低于阈值）
  3. BIC 在 K=1/2/3 中选对 K=2

运行： "C:/Program Files/Python311/python.exe" test_hmm_core.py
（亦兼容 pytest test_hmm_core.py）
"""
import numpy as np

from hmm_gate_research import GaussianHMM, fit_hmm_best, n_hmm_params, bic

SEED = 7


def make_synthetic(seed=SEED):
    """两状态序列：前 200 天低波正漂移（state 0），后 200 天高波负漂移（state 1）。"""
    rng = np.random.default_rng(seed)
    n0, n1 = 200, 200
    seg0 = rng.normal(0.10, 0.30, n0)   # 低波正收益
    seg1 = rng.normal(-0.35, 1.00, n1)  # 高波负收益
    X = np.concatenate([seg0, seg1]).reshape(-1, 1)
    true_switch = n0  # 第 200 天切换
    return X, true_switch


def test_em_convergence():
    X, _ = make_synthetic()
    m = GaussianHMM(2, seed=SEED, max_iter=300, tol=1e-6).fit(X)
    assert m.converged_, "EM 未收敛"
    assert m.n_iter_ < 300, f"EM 迭代异常多: {m.n_iter_}"
    assert np.isfinite(m.loglik_) and m.loglik_ > -np.inf


def test_viterbi_recovers_switch():
    X, true_switch = make_synthetic()
    m = GaussianHMM(2, seed=SEED, max_iter=300, tol=1e-6).fit(X)
    path = m.predict(X)
    # 找路径里的切换点（允许状态标号任意方向）
    diffs = np.nonzero(np.diff(path) != 0)[0]
    # 期望恰有一次主切换：取与真实点最近的一次
    assert len(diffs) >= 1, "Viterbi 路径没有任何切换"
    nearest = int(np.min(np.abs(diffs + 1 - true_switch)))  # diff[i] 表示 i->i+1 切换
    assert nearest <= 3, f"切换点恢复误差 {nearest} 天 > ±3 天容差（切换点集合 {diffs[:10]}）"
    # 两段占比合理：每段至少 80% 归入正确状态
    ratio0 = (path[:true_switch] == path[0]).mean()
    ratio1 = (path[true_switch:] == path[-1]).mean()
    assert ratio0 > 0.8 and ratio1 > 0.8, f"分段纯度不足: {ratio0:.2f}/{ratio1:.2f}"


def test_bic_selects_k2():
    X, _ = make_synthetic()
    T = len(X)
    bics = {}
    for K in (1, 2, 3):
        m = fit_hmm_best(X, K, seed=SEED, n_restarts=4)
        bics[K] = bic(m.loglik_, T, n_hmm_params(K, 1))
    assert min(bics, key=bics.get) == 2, f"BIC 未选对 K=2: {bics}"


if __name__ == "__main__":
    fns = [test_em_convergence, test_viterbi_recovers_switch, test_bic_selects_k2]
    ok = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            ok += 1
        except AssertionError as e:
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{ok}/{len(fns)} tests passed")
    raise SystemExit(0 if ok == len(fns) else 1)
