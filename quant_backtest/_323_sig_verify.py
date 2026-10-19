# -*- coding: utf-8 -*-
"""323 信号层验证：双刀全市场 14:45 快照（8-10~9-01，17 交易日）。"""
import sys
import time

sys.path.insert(0, r"C:\项目\老田的AI助手\quant_backtest")
import backtest_engine_v2 as E

msgs = []


def progress(phase, pct, note):
    msgs.append(note)


t0 = time.time()
sig, sig_sell, meta, err = E.calc_dknife_signals_market("20260810", "20260901", progress, hist_st=True)
if err:
    print("ERR:", err)
    sys.exit(1)
print("耗时 %.0fs | 信号日数 %d | 信号总次 %d" % (time.time() - t0, len(sig), sum(len(v) for v in sig.values())))
print("meta:", {k: meta[k] for k in ("universe", "hist_st", "source")})
print("hist_st 生效证据:", meta["hist_st"])
per = {d: len(v) for d, v in sorted(sig.items())}
print("每日信号数:", per)
tot = sum(per.values())
print("日均信号: %.1f | 有信号天数占比: %d/%d" % (tot / max(len(sig), 1), len(sig), 17))
# 抽样信号票名
sample_day = sorted(sig)[-1]
names = E.names if hasattr(E, "names") else {}
ss = sorted(sig[sample_day])[:8]
print("样例 %s:" % sample_day, ss)
