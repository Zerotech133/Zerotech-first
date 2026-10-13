# -*- coding: utf-8 -*-
"""K-STOCK-325G：回补完成后衔接链一键 runbook（K-ORG 之外主控侧预案）。

链：①守卫（_325b 回补须已静默 ≥15 分钟，防双进程并发写 manifest）
    ②幂等补拉（重跑 _325b_backfill.py：done 全跳过，仅 fail 票重试）
    ③终版快照（dknife_snap.py --src tushare --ym0 202606 --ym1 202610
       ——⚠️ 不带 --verify：该脚本 verify 与重建互斥（if/else），
       带 --verify 只验证旧 rtmin 快照即退出、tushare 不重建（325G 干跑预检修）；
       重建分支末尾自带 verify(s)，重建+验证一步到位）
    ④首窗回测（calc_dknife_signals_market 全窗口 → _325g_result.json）
全程输出落文件（长跑铁律），任一步 FAIL 即停并留痕。

用法：
  python _325g_final_chain.py --check   # 只跑守卫+打印计划，不动数据
  python _325g_final_chain.py           # 全链执行
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG = os.path.join(HERE, "_325g_chain.log")
BACKFILL_LOG = os.path.join(HERE, "_325b_backfill.log")
RESULT = os.path.join(HERE, "_325g_result.json")
STAMP = time.strftime("%Y-%m-%d %H:%M:%S")

# 子进程解释器钉系统 3.11（pandas/tushare 在位）——sys.executable 继承启动者，
# 托管 3.13 无 pandas（325Gb 干跑预检修判例：20:39 首跑②步 ModuleNotFound rc=1 停链）
PY311 = r"C:\Program Files\Python311\python.exe"
PY = PY311 if os.path.exists(PY311) else sys.executable
if not os.path.exists(PY311) and sys.executable != PY311:
    log("WARN: 系统 3.11 不在位，子进程回退 sys.executable=%s" % sys.executable)


def log(msg):
    line = "[%s] %s" % (STAMP, msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def guard_backfill_quiet():
    """守卫：_325b 日志 15 分钟内仍有写入=回补可能未完，拒绝开链。"""
    if not os.path.exists(BACKFILL_LOG):
        return "SKIP(无 _325b 日志)"
    age = time.time() - os.path.getmtime(BACKFILL_LOG)
    if age < 15 * 60:
        raise SystemExit("GUARD FAIL: _325b_backfill.log %.0f 分钟前仍在写入——"
                         "回补可能未完成，晚点再跑本链" % (age / 60))
    return "PASS(静默 %.1f 小时)" % (age / 3600.0)


def run_step(name, cmd, log_fp):
    log("%s 开始：%s" % (name, " ".join(cmd)))
    with open(log_fp, "ab") as f:
        f.write(("\n==== %s %s ====\n" % (STAMP, name)).encode())
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=HERE)
    log("%s 结束 rc=%d（明细 %s）" % (name, rc, os.path.basename(log_fp)))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    # 主进程自检：第④步进程内 import pandas，须 3.11 启动（fail-closed）
    if not args.check:
        try:
            import pandas  # noqa: F401
        except ImportError:
            raise SystemExit(
                "FAIL: 当前解释器无 pandas（%s）——请用 %s 启动本链"
                % (sys.executable, PY311))

    log("== 325G 衔接链启动 ==")
    g = guard_backfill_quiet()
    log("守卫①回补静默检查: %s" % g)
    log("计划: ②幂等补拉 _325b_backfill.py → ③终版快照 dknife_snap(tushare "
        "202606-202610) → ④首窗 calc_dknife_signals_market(20260601→今日)")
    if args.check:
        log("--check 模式到此为止")
        return

    rc = run_step("②幂等补拉", [PY, "-u", "_325b_backfill.py"],
                  os.path.join(HERE, "_325g_step2_backfill.log"))
    if rc != 0:
        log("FAIL@②补拉 rc=%d——停链（快照不基于残缺数据）" % rc)
        sys.exit(2)

    rc = run_step("③终版快照",
                  [PY, "-u", "dknife_snap.py", "--src", "tushare",
                   "--ym0", "202606", "--ym1", "202610"],
                  os.path.join(HERE, "_325g_step3_snap.log"))
    if rc != 0:
        log("FAIL@③快照 rc=%d——停链" % rc)
        sys.exit(3)

    log("④首窗回测开始（全市场双刀信号 20260601→今日）")
    from backtest_engine_v2 import calc_dknife_signals_market
    today = time.strftime("%Y%m%d")
    sig, pool, meta, err = calc_dknife_signals_market("20260601", today,
                                                      hist_st=True)
    if err:
        log("FAIL@④信号: %s" % err)
        sys.exit(4)
    out = {
        "ts": STAMP,
        "meta": meta,
        "signal_days": len(sig),
        "n_sig_total": meta.get("n_sig"),
        "last_days": {d: sorted(sig[d])[:8] for d in sorted(sig)[-5:]},
    }
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    log("④完成: universe=%s 只·信号日=%s·信号总数=%s → %s"
        % (meta.get("universe"), len(sig), meta.get("n_sig"),
           os.path.basename(RESULT)))
    log("== 325G 衔接链全部完成 ==")


if __name__ == "__main__":
    main()
