# -*- coding: utf-8 -*-
"""股吧深夜窗口采集 · T-190 试验（2026-11-11 v2 计划）。

与 09:30 TargetQueueIntel 的 guba 块并跑（对比实验设计：两窗口拦截率对照，
guba_pw 幂等去重，重复采集无害）。日志独立落 logs/guba_nightly.log。

调度：WorkBuddy 自动化 GubaNightly 每日 23:00。
试验期：11-11 ~ 11-18；11-15 微调日出 3-4 晚初判，11-18 全周结论。
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Program Files\Python311\python.exe"
LOG = os.path.join(HERE, "logs", "guba_nightly.log")

if not sys.stdout.isatty():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stderr = sys.stdout


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _clean_env():
    """剔除代理环境变量（判例 #26 直连；与五 runner 同款）。"""
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main():
    log("===== GubaNightly start %s =====" % time.strftime("%Y-%m-%d"))
    codes = ["--queue"]                       # guba_pw 读 target_queue_chanlun actionable
    n_parts = 0
    rc_all = []
    # --queue 模式 guba_pw 内部自己分批（逐只循环）；单块跑全队列，超时给足
    try:
        r = subprocess.run(
            [PY, os.path.join(HERE, "guba_pw.py")] + codes,
            cwd=HERE, capture_output=True, text=True,
            timeout=1500, env=_clean_env())
        rc_all.append(r.returncode)
        for ln in (r.stdout or "").strip().splitlines()[-6:]:
            log("   | %s" % ln)
        if r.returncode != 0:
            log("   !! rc=%s %s" % (r.returncode, (r.stderr or "")[-200:]))
    except Exception as e:  # noqa: BLE001
        rc_all.append(-1)
        log("   !! exc %s" % e)
    log("===== GubaNightly done rc=%s =====" % rc_all)
    return 0 if all(rc == 0 for rc in rc_all) else 1


if __name__ == "__main__":
    raise SystemExit(main())
