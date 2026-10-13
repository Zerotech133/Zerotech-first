# -*- coding: utf-8 -*-
"""K-STOCK-319 · 10-16 开闸一键正式跑批（315 总表唯一基准·零参数改动）。

流程：
1. 读数据湖最新交易日 d1（000001.SZ max trade_date）——守卫：必须晚于 20261012，否则提示先增量拉数退出；
2. 事件缓存按 (d0,d1) 键控：新 d1 缓存缺失则先重扫 market_scan（y3/y1 两窗各一）；
3. 七臂串行（前缀 open1016_*）：y3 五臂（base/weak/high/hsc/final）+ y1 双臂（base/final）；
4. 幂等：产物已存在跳过（--force 覆盖）；
5. 跑完自动对照 315/311 基线产 delta 表落 _open1016_compare.md。

用法：python open1016_run.py [--force]
"""
import json
import os
import subprocess
import sys
import time

import pandas as pd

QBT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
BT2 = os.path.join(QBT, "backtest_engine_v2.py")
SCAN = os.path.join(QBT, "market_scan.py")
BT2_DIR = os.path.join(QBT, "data", "bt2")
STATE_DIR = os.path.join(QBT, "data", "bt2_state")
FORCE = "--force" in sys.argv
# ⚠️ --d1-override 仅供链路自测（用既有缓存窗口），正式开闸禁用
D1_OVERRIDE = None
if "--d1-override" in sys.argv:
    D1_OVERRIDE = sys.argv[sys.argv.index("--d1-override") + 1]
# --only y3_base[,y1_final]：只跑指定臂（正式重跑/自测用）
ONLY = None
if "--only" in sys.argv:
    ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(","))
MIN_D1 = "20261012"

# 315/311 定稿参数（零改动纪律：与 portal/docs/2026-10-13_K-STOCK-315 总表逐项一致）
Y3_D0, Y1_D0 = "20231010", "20251013"
ARMS = [
    # (job_id_suffix, d0, 额外 CLI 参数)
    ("y3_base",  Y3_D0, ["--gate-mode", "off"]),
    ("y3_weak",  Y3_D0, ["--gate-mode", "weak"]),
    ("y3_high",  Y3_D0, ["--gate-mode", "high"]),
    ("y3_hsc",   Y3_D0, ["--gate-mode", "high", "--slot-mode", "score"]),
    ("y3_final", Y3_D0, ["--gate-mode", "high", "--min-score", "80"]),
    ("y1_base",  Y1_D0, ["--gate-mode", "off"]),
    ("y1_final", Y1_D0, ["--gate-mode", "high", "--min-score", "80"]),
]
# 对照基线（315/311 锚定，旧 d1=20261012 口径）
BASELINES = {
    "y3_base":  ("bt2_311y3hst_base.json",  "-32.44"),
    "y3_weak":  ("bt2_315y3weak.json",      "-23.75"),
    "y3_high":  ("bt2_315y3high.json",      "-14.45"),
    "y3_hsc":   ("bt2_315y3hsc.json",       "-21.18"),
    "y3_final": ("bt2_311y3hst_final.json", "-4.96"),
    "y1_base":  ("bt2_311y1hst_base.json",  "-21.90"),
    "y1_final": ("bt2_311y1hst_final.json", "-12.43"),
}


def lake_latest():
    df = pd.read_parquet(os.path.join(QBT, "data", "tdx", "ts_daily"),
                         columns=["ts_code", "trade_date"])
    return str(df.loc[df.ts_code == "000001.SZ", "trade_date"].max())


def ensure_cache(d0, d1, log):
    fp = os.path.join(STATE_DIR, "sig_events_market_v3_%s_%s.json" % (d0, d1))
    if os.path.exists(fp):
        log.write("cache HIT %s\n" % fp)
        return True
    log.write("cache MISS -> rescan market_scan --d0 %s --d1 %s\n" % (d0, d1))
    t0 = time.time()
    r = subprocess.run([PY, "-u", SCAN, "--d0", d0, "--d1", d1],
                       stdout=log, stderr=subprocess.STDOUT, cwd=QBT)
    ok = r.returncode == 0 and os.path.exists(fp)
    log.write("rescan rc=%s ok=%s (%.0fs)\n" % (r.returncode, ok, time.time() - t0))
    return ok


def main():
    log_fp = os.path.join(QBT, "_open1016_run.log")
    log = open(log_fp, "w", encoding="utf-8")
    d1 = D1_OVERRIDE or lake_latest()
    print("lake latest d1 =", d1, "(override=%s)" % D1_OVERRIDE, flush=True)
    if not D1_OVERRIDE and not (d1.isdigit() and d1 > MIN_D1):
        print("GUARD FAIL: 数据湖最新 %s 未越过 %s —— 先增量拉数（ts_daily/adj_factor），再跑本脚本。"
              % (d1, MIN_D1), flush=True)
        log.write("guard fail d1=%s\n" % d1)
        log.close()
        sys.exit(2)

    d0s = {Y3_D0, Y1_D0}
    for d0 in sorted(d0s):
        if not ensure_cache(d0, d1, log):
            print("GUARD FAIL: 事件缓存重扫失败（窗口 %s~%s），详见 %s" % (d0, d1, log_fp))
            log.close()
            sys.exit(3)

    results = []
    for suf, d0, extra in ARMS:
        if ONLY and suf not in ONLY:
            continue
        jid = "open1016_%s" % suf
        out_fp = os.path.join(BT2_DIR, "bt2_%s.json" % jid)
        if os.path.exists(out_fp) and not FORCE:
            print("skip (exists):", jid, flush=True)
            d = json.load(open(out_fp, encoding="utf-8"))
            results.append((suf, d["summary"]))
            continue
        t0 = time.time()
        print("RUN", jid, flush=True)
        r = subprocess.run([PY, "-u", BT2, "--d0", d0, "--d1", d1, "--hist-st", "true",
                            "--job-id", jid] + extra,
                           stdout=log, stderr=subprocess.STDOUT, cwd=QBT)
        if r.returncode != 0 or not os.path.exists(out_fp):
            print("FAIL", jid, "rc=%s -> 见 %s" % (r.returncode, log_fp), flush=True)
            sys.exit(4)
        d = json.load(open(out_fp, encoding="utf-8"))
        results.append((suf, d["summary"]))
        print("DONE %s (%.0fs) ret=%s%% PF=%s trades=%s mdd=%s%% mid_days=%s" % (
            jid, time.time() - t0, d["summary"]["total_return_pct"],
            d["summary"]["profit_factor"], d["summary"]["n_trades"],
            d["summary"]["max_drawdown_pct"],
            d["summary"].get("n_mid_slot_days", "-")), flush=True)

    # 对照 delta 表
    lines = ["# open1016 对照 315/311 锚定基线（旧 d1=20261012）", "",
             "| 臂 | 基线ret% | 本次ret% | delta | 基线PF | 本次PF | 笔数 | 回撤% | 仓位% |",
             "|---|---|---|---|---|---|---|---|---|"]
    for suf, s in results:
        bl_fp, bl_ret = BASELINES[suf]
        ret = s["total_return_pct"]
        try:
            delta = round(ret - float(bl_ret), 2)
        except ValueError:
            delta = "?"
        bl_pf = "-"
        bfp = os.path.join(BT2_DIR, bl_fp)
        if os.path.exists(bfp):
            bl_pf = json.load(open(bfp, encoding="utf-8"))["summary"]["profit_factor"]
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            suf, bl_ret, ret, delta, bl_pf, s["profit_factor"], s["n_trades"],
            s["max_drawdown_pct"], s.get("avg_invested_pct")))
    cmp_fp = os.path.join(QBT, "_open1016_compare.md")
    open(cmp_fp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    log.close()
    print("\ncompare ->", cmp_fp, flush=True)
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
