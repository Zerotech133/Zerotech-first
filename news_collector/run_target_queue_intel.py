# -*- coding: utf-8 -*-
"""缠论 target_queue 标的驱动四源采集 · 每日自动（2026-11-11）。

补齐 COLLECT_REGISTER 中四个「手动」采集器的日更缺口：
读 target_queue_chanlun.json actionable 队列，分流调用：
  .SZ -> irm_collector.py    深交所互动易（requests 无头）
  .SH -> sse_collector.py    上证 e互动（requests 无头）
  全部 -> taoguba_collector.py 淘股吧（requests SSR 直出，--pages 1）
  全部 -> guba_pw.py         东财股吧（Playwright 渲染，仅第 1 页）

股吧口径（2026-11-11 实测）：东财对「身份核实」拦截页是 IP/会话级风控，
有头真实 Chromium 同样复现——不硬闯、不换出口（旧 403 换口逻辑禁止复用），
no_marker 优雅跳过不写库，风控解除后自动恢复；拦截率逐轮入账，连续
拦截 >= INTERCEPT_ALERT_DAYS 天在日志打 ALERT 供人工介入。

调度：WorkBuddy 自动化 TargetQueueIntel 每日 09:30（错开晨链
06:00-08:30：XqTimeline 07:00 / KStockIntel 07:30 / SentimentSnapshot 08:00 /
SentimentBT 07:20 之后）。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"C:\Program Files\Python311\python.exe"
QUEUE_FILE = os.path.join(HERE, "target_queue_chanlun.json")
REPO = os.path.dirname(HERE)
SSE_UIDMAP = os.path.join(REPO, "data", "sse", "uid_map.json")
LOG = os.path.join(HERE, "logs", "target_queue_intel_daily.log")
INTERCEPT_ALERT_DAYS = 3

if not sys.stdout.isatty():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stderr = sys.stdout


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def _clean_env():
    """剔除代理环境变量（判例 #26 直连；沙箱会话代理会让子进程秒挂）。"""
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


def run_collector(script, codes, extra=None, timeout=1200):
    if not codes:
        return 0
    cmd = [PY, os.path.join(HERE, script), ",".join(codes)]
    if extra:
        cmd += extra
    log("→ %s  %d 只  %s" % (script, len(codes),
                             ",".join(codes[:8]) + ("…" if len(codes) > 8 else "")))
    try:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           timeout=timeout, env=_clean_env())
        tail = (r.stdout or "").strip().splitlines()[-3:]
        for ln in tail:
            log("   | %s" % ln)
        if r.returncode != 0:
            log("   !! rc=%s  %s" % (r.returncode, (r.stderr or "")[-160:]))
        return r.returncode
    except Exception as e:  # noqa: BLE001
        log("   !! exc %s" % e)
        return -1


def queue_codes():
    q = json.load(open(QUEUE_FILE, encoding="utf-8"))
    import re
    out = []
    for it in q.get("actionable", []):
        m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", it.get("code", ""), re.I)
        if m:
            out.append((m.group(1), m.group(2).upper()))
    # 去重保序
    seen = set()
    uniq = []
    for c6, mkt in out:
        if c6 not in seen:
            seen.add(c6)
            uniq.append((c6, mkt))
    return uniq


def main():
    log("===== TargetQueueIntel start %s =====" % time.strftime("%Y-%m-%d"))
    codes = queue_codes()
    if not codes:
        log("actionable 队列为空，本轮跳过")
        return
    sz = [c for c, m in codes if m == "SZ"]
    sh = [c for c, m in codes if m == "SH"]
    bj = [c for c, m in codes if m == "BJ"]
    all6 = [c for c, _ in codes]
    log("队列 %d 只（SZ %d / SH %d / BJ %d）" % (len(codes), len(sz), len(sh), len(bj)))

    if sh and not os.path.exists(SSE_UIDMAP):
        log("sse uid 映射缺失，先构建（一次性）…")
        run_collector("sse_collector.py", ["--uidmap"])

    for part in chunked(sz, 50):
        run_collector("irm_collector.py", part)
    for part in chunked(sh, 50):
        run_collector("sse_collector.py", part)
    for part in chunked(all6, 40):
        run_collector("taoguba_collector.py", part, extra=["--pages", "1"])
    # 股吧：逐块跑，单块超时收紧；拦截页 no_marker 时 collector 自行跳过
    guba_rc = []
    for part in chunked(all6, 30):
        guba_rc.append(run_collector("guba_pw.py", part, timeout=1500))
    if all(rc != 0 for rc in guba_rc):
        log("ALERT_CANDIDATE: 股吧本轮全部失败（若连续 %d 天需人工介入风控）"
            % INTERCEPT_ALERT_DAYS)
    log("===== TargetQueueIntel done =====")


if __name__ == "__main__":
    main()
