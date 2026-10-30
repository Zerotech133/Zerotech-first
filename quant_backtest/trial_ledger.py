# -*- coding: utf-8 -*-
"""试验台账 + Deflated Sharpe Ratio（trial-ledger 2026-10-14，G2 闸门加固）。

出处：开源借鉴 #1（purgedcv/ml4t-diagnostic 思想，纯 stdlib+math 自实现
零新依赖）+《十二策略对照复盘与开源借鉴报告》优先级第 1 位。动机：本会话
参数网格（cross 36 组合、chanlun 9 档、双刀变体、qpzm 门控）都是"事后从
N 次试验里挑最优"——幸存者偏差使报告的 Sharpe 系统性偏高。Deflated SR
（Bailey & López de Prado 2014）按试验次数 N 折减：

  SR0 = sqrt(V[SR]) · ((1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e)))   基准 SR
  DSR = Φ( (SR - SR0)·sqrt(T-1) / sqrt(1 - γ3·SR + ((γ4-1)/4)·SR²) )

  SR 为periodic（非年化）Sharpe，γ3 偏度、γ4 峰度、T 样本数、N 试验数。
  DSR<0.95 → 该"最优"在计入学试次数后不显著（建议 G2 闸门增列参考线）。

台账：data/trial_ledger.jsonl，每行一次参数试验（含被否决的配置——
被否决的也要记，否则 N 被低估、DSR 虚高）。幂等键 trial_id=sha1(策略|
配置指纹)；O_APPEND 单次 write+fsync+锁（判例 gate_shadow_collect）。

用法:
  python trial_ledger.py record --strategy cross --config '{"k":3,...}' \
      --sharpe 0.42 --n 252 --skew -0.1 --kurt 4.2 --verdict kept
  python trial_ledger.py dsr --sharpe 0.42 --n-trials 36 --t 252 \
      [--skew -0.1] [--kurt 4.2]
  python trial_ledger.py stats                    # 台账统计
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LEDGER = os.path.join(HERE, "data", "trial_ledger.jsonl")
LOCK = LEDGER + ".lock"
STALE_LOCK_SEC = 120

_DSR_PASS = 0.95  # G2 参考线：DSR ≥0.95 视为"计入学试次数后仍显著"


# ── 数值核（纯 math，可独立测试） ─────────────────────────────
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p):
    """Acklam 逆正态（纯 python，精度 ~1e-9，够 DSR 用）。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p out of (0,1)")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def expected_max_sharpe(n_trials, var_sr, gamma=0.5772156649):
    """N 次试验下最优 SR 的期望基准 SR0（Bailey & LdP 式 4-5）。

    var_sr：单次试验 SR 的方差（跨试验）；无实测时用
    var_sr_from_single(sr, t, skew, kurt) 从最优试验自身估计。
    """
    if n_trials < 1 or var_sr <= 0:
        raise ValueError("n_trials>=1 且 var_sr>0")
    if n_trials <= 1:
        return 0.0  # 无多重性：无基准惩罚（退化为 PSR）
    e = math.e
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * e))
    return math.sqrt(var_sr) * ((1 - gamma) * z1 + gamma * z2)


def var_sharpe(sr, t, skew=0.0, kurt=3.0):
    """单试验 SR 估计方差 V[SR] = (1 - γ3·SR + (γ4-1)/4·SR²)/(T-1)。"""
    if t < 2:
        raise ValueError("t>=2")
    v = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / (t - 1)
    return max(v, 1e-12)


def deflated_sharpe_ratio(sr, t, n_trials, skew=0.0, kurt=3.0, var_sr=None):
    """DSR ∈ (0,1)：SR 在计入学 N 次试验后仍为真的概率。返回 (dsr, sr0)。

    var_sr 缺省时由最优试验自身 (sr,t,skew,kurt) 估计（保守常用口径）。
    """
    if var_sr is None:
        var_sr = var_sharpe(sr, t, skew, kurt)
    sr0 = expected_max_sharpe(n_trials, var_sr)
    denom = math.sqrt(max(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr, 1e-12))
    z = (sr - sr0) * math.sqrt(t - 1) / denom
    return _norm_cdf(z), sr0


def sharpe_from_returns(rets):
    """周期收益序列 → (sr, skew, kurt, t)。无 2 个以上样本则抛。"""
    n = len(rets)
    if n < 3:
        raise ValueError("returns n>=3")
    mu = sum(rets) / n
    var = sum((x - mu) ** 2 for x in rets) / (n - 1)
    sd = math.sqrt(var)
    if sd <= 0:
        raise ValueError("zero variance returns")
    sr = mu / sd
    m3 = sum((x - mu) ** 3 for x in rets) / n
    m4 = sum((x - mu) ** 4 for x in rets) / n
    skew = m3 / sd**3
    kurt = m4 / sd**4
    return sr, skew, kurt, n


# ── 台账（幂等原子追加，判例 gate_shadow_collect） ────────────
def _log(msg):
    try:
        with open("C:/Temp/trial_ledger.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/trial_ledger.py", exc)


def trial_id(strategy, config_fingerprint):
    raw = "%s|%s" % (strategy, config_fingerprint)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _acquire_lock(timeout=30):
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.stat(LOCK).st_mtime > STALE_LOCK_SEC:
                    os.remove(LOCK)
                    continue
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/trial_ledger.py", exc)
            if time.time() >= deadline:
                return False
            time.sleep(0.2)


def _release_lock():
    try:
        os.remove(LOCK)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/trial_ledger.py", exc)


def record_trial(entry, path=LEDGER):
    """落一条试验记录（幂等：trial_id 撞车则跳过）。返回 (appended, id)。"""
    tid = trial_id(str(entry.get("strategy")), str(entry.get("config_fingerprint")))
    full = {
        "trial_id": tid,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        **entry,
        "strategy": str(entry.get("strategy")),
        "config_fingerprint": str(entry.get("config_fingerprint")),
    }
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line).get("trial_id"))
                except Exception:
                    continue
    if tid in seen:
        return False, tid
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not _acquire_lock():
        _log("record: lock timeout, skip %s" % tid)
        return False, tid
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(full, ensure_ascii=False) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return True, tid
    finally:
        _release_lock()


def load_trials(path=LEDGER):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def ledger_stats(path=LEDGER):
    rows = load_trials(path)
    by = {}
    for r in rows:
        s = by.setdefault(r.get("strategy", "?"), {"n": 0, "kept": 0, "rejected": 0})
        s["n"] += 1
        v = str(r.get("verdict", ""))
        if v in ("kept", "rejected"):
            s[v] += 1
    return {"total": len(rows), "by_strategy": by}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/trial_ledger.py", exc)
    ap = argparse.ArgumentParser(description="试验台账 + DSR")
    sub = ap.add_subparsers(dest="cmd")
    p_rec = sub.add_parser("record")
    p_rec.add_argument("--strategy", required=True)
    p_rec.add_argument("--config", required=True, help="配置指纹（JSON 串）")
    p_rec.add_argument("--sharpe", type=float, default=None)
    p_rec.add_argument("--t", type=int, default=None, help="样本期数")
    p_rec.add_argument("--n-trials", type=int, default=None)
    p_rec.add_argument("--skew", type=float, default=None)
    p_rec.add_argument("--kurt", type=float, default=None)
    p_rec.add_argument("--oos", default=None, help="OOS 判据结果（如 0.63）")
    p_rec.add_argument(
        "--verdict", default="kept", choices=["kept", "rejected", "pending"]
    )
    p_rec.add_argument("--note", default="")
    p_rec.add_argument("--path", default=LEDGER, help="台账路径覆盖")
    p_dsr = sub.add_parser("dsr")
    p_dsr.add_argument("--sharpe", type=float, required=True)
    p_dsr.add_argument("--t", type=int, required=True)
    p_dsr.add_argument("--n-trials", type=int, required=True)
    p_dsr.add_argument("--skew", type=float, default=0.0)
    p_dsr.add_argument("--kurt", type=float, default=3.0)
    sub.add_parser("stats")
    a = ap.parse_args(argv)
    if a.cmd == "record":
        dsr = sr0 = None
        if a.sharpe is not None and a.t and a.n_trials:
            dsr, sr0 = deflated_sharpe_ratio(
                a.sharpe, a.t, a.n_trials, a.skew or 0.0, a.kurt or 3.0
            )
        ok, tid = record_trial(
            {
                "strategy": a.strategy,
                "config_fingerprint": a.config,
                "sharpe": a.sharpe,
                "t": a.t,
                "n_trials": a.n_trials,
                "skew": a.skew,
                "kurt": a.kurt,
                "oos": a.oos,
                "verdict": a.verdict,
                "note": a.note,
                "dsr": None if dsr is None else round(dsr, 4),
                "sr0": None if sr0 is None else round(sr0, 4),
            },
            a.path,
        )
        print(
            "RECORD %s id=%s dsr=%s"
            % (
                "OK" if ok else "DUP",
                tid,
                None
                if dsr is None
                else "%.4f%s"
                % (dsr, " ≥0.95 显著" if dsr >= _DSR_PASS else " <0.95 不显著"),
            )
        )
        return 0
    if a.cmd == "dsr":
        dsr, sr0 = deflated_sharpe_ratio(a.sharpe, a.t, a.n_trials, a.skew, a.kurt)
        print(
            "DSR=%.4f SR0=%.4f %s"
            % (dsr, sr0, "PASS(≥0.95)" if dsr >= _DSR_PASS else "FAIL(<0.95)")
        )
        return 0
    print(json.dumps(ledger_stats(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
