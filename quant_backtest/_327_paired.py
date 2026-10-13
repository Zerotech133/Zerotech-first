# -*- coding: utf-8 -*-
"""K-STOCK-327 · 双刀×缠论并轨对照回测（首个可比信号窗 202609-202610）。

设计：
- 同窗：d0=20260901 → d1=20261012（双刀 54 日 warmup 后首个可比信号窗）。
- 同宇宙：以 14:45 快照覆盖宇宙（当前 972 只·回补中期版）为对照域，
  缠论臂经 manual_picks 限定到同一名单（引擎信号过滤层生效，hist_st 各自再剔）。
- 三臂：
  1. dknife      双刀日内纪律（gate off·lc on·hist_st on）＝325 E2E 口径；
  2. chan_base   缠论同宇宙基线（gate off·min0·lc·runup5·hist_st）；
  3. chan_final  缠论终选（gate high·min80·lc·runup5·hist_st）。
- 事件缓存 sig_events_market_v3_20260901_20261012.json 需先备（327 前置扫描）。
- 幂等：产物存在即 skip（--force 覆盖）；--only 臂过滤。

用法：python _327_paired.py [--force] [--only dknife,chan_base,chan_final]
"""
import json
import os
import sys
import time

import pandas as pd

from _run_guard import run_guarded

QBT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
BT2 = os.path.join(QBT, "backtest_engine_v2.py")
SNAP = os.path.join(QBT, "data", "tdx", "dknife_snap", "dknife_snap.parquet")
BT2_DIR = os.path.join(QBT, "data", "bt2")
CACHE = os.path.join(QBT, "data", "sig_events",
                     "sig_events_market_v3_20260901_20261012.json")
D0, D1 = "20260901", "20261012"
FORCE = "--force" in sys.argv
ONLY = None
if "--only" in sys.argv:
    ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(","))


def main():
    log = open(os.path.join(QBT, "_327_paired.log"), "w", encoding="utf-8")

    if not os.path.exists(CACHE):
        print("GUARD FAIL: 事件缓存缺失，先跑 market_scan --d0 %s --d1 %s" % (D0, D1))
        sys.exit(2)
    if not os.path.exists(SNAP):
        print("GUARD FAIL: 14:45 快照缺失，先跑 dknife_snap.py")
        sys.exit(3)

    snap = pd.read_parquet(SNAP, columns=["ts_code"])
    picks = sorted(snap.ts_code.unique())
    picks_file = os.path.join(QBT, "_327_universe.txt")
    open(picks_file, "w", encoding="utf-8").write(",".join(picks))
    print("universe from snapshot: %d codes -> %s" % (len(picks), picks_file))

    ARMS = [
        ("dknife", ["--strategy", "double_knife"]),
        ("chan_base", ["--strategy", "chanlun", "--manual-picks-file", picks_file,
                       "--gate-mode", "off"]),
        ("chan_final", ["--strategy", "chanlun", "--manual-picks-file", picks_file,
                        "--gate-mode", "high", "--min-score", "80"]),
    ]
    results = []
    for suf, extra in ARMS:
        if ONLY and suf not in ONLY:
            continue
        jid = "327_%s" % suf   # 引擎落盘自动加 bt2_ 前缀 → bt2_327_*.json
        out_fp = os.path.join(BT2_DIR, "bt2_%s.json" % jid)
        if os.path.exists(out_fp) and not FORCE:
            print("skip (exists):", jid)
            results.append((suf, json.load(open(out_fp, encoding="utf-8"))["summary"]))
            continue
        t0 = time.time()
        if os.path.exists(out_fp):
            os.remove(out_fp)   # --force 语义前移：先删旧产物，杜绝“引擎没写文件→读旧 json 误判 DONE”
        print("RUN", jid, flush=True)
        # K-STOCK-337: subprocess.run -> run_guarded（静默挂死杀树+自动重试一次）
        rc, _used = run_guarded(
            [PY, "-u", BT2, "--d0", D0, "--d1", D1, "--hist-st", "true",
             "--job-id", jid] + extra,
            log_fh=log, cwd=QBT, label=jid)
        if rc != 0 or not os.path.exists(out_fp):
            print("FAIL %s rc=%s out_exists=%s -> _327_paired.log"
                  % (jid, rc, os.path.exists(out_fp)))
            sys.exit(4)
        s = json.load(open(out_fp, encoding="utf-8"))["summary"]
        results.append((suf, s))
        print("DONE %s (%.0fs) ret=%s%% PF=%s trades=%s mdd=%s%%" % (
            jid, time.time() - t0, s["total_return_pct"], s["profit_factor"],
            s["n_trades"], s["max_drawdown_pct"]), flush=True)

    # 对照表
    lines = ["# K-STOCK-327 双刀×缠论并轨对照（窗 %s~%s·同宇宙 %d 只）"
             % (D0, D1, len(picks)), "",
             "| 臂 | ret% | annual% | PF | 胜率% | 笔数 | 回撤% | 平均仓位% |",
             "|---|---|---|---|---|---|---|---|"]
    for suf, s in results:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            suf, s["total_return_pct"], s["annualized_pct"], s["profit_factor"],
            s["win_rate_pct"], s["n_trades"], s["max_drawdown_pct"],
            s.get("avg_invested_pct")))
    cmp_fp = os.path.join(QBT, "_327_compare.md")
    open(cmp_fp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    log.close()
    print("\ncompare ->", cmp_fp)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
