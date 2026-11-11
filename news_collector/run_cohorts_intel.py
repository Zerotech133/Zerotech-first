# -*- coding: utf-8 -*-
"""两池(主选+auto生产池) cohort 出票标的 · 开源情报驱动采集（每日自动）。

来源：portal/daily_report/cohorts/cohort_<asof>_{main,auto}.json
  - main = 主选·三条全中（S01-S11 线策略主选，通常少数几只）
  - auto = auto池·生产（S12 ZKG_AUTO_C20 影子池，因子管线，数十只）
  - asof = 出票信号日，entry_date = 次交易日入场。每个交易日生成新批次。

每次运行：
  1) 取 main 文件中 asof 最新的一批，并配对同 asof 的 auto 批次；
  2) 采集范围 = main ∪ auto 的 A股代码（去后缀）；
  3) 分流调用现有采集器（requests 无头友好，分批容错）：
       .SZ -> irm_collector.py / .SH -> sse_collector.py / 全部 -> taoguba_collector.py(--pages 1)
  4) 写出 target_queue_cohorts.json：两池清单登记 + xq_include_cohorts 开关
     （默认 false —— 是否并入 07:00 雪球有头管线属运营裁定，归上级）；
  5) 刷新 cohorts_watchlist.json + 写日志。

身份边界（OSINT1 马良）：只做收集+治理，不产出研判；两池为影子池/出票记录，
这里补齐的是标的公开情报，非买卖方向。
"""
import os
import sys
import json
import time
import glob
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
COHORT_DIR = os.path.join(REPO, "portal", "daily_report", "cohorts")
PY = r"C:\Program Files\Python311\python.exe"
WATCHLIST = os.path.join(HERE, "cohorts_watchlist.json")
XQ_CQ = os.path.join(HERE, "target_queue_cohorts.json")
LOG = os.path.join(HERE, "logs", "cohorts_intel_daily.log")
SSE_UIDMAP = os.path.join(REPO, "data", "sse", "uid_map.json")
BATCH = 40

os.makedirs(os.path.dirname(LOG), exist_ok=True)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def load_latest_batches():
    """返回 (asof, {"main": codes, "auto": codes})；asof 取 main 文件最大者。"""
    files = glob.glob(os.path.join(COHORT_DIR, "cohort_*_main.json"))
    if not files:
        return None, {}
    best, best_asof = None, ""
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        a = str(d.get("asof", ""))
        if a > best_asof:
            best, best_asof = f, a
    if not best:
        return None, {}
    batches = {}
    for tag in ("main", "auto"):
        f = best.replace("_main.json", "_%s.json" % tag)
        if os.path.exists(f):
            try:
                d = json.load(open(f, encoding="utf-8"))
                batches[tag] = {
                    "file": os.path.basename(f),
                    "pool_label": d.get("pool_label"),
                    "entry_date": d.get("entry_date"),
                    "codes": d.get("codes") or [],
                }
            except Exception as e:  # noqa: BLE001
                log("!! %s 解析失败: %s" % (os.path.basename(f), e))
    return best_asof, batches


def _clean_env():
    """剔除代理环境变量（判例 #26 直连；T-187 2026-11-11，对齐 kstock runner）。"""
    env = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        env.pop(k, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def run_collector(script, codes, extra=None):
    if not codes:
        return 0
    cmd = [PY, os.path.join(HERE, script), ",".join(codes)]
    if extra:
        cmd += extra
    log("→ %s  %d 只: %s" % (script, len(codes), ",".join(codes[:6]) + ("…" if len(codes) > 6 else "")))
    try:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           timeout=1200, env=_clean_env())
        tail = (r.stdout or "").strip().splitlines()[-3:]
        for ln in tail:
            log("   | %s" % ln)
        if r.returncode != 0:
            log("   !! rc=%s  %s" % (r.returncode, (r.stderr or "")[-160:]))
        return r.returncode
    except Exception as e:  # noqa: BLE001
        log("   !! exc %s" % e)
        return -1


def run_batched(script, codes, extra=None):
    bad = 0
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        log("   [batch %d/%d]" % (i // BATCH + 1, (len(codes) + BATCH - 1) // BATCH))
        if run_collector(script, chunk, extra) != 0:
            bad += 1
    return bad


def write_xq_queue(all_codes):
    payload = {
        "asof": "cohorts_two_pools",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "regime": "two_pools_shadow_entries",
        "total": len(all_codes),
        "all": [{"code": c} for c in all_codes],
        "actionable": [],
        "xq_include_cohorts": False,
        "xq_expand_note": (
            "开关置 true 后需在 xq_stock_timeline.load_queue 增加本文件并集；"
            "两池每日滚动换仓，并入 07:00 有头管线前须评估负载，属运营裁定，归上级"
        ),
    }
    with open(XQ_CQ, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    if not sys.stdout.isatty():
        sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stderr = sys.stdout
    log("===== CohortsIntel start %s =====" % time.strftime("%Y-%m-%d"))
    asof, batches = load_latest_batches()
    if not asof or not batches:
        log("!! 未找到 portal/daily_report/cohorts/cohort_*_{main,auto}.json")
        return
    union = sorted({c for b in batches.values() for c in b["codes"] if c})
    log("最新批次 asof=%s : %s" % (asof, {k: len(v["codes"]) for k, v in batches.items()}))
    if not union:
        log("!! 两池代码为空，退出")
        return

    sz = [c[:-3] for c in union if c.endswith(".SZ")]
    sh = [c[:-3] for c in union if c.endswith(".SH")]
    bj = [c[:-3] for c in union if c.endswith(".BJ")]
    ash6 = sz + sh + bj
    log("采集范围并集 %d 只 | 分流 SZ=%d SH=%d BJ=%d" % (len(union), len(sz), len(sh), len(bj)))

    if sh and not os.path.exists(SSE_UIDMAP):
        log("sse uid 映射缺失，先构建（一次性）…")
        try:
            subprocess.run([PY, os.path.join(HERE, "sse_collector.py"), "--uidmap"],
                           cwd=HERE, capture_output=True, text=True, timeout=900)
        except Exception as e:  # noqa: BLE001
            log("   !! uidmap 构建异常 %s（sse 本次可能跳过）" % e)

    bad_irm = run_batched("irm_collector.py", sz)
    bad_sse = run_batched("sse_collector.py", sh)
    bad_tgb = run_batched("taoguba_collector.py", ash6, extra=["--pages", "1"])
    log("三源批次异常数: irm=%d sse=%d taoguba=%d" % (bad_irm, bad_sse, bad_tgb))

    # xq 雪球：两池清单只登记，开关默认关闭（不擅自扩 07:00 有头管线）
    write_xq_queue(union)
    log("xq 队列文件已写出: 两池 %d 只登记(开关 false，未并入 07:00)" % len(union))

    out = {
        "source": "portal/daily_report/cohorts/cohort_*_{main,auto}.json",
        "asof": asof,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "两池为出票记录/影子池入场批次；只补标的公开情报，非研判",
        "pools": batches,
        "n_union": len(union),
        "collected_ashare": ash6,
        "breakdown": {"SZ": sz, "SH": sh, "BJ": bj},
        "coverage_note": (
            "自动覆盖 A股 irm/sse/taoguba（三源 requests 无头）；"
            "xq 雪球仅登记 target_queue_cohorts.json，开关 xq_include_cohorts=false，"
            "是否并入 07:00 有头管线待上级裁定"
        ),
    }
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("watchlist 已刷新: 采集 %d 只 (asof=%s)" % (len(union), asof))
    log("===== CohortsIntel done =====")


if __name__ == "__main__":
    main()
