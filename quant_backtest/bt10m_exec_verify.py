# -*- coding: utf-8 -*-
"""bt10m 执行口径验证入口（一条命令跑完全部验证，2026-11-10 审计后立）。

「以后改执行口径只动 bt10m_sim_core.py 一处，跑一遍验证入口即完成验证。」

三步验证：
  Step 1  bt10m_selftest.py        —— 9 组合成行情用例（秒级）
  Step 2  bt10m_frozen.selfcheck() —— 固化参数在位 + 参照 NAV 对表
  Step 3  回归（两档）：
          默认 quick：仅核对 reports/bt10m_10m_monthly2.json 与
                      bt10m_frozen.REFERENCE_NAV（±0.05pp），不重跑引擎；
          --full    ：真正重跑 bt10m_10m_monthly2.py（7 腿，~75s）+
                      bt10m_10m_monthly.py（4 腿包装层，~60s），再对参照表。

用法：
  python bt10m_exec_verify.py            # 改内核逻辑后的常规验证（~5s）
  python bt10m_exec_verify.py --full     # 改口径/发版前的完整回归（~2.5min）
退出码 0 = 全过；任一步失败即停。
（注：bt10m_verify.py 是另一个工具——1 年数据二次验证，与本入口无关。）
"""
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)


def run_step(name, cmd):
    t0 = time.time()
    print("== %s ==" % name, flush=True)
    r = subprocess.run(cmd, cwd=BASE)
    if r.returncode != 0:
        print("!! %s 失败（rc=%d），验证中止" % (name, r.returncode))
        sys.exit(r.returncode or 1)
    print("== %s 通过（%.1fs）==\n" % (name, time.time() - t0), flush=True)


def check_reference():
    """quick 回归：现有 monthly2 报告 vs 固化参照 NAV（±0.05pp）。"""
    import bt10m_frozen
    rep = os.path.join(BASE, "reports", "bt10m_10m_monthly2.json")
    d = json.load(open(rep, encoding="utf-8"))
    worst, worst_leg = 0.0, ""
    for leg, want in bt10m_frozen.REFERENCE_NAV.items():
        if leg == "bench":
            continue
        got = d["legs"][leg]["final_nav"]
        dev = abs(got - want)
        if dev > worst:
            worst, worst_leg = dev, leg
    assert worst <= 0.05, \
        "回归失败：%s NAV 与参照偏差 %.4fpp（>0.05）" % (worst_leg, worst)
    print("回归：7 腿 NAV 与固化参照最大偏差 %.4fpp（%s）—— PASS"
          % (worst, worst_leg), flush=True)


def main():
    full = "--full" in sys.argv
    t0 = time.time()
    py = sys.executable

    # Step 1：用例
    run_step("Step1 用例 selftest", [py, "bt10m_selftest.py"])

    # Step 2：固化自检
    run_step("Step2 固化 selfcheck", [py, "bt10m_frozen.py"])

    # Step 3：回归
    if full:
        run_step("Step3a 回归 monthly2（7 腿重跑）",
                 [py, "bt10m_10m_monthly2.py"])
        run_step("Step3b 回归 monthly（cand 包装层）",
                 [py, "bt10m_10m_monthly.py"])
    check_reference()
    print("\n验证全过（%s 档，%.1fs）——执行口径未漂移"
          % ("full" if full else "quick", time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
