# -*- coding: utf-8 -*-
"""hf_hist 自检：确认 hf_hist 产出的信号「当时真的能知道」。

这一层的错误比策略错误致命得多：如果 detect_date 早于信号实际可确认的日子，
回测会凭空多出收益，而且看起来极其漂亮（首版就是 +400% 净值）。

断言（每条都独立于被测参数，退化档必须报红）
------------------------------------------------
V1 detect 日截断分析，必须能产出同型同日的信号           —— 核心正确性
V2 detect 日 **早于** 信号日 的行数必须为 0               —— 不允许反向前视
V3 长历史文件里的最大 lag 必须 > 0（若恒 0 说明 verify 没生效）
V4 校验版行数 < 未校验版行数（前视确实存在，不是空转）
V5 2买 的剔除率必须高于 3买（2买 依赖「回调笔完结」，结构滞后最重）
V6 lag 中位数落在 1~30 个交易日（量级要合常识）

用法::

    python -X utf8 tools/research/hf_hist_selfcheck.py
    python -X utf8 tools/research/hf_hist_selfcheck.py --degrade   # 应报红
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QBT = os.path.join(ROOT, "quant_backtest")
sys.path.insert(0, QBT)
sys.path.insert(0, os.path.join(ROOT, "tools", "research"))
import hf_book as H          # noqa: E402

SIG = os.path.join(QBT, "data", "chanlun_hist_signals.json")
SIG_NV = os.path.join(QBT, "data", "chanlun_hist_signals_noverify.json")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def check(sample_n=30, degrade=False):
    res = []

    def add(k, ok, msg):
        res.append((k, bool(ok), msg))

    if not os.path.exists(SIG):
        add("V0", False, "缺 %s" % SIG)
        return res
    d = _load(SIG)
    rows = d["rows"]
    axis = H.load_axis()
    ax_pos = {x: i for i, x in enumerate(axis)}

    # ---- V2 日期方向
    bad = [r for r in rows if H._d(r["detect_date"]) < H._d(r["sig_dt"])]
    add("V2", not bad, "detect 早于 sig 的行 %d 条（必须 0）" % len(bad))

    # ---- V3 lag 上限
    lags = [r["lag"] for r in rows if r.get("lag") is not None]
    mx = max(lags) if lags else 0
    # 退化档：假装 verify 没做，lag 全 0
    got = (mx == 0) if degrade else (mx > 0)
    add("V3", got, "最大 lag = %d（%s）" % (mx, "应>0" if not degrade else "退化档应=0"))

    # ---- V4 前后行数
    if os.path.exists(SIG_NV):
        n_v, n_nv = len(rows), len(_load(SIG_NV)["rows"])
        got = (n_v >= n_nv) if degrade else (n_v < n_nv)
        add("V4", got, "校验 %d 行 / 未校验 %d 行（%s）" % (
            n_v, n_nv, "退化档应>=" if degrade else "应<"))
    else:
        add("V4", False, "缺未校验版对照文件")

    # ---- V5 2买 剔除率最高
    if os.path.exists(SIG_NV):
        nv = _load(SIG_NV)["rows"]
        cnt = lambda xs, t: sum(1 for r in xs if r["type"] == t)  # noqa: E731
        drop = {}
        for t in ("1买", "2买", "3买"):
            a, b = cnt(nv, t), cnt(rows, t)
            drop[t] = 1 - b / a if a else 0
        got = max(drop, key=drop.get) == "2买"
        add("V5", got, "剔除率 %s（2买 应最高）" % "  ".join(
            "%s %.0f%%" % (t, drop[t] * 100) for t in ("1买", "2买", "3买")))

    # ---- V6 lag 中位数量级
    med = sorted(lags)[len(lags) // 2] if lags else 0
    got = (1 <= med <= 30) if not degrade else (med == 0)
    add("V6", got, "lag 中位数 %d 日（应在 1~30）" % med)

    # ---- V1 抽样截断重放
    import chanlun_scan as cs
    import chanlun_signal as csig

    random.seed(20261108)
    pool = [r for r in rows if ax_pos.get(H._d(r["sig_dt"]), 0) > 200]
    sample = random.sample(pool, min(sample_n, len(pool)))
    ok_n, msg = 0, []
    for r in sample:
        code = r["code"]
        p = os.path.join(QBT, "data", "ashare", code.replace(".", "_") + ".parquet")
        if not os.path.exists(p):
            continue
        bars = cs._bars_from_parquet(p)
        if not bars:
            continue
        pos = {str(b["dt"]).replace("-", "")[:8]: i for i, b in enumerate(bars)}
        di = pos.get(H._d(r["detect_date"]))
        if di is None or di + 1 > len(bars):
            continue
        try:
            st = csig._az(bars[: di + 1])
            sigs = csig.find_signals(st["bis"], st["segments"], st["pivots"])
        except Exception:
            continue
        hit = any(x.get("type") == r["type"]
                  and str(x.get("dt") or "")[:10] == r["sig_dt"] for x in sigs)
        if degrade:
            hit = not hit          # 退化档反转预期：断言应当失败
        ok_n += 1 if hit else 0
        if not hit and len(msg) < 3:
            msg.append("%s %s det=%s" % (code, r["type"], r["detect_date"]))
    add("V1", ok_n == len(sample),
        "抽样 %d 条，detect 日可复现 %d 条%s" % (
            len(sample), ok_n, "" if ok_n == len(sample) else "  失败例：" + "；".join(msg)))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--degrade", action="store_true", help="退化档：断言应报红")
    ap.add_argument("--sample", type=int, default=30)
    a = ap.parse_args()
    res = check(a.sample, a.degrade)
    print("hf_hist 自检（%d 条）%s" % (len(res), "· 退化档" if a.degrade else ""))
    bad = 0
    for k, ok, msg in res:
        print("  %-4s %s  %s" % (k, "PASS" if ok else "FAIL", msg))
        bad += 0 if ok else 1
    print("→ %d/%d 通过" % (len(res) - bad, len(res)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
