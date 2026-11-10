# -*- coding: utf-8 -*-
"""侯风(HOUFENG) 选股策略产出标的 · 开源情报驱动采集（每日自动）。

每次运行：
  1) 自动定位 portal/houfeng/ 下最新的 houfeng_pick_*.json（按 signal_date 取最新），
     抽取策略选股产出（pool_off.picks，关闸期即"影子池/观察名单"，top_n=30）；
  2) 分离 A股(.SZ/.SH/.BJ)；
  3) A股(去后缀) 按市场分流调用现有采集器（requests 无头友好，计划任务可跑）：
       .SZ -> irm_collector.py         深交所互动易（官方问答维度）
       .SH -> sse_collector.py         上证 e互动（官方问答维度）
       全部A股 -> taoguba_collector.py  淘股吧（游资/短线情绪维度）
     - 北交所(.BJ, 920599) 无 irm/sse 官方源，仅入 taoguba（雪球 xq 对 BJ 前缀处理有限，见下）；
  4) 写出 target_queue_houfeng.json，供 xq_stock_timeline.py 的 load_queue 并集合并
     → 现有 XqTimelineDaily(07:00) 自动补齐侯风 30 只的雪球舆情（无需改 run_xq_daily.py）；
  5) 刷新 news_collector/houfeng_watchlist.json (asof) + 写日志。

说明（与 k_stock 桥平行，身份边界一致）：本席只做"收集+治理"，不产出研判。
侯风当前为关闸期影子池（gate_safe=false，paper 模式只记录不下场，实持仓 0），
故这里补齐的是"选股清单的公开情报"，非持仓标的。
"""
import os
import sys
import json
import time
import glob
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOUFENG_DIR = os.path.join(REPO, "portal", "houfeng")
PY = r"C:\Program Files\Python311\python.exe"
WATCHLIST = os.path.join(HERE, "houfeng_watchlist.json")
XQ_HF_QUEUE = os.path.join(HERE, "target_queue_houfeng.json")
LOG = os.path.join(HERE, "logs", "houfeng_intel_daily.log")
SSE_UIDMAP = os.path.join(REPO, "data", "sse", "uid_map.json")

os.makedirs(os.path.dirname(LOG), exist_ok=True)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def find_latest_pick():
    """返回 (path, signal_date_str) 取 signal_date 最大者；失败回退 mtime。"""
    files = glob.glob(os.path.join(HOUFENG_DIR, "houfeng_pick_*.json"))
    if not files:
        return None, None
    best = None
    best_key = None
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
            sd = str(d.get("meta", {}).get("signal_date", ""))
        except Exception:
            sd = ""
        key = (sd, os.path.getmtime(f))
        if best is None or key > best_key:
            best, best_key = f, key
    return best, best_key[0] if best_key else None


def build_watchlist():
    path, sd = find_latest_pick()
    if not path:
        return None, None, []
    d = json.load(open(path, encoding="utf-8"))
    picks = (d.get("pool_off") or {}).get("picks") or []
    # 兜底：若 pool_off 为空，退回 pool_on / 顶层 picks
    if not picks:
        picks = (d.get("pool_on") or {}).get("picks") or d.get("picks") or []
    codes = [p.get("code") for p in picks if p.get("code")]
    codes = sorted(set(codes))
    return path, sd, picks


def run_collector(script, codes, extra=None):
    if not codes:
        return 0
    cmd = [PY, os.path.join(HERE, script), ",".join(codes)]
    if extra:
        cmd += extra
    log("→ %s  %s" % (script, ",".join(codes)))
    try:
        r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, timeout=900)
        tail = (r.stdout or "").strip().splitlines()[-4:]
        for ln in tail:
            log("   | %s" % ln)
        if r.returncode != 0:
            log("   !! rc=%s  %s" % (r.returncode, (r.stderr or "")[-160:]))
        return r.returncode
    except Exception as e:  # noqa: BLE001
        log("   !! exc %s" % e)
        return -1


def write_xq_queue(codes):
    payload = {
        "asof": "houfeng_pick",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "regime": "houfeng_strategy_picks",
        "total": len(codes),
        "all": [{"code": c} for c in codes],
        "actionable": [{"code": c} for c in codes],
    }
    with open(XQ_HF_QUEUE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    if not sys.stdout.isatty():
        sys.stdout = open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stderr = sys.stdout
    log("===== HoufengIntel start %s =====" % time.strftime("%Y-%m-%d"))
    path, sd, picks = build_watchlist()
    if not path:
        log("!! 未找到 portal/houfeng/houfeng_pick_*.json")
        return
    log("选股源: %s (signal_date=%s) 选股 %d 只" % (os.path.basename(path), sd, len(picks)))

    codes = sorted({p["code"] for p in picks if p.get("code")})
    sz = [c[:-3] for c in codes if c.endswith(".SZ")]
    sh = [c[:-3] for c in codes if c.endswith(".SH")]
    bj = [c[:-3] for c in codes if c.endswith(".BJ")]
    ash6 = sz + sh + bj  # 淘股吧全 A股（含 BJ）
    log("分流: SZ=%d SH=%d BJ=%d | 淘股吧=%d" % (len(sz), len(sh), len(bj), len(ash6)))

    # sse 首次需建 uid 映射（一次性，约 70 页遍历）
    if sh and not os.path.exists(SSE_UIDMAP):
        log("sse uid 映射缺失，先构建（一次性）…")
        try:
            subprocess.run([PY, os.path.join(HERE, "sse_collector.py"), "--uidmap"],
                          cwd=HERE, capture_output=True, text=True, timeout=900)
        except Exception as e:  # noqa: BLE001
            log("   !! uidmap 构建异常 %s（sse 本次可能跳过）" % e)

    run_collector("irm_collector.py", sz)
    run_collector("sse_collector.py", sh)
    # 淘股吧个股通常仅 1 页（最热帖），翻第 2 页会 404；限定 --pages 1
    run_collector("taoguba_collector.py", ash6, extra=["--pages", "1"])

    # 雪球 xq：写出队列文件，由 XqTimelineDaily(07:00) 并集合并补齐
    write_xq_queue(codes)
    log("xq 侯风队列已写出: %s (%d 标的)" % (XQ_HF_QUEUE, len(codes)))

    out = {
        "source": "portal/houfeng/houfeng_pick_*.json (pool_off.picks)",
        "pick_file": os.path.basename(path),
        "signal_date": sd,
        "asof": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gate_safe": False,
        "note": "关闸期影子池/观察名单，paper 模式只记录不下场，实持仓 0；补齐的是选股清单公开情报",
        "picks": [
            {"rank": p.get("rank"), "code": p.get("code"), "name": p.get("name"),
             "board": p.get("board"), "score": p.get("score")}
            for p in picks
        ],
        "collected_ashare": ash6,
        "breakdown": {"SZ": sz, "SH": sh, "BJ": bj},
        "coverage_note": (
            "自动覆盖 A股 irm(深交所互动易)/sse(上证e互动)/taoguba(淘股吧); "
            "雪球 xq 经 target_queue_houfeng.json 并入 XqTimelineDaily(07:00) 全队列补齐; "
            "BJ(920599) 无 irm/sse 官方源且 xq 前缀处理有限，仅入 taoguba"
        ),
    }
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("watchlist 已刷新: %s (%d 标的)" % (WATCHLIST, len(codes)))
    log("===== HoufengIntel done =====")


if __name__ == "__main__":
    main()
