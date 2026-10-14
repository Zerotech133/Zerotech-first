# -*- coding: utf-8 -*-
"""trial_ledger 单元测试（G2 加固 2026-10-14）。

覆盖：DSR 数值性质（N=1 退化为 PSR、N 增大 DSR 单调降、零偏度峰度正态
解析对照）、SR0 公式、逆正态精度、台账幂等/原子追加/坏行容忍、CLI record。
运行：python -m pytest test_trial_ledger.py -q
"""
import json
import math
import os
import tempfile

import trial_ledger as tl


def test_norm_ppf_accuracy():
    """逆正态 vs 已知分位数（精度 1e-6）。"""
    assert abs(tl._norm_ppf(0.975) - 1.959964) < 1e-5
    assert abs(tl._norm_ppf(0.5)) < 1e-6
    assert abs(tl._norm_ppf(0.001) + 3.090232) < 1e-5


def test_dsr_n1_equals_psr():
    """N=1：SR0→0（z1=z2=0），DSR 退化为 PSR=Φ(SR·sqrt(T-1)/denom)。"""
    dsr, sr0 = tl.deflated_sharpe_ratio(0.1, 252, n_trials=1)
    assert abs(sr0) < 1e-9, sr0
    denom = math.sqrt(1 - 0 + (3 - 1) / 4 * 0.01)
    expect = tl._norm_cdf(0.1 * math.sqrt(251) / denom)
    assert abs(dsr - expect) < 1e-9


def test_dsr_monotone_decreasing_in_ntrials():
    """试验次数越多，SR0 越高、DSR 越低（幸存者偏差惩罚方向正确）。"""
    vals = [tl.deflated_sharpe_ratio(0.15, 252, n)[0] for n in (1, 5, 36, 500)]
    assert vals == sorted(vals, reverse=True), vals
    # N 大到一定程度后 0.15 的 SR 不再显著
    assert vals[-1] < 0.95


def test_dsr_pass_fail_boundary():
    """强 SR 大样本过线、弱 SR 小样本不过线（G2 参考线双向 sanity）。"""
    dsr_good, _ = tl.deflated_sharpe_ratio(0.30, 500, n_trials=10)
    dsr_bad, _ = tl.deflated_sharpe_ratio(0.05, 30, n_trials=100)
    assert dsr_good >= 0.95
    assert dsr_bad < 0.95


def test_expected_max_sharpe_positive_and_growing():
    v = tl.var_sharpe(0.1, 100)
    s10 = tl.expected_max_sharpe(10, v)
    s100 = tl.expected_max_sharpe(100, v)
    assert 0 < s10 < s100


def test_sharpe_from_returns():
    rets = [0.01, 0.02, -0.005, 0.015, 0.008, -0.002, 0.012]
    sr, skew, kurt, n = tl.sharpe_from_returns(rets)
    assert n == 7 and sr > 0 and kurt > 0
    # 与 numpy 手算一致
    import numpy as np
    a = np.array(rets)
    assert abs(sr - a.mean() / a.std(ddof=1)) < 1e-12


def test_ledger_record_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        e = {"strategy": "cross", "config_fingerprint": '{"k":3,"q":75}',
             "sharpe": 0.12, "t": 252, "n_trials": 36, "verdict": "kept"}
        ok1, tid = tl.record_trial(e, path)
        ok2, tid2 = tl.record_trial(e, path)
        assert ok1 and not ok2 and tid == tid2
        rows = tl.load_trials(path)
        assert len(rows) == 1 and rows[0]["trial_id"] == tid


def test_ledger_stats_and_bad_lines():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        tl.record_trial({"strategy": "cross", "config_fingerprint": "a",
                         "verdict": "kept"}, path)
        tl.record_trial({"strategy": "cross", "config_fingerprint": "b",
                         "verdict": "rejected"}, path)
        with open(path, "a", encoding="utf-8") as f:
            f.write("{bad line\n")            # 坏行容忍
        st = tl.ledger_stats(path)
        assert st["total"] == 2
        assert st["by_strategy"]["cross"] == {"n": 2, "kept": 1,
                                              "rejected": 1}


def test_cli_record_and_dsr(tmp_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        rc = tl.main(["record", "--strategy", "chanlun",
                      "--config", '{"beichi":"P6-a-3"}', "--sharpe", "-0.02",
                      "--t", "219", "--n-trials", "9",
                      "--verdict", "rejected", "--note", "全否决",
                      "--path", path])
        assert rc == 0
        rows = tl.load_trials(path)
        assert rows[0]["verdict"] == "rejected"
        assert rows[0]["dsr"] is not None and rows[0]["dsr"] < 0.95


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
