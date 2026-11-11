# -*- coding: utf-8 -*-
"""侯风(HOUFENG) 选股策略产出标的 · 开源情报驱动采集（每日自动）。

每次运行：
  1) 自动定位 portal/houfeng/ 下最新的 houfeng_pick_*.json（按 signal_date 取最新），
     抽取策略选股产出（pool_off.picks，关闸期即"影子池/观察名单"，top_n=30）；
  2) 读取 houfeng_candidates_164.json —— 原始候选池（影子池全量，11-06 信号日
     冻结快照；由 houfeng_cand164_replay.py 双金标准只读复现产出，非每日重算）；
     仅当 gold_checks 全部为真且 n_candidates 与清单长度一致才采用（治理校验）；
  3) 采集范围 = 最新选股 ∪ 候选池（当前 30 ⊂ 候选池，故为候选池全量）；
     分离 A股(.SZ/.SH/.BJ)；
  4) A股(去后缀) 按市场分流调用现有采集器（requests 无头友好，计划任务可跑），
     分批调用避免单次命令过长 / 超时：
       .SZ -> irm_collector.py         深交所互动易（官方问答维度）
       .SH -> sse_collector.py         上证 e互动（官方问答维度）
       全部A股 -> taoguba_collector.py  淘股吧（游资/短线情绪维度）
     - 北交所(.BJ, 920599) 无 irm/sse 官方源，仅入 taoguba；
  5) 写出 target_queue_houfeng.json（xq 雪球队列）：
     - all/actionable 保持"最新选股"口径（30 只），不擅自扩大 07:00 有头采集管线；
     - 另登记 candidate_pool 全量清单 + xq_include_candidates 开关（默认 false）；
       开关置 true 后 xq_stock_timeline.load_queue 才会把候选池并入
       （当前 489 → 593，净增 104 只，属运营裁定，归上级）；
  6) 刷新 news_collector/houfeng_watchlist.json (asof) + 写日志。

说明（与 k_stock 桥平行，身份边界一致）：本席只做"收集+治理"，不产出研判。
侯风当前为关闸期影子池（gate_safe=false，paper 模式只记录不下场，实持仓 0），
故这里补齐的是"选股清单/候选池的公开情报"，非持仓标的，也非买卖方向。
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
CAND_POOL = os.path.join(HERE, "houfeng_candidates_164.json")
LOG = os.path.join(HERE, "logs", "houfeng_intel_daily.log")
SSE_UIDMAP = os.path.join(REPO, "data", "sse", "uid_map.json")
BATCH = 40  # 单批代码数上限（164 只 × 7 字符 ≈ 1.1KB，远低于命令行上限；分批主要为容错/超时）

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
    return path, sd, picks


def load_candidate_pool():
    """读取只读复现产出的候选池；治理校验不过则返回 (None, 原因)。

    不硬编码候选数量（编号字面量在传输通道可能串味），改为：
      - gold_checks 两项必须为真 bool；
      - n_candidates 必须等于 candidates 数组实际长度；
      - schema 必须为 houfeng.candidate_pool/v1。
    """
    if not os.path.exists(CAND_POOL):
        return None, "候选池文件不存在"
    try:
        d = json.load(open(CAND_POOL, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return None, "候选池解析失败: %s" % e
    if d.get("schema") != "houfeng.candidate_pool/v1":
        return None, "schema 不符"
    g = d.get("gold_checks") or {}
    if g.get("candidate_count_164") is not True or g.get("top30_exact_match") is not True:
        return None, "金标准校验未通过或缺失"
    rows = d.get("candidates") or []
    if d.get("n_candidates") != len(rows):
        return None, "n_candidates 与清单长度不一致"
    codes = sorted({r["code"] for r in rows if r.get("code")})
    return {"file": os.path.basename(CAND_POOL),
            "signal_date": d.get("signal_date"),
            "codes": codes,
            "n": len(codes)}, None


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
    """分批调用采集器；返回非零批次数。"""
    bad = 0
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        log("   [batch %d/%d]" % (i // BATCH + 1, (len(codes) + BATCH - 1) // BATCH))
        if run_collector(script, chunk, extra) != 0:
            bad += 1
    return bad


def write_xq_queue(pick_codes, cand_codes):
    payload = {
        "asof": "houfeng_pick",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "regime": "houfeng_strategy_picks",
        "total": len(pick_codes),
        "all": [{"code": c} for c in pick_codes],
        "actionable": [{"code": c} for c in pick_codes],
        # 候选池全量清单仅登记，默认不并入 07:00 有头采集（运营裁定归上级）
        "candidate_pool": [{"code": c} for c in cand_codes],
        "candidate_pool_signal_date": "20261106",
        "xq_include_candidates": False,
        "xq_expand_note": (
            "开关置 true 后 load_queue 会并入 candidate_pool；"
            "当前 all=489，并入后 593（净增 104 只有头采集），须上级裁定"
        ),
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
    pick_codes = sorted({p["code"] for p in picks if p.get("code")})
    log("选股源: %s (signal_date=%s) 选股 %d 只" % (os.path.basename(path), sd, len(picks)))

    cand, err = load_candidate_pool()
    if cand:
        log("候选池: %s (signal_date=%s) %d 只 [金标准校验通过]"
            % (cand["file"], cand["signal_date"], cand["n"]))
        cand_codes = cand["codes"]
    else:
        log("!! 候选池不可用(%s) → 本次仅按选股清单采集" % err)
        cand_codes = []

    all_codes = sorted(set(pick_codes) | set(cand_codes))
    new_vs_pick = sorted(set(all_codes) - set(pick_codes))
    log("采集范围: 选股 %d + 候选池净增 %d = 合计 %d 只"
        % (len(pick_codes), len(new_vs_pick), len(all_codes)))

    sz = [c[:-3] for c in all_codes if c.endswith(".SZ")]
    sh = [c[:-3] for c in all_codes if c.endswith(".SH")]
    bj = [c[:-3] for c in all_codes if c.endswith(".BJ")]
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

    bad_irm = run_batched("irm_collector.py", sz)
    bad_sse = run_batched("sse_collector.py", sh)
    # 淘股吧个股通常仅 1 页（最热帖），翻第 2 页会 404；限定 --pages 1
    bad_tgb = run_batched("taoguba_collector.py", ash6, extra=["--pages", "1"])
    log("三源批次异常数: irm=%d sse=%d taoguba=%d" % (bad_irm, bad_sse, bad_tgb))

    # 雪球 xq：选股清单入队列；候选池只登记，开关默认关闭（不擅自扩有头管线）
    write_xq_queue(pick_codes, cand_codes)
    log("xq 队列已写出: 选股 %d 只入 all；候选池 %d 只登记(开关 false，未并入 07:00)"
        % (len(pick_codes), len(cand_codes)))

    out = {
        "source": "portal/houfeng/houfeng_pick_*.json (pool_off.picks)",
        "pick_file": os.path.basename(path),
        "signal_date": sd,
        "asof": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gate_safe": False,
        "note": "关闸期影子池/观察名单，paper 模式只记录不下场，实持仓 0；补齐的是选股清单/候选池公开情报",
        "picks": [
            {"rank": p.get("rank"), "code": p.get("code"), "name": p.get("name"),
             "board": p.get("board"), "score": p.get("score")}
            for p in picks
        ],
        "candidate_pool": {
            "file": cand["file"] if cand else None,
            "signal_date": cand["signal_date"] if cand else None,
            "n_candidates": cand["n"] if cand else 0,
            "net_new_vs_picks": len(new_vs_pick),
            "gold_checks_passed": bool(cand),
            "note": "11-06 信号日冻结快照（只读复现），非每日重算；如需刷新须重跑 houfeng_cand164_replay.py",
            "xq_queue_status": "已登记 target_queue_houfeng.json.candidate_pool，开关 xq_include_candidates=false（未并入 07:00，待上级裁定）",
        },
        "collected_ashare": ash6,
        "breakdown": {"SZ": sz, "SH": sh, "BJ": bj},
        "coverage_note": (
            "自动覆盖 A股 irm(深交所互动易)/sse(上证e互动)/taoguba(淘股吧)，范围为选股∪候选池全量; "
            "雪球 xq 当前仅最新选股 30 只经 target_queue_houfeng.json 并入 XqTimelineDaily(07:00); "
            "BJ(920599) 无 irm/sse 官方源且 xq 前缀处理有限，仅入 taoguba"
        ),
    }
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("watchlist 已刷新: 采集 %d 只 / 选股 %d 只" % (len(all_codes), len(pick_codes)))
    log("===== HoufengIntel done =====")


if __name__ == "__main__":
    main()
