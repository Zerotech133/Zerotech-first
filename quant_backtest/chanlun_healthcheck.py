# -*- coding: utf-8 -*-
"""缠论系统体检（一条命令可重复执行）。

把此前分散在 chanlun_gate / chanlun_conformance / chanlun_cost_model 三个脚本
里的审计结论固化成一个入口：改完代码或数据，跑一次就知道系统是否仍在健康状态。

体检项
------
H1 闸门复现    六类信号 × 5/10/20 持期，18 格必须全部卡在 G1（判否）。
               任一格通过 G1 = 出现了从未见过的 edge → FAIL（需人工复核）。
H2 口径一致性  抽样 20 只（seed 42）双口径 Jaccard。<0.10 PASS / 0.10~0.90 WARN
               / >0.90 FAIL（口径被意外统一 = 有人动了信号层）。
H3 前视检查    抽 5 只逐日截断重放，lag0 必须为 0。lag0>0 = 前视 → FAIL。
H4 v4 对账     重算 1买 10 日 毛 alpha / FM t / standard 10万 净 alpha，
               与 data/chanlun_cost_model.json 基线比。
               毛 alpha 差 >0.05pp 或 FM t 差 >0.15 → WARN；净 alpha 差 >0.05pp → FAIL。
H5 数据新鲜度  data/ashare/*.parquet 最大 trade_date 距今 >5 自然日 → WARN。

退出码：全 PASS/WARN → 0；有 FAIL → 1。

用法:
    python chanlun_healthcheck.py
    python chanlun_healthcheck.py --workers 8 --out data/chanlun_healthcheck.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import logging
import os
import subprocess
import sys
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor
import atomic_io  # noqa: E402  (P2-c 统一原子写)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
POOL = os.path.join(DATA, "chanlun_pool_ext_daily_full.jsonl")
COST_REF = os.path.join(DATA, "chanlun_cost_model.json")
CONF_REF = os.path.join(DATA, "chanlun_conformance.json")
OUT = os.path.join(DATA, "chanlun_healthcheck.json")
LOG = os.path.join(HERE, "_hc_run.log")

TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]
HOLDS = [5, 10, 20]
WORKERS = 8
MAX_WORKERS = 8

H2_N = 20
H2_SEED = 42
H2_PASS = 0.10           # 低于 = 口径仍分离（已知基线 0.0537）
H2_FAIL = 0.90           # 高于 = 口径被统一
H3_N = 5
H4_TYPE, H4_HOLD, H4_NOTIONAL = "1买", "10", 100000
H4_GROSS_TOL = 0.05      # pp
H4_T_TOL = 0.15
H4_NET_TOL = 0.05        # pp
H4_FM_CONCLUSION = 1.96  # 结论边界：FM |t| 越此线则「无 edge」结论动摇，必须人工复核
H5_MAX_DAYS = 5

log = logging.getLogger("hc")


# ── 小工具 ────────────────────────────────────────────────────
def _pmap(fn, jobs, workers, chunksize=1):
    jobs = list(jobs)
    if not jobs:
        return []
    if len(jobs) == 1 or workers <= 1:
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
        return list(ex.map(fn, jobs, chunksize=chunksize))


def _dw(s):
    """显示宽度（中文按 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, w, left=True):
    s = "" if s is None else str(s)
    gap = max(0, w - _dw(s))
    return (s + " " * gap) if left else (" " * gap + s)


def _r(x, n=3):
    return None if x is None else round(float(x), n)


# ── H1 闸门复现 ───────────────────────────────────────────────
def h1(workers):
    import chanlun_gate as g

    rep = g.run(pool=POOL, holds=HOLDS, types=TYPES, workers=workers,
                dedup=True, quiet=True)
    if rep.get("status") == "no_sample":
        return "FAIL", {"error": "gate 无样本"}, {"cells": 18, "all_stuck_at_g1": True}

    cells = [(t, h, c) for t, blk in rep["types"].items() for h, c in blk.items()]
    n_cells = len(cells)
    g1_pass = ["%s_%s" % (t, h) for t, h, c in cells if c["g1"]["pass"]]
    n_g1 = sum(1 for _, _, c in cells if c["stuck_at"] == "G1")
    ts = [c["g1"]["fm_t"] for _, _, c in cells if c["g1"]["fm_t"] is not None]
    ns = [c["n"] for _, _, c in cells]
    metrics = {
        "n_cells": n_cells,
        "n_stuck_at_g1": n_g1,
        "g1_passed_cells": g1_pass,
        "max_fm_t": max(ts) if ts else None,
        "min_n": min(ns) if ns else None,
        "n_signals": rep.get("n_signals_used"),
        "gate_elapsed_s": rep.get("elapsed_s"),
    }
    baseline = {"n_cells": 18, "all_stuck_at_g1": True,
                "note": "18 格全卡 G1 判否；任一格过 G1 即视为新 edge"}
    ok = (n_cells == 18) and (n_g1 == n_cells) and not g1_pass
    return ("PASS" if ok else "FAIL"), metrics, baseline


# ── H2 口径一致性 ─────────────────────────────────────────────
def h2(workers):
    import chanlun_conformance as cc

    picked, scanned = cc._pick_universe(H2_N, H2_SEED)
    rows = _pmap(cc._stock_job, picked, workers)
    agg = cc._agg_stage1(rows)
    jac = agg.get("jaccard_pooled")
    jac_macro = agg.get("jaccard_macro")
    metrics = {
        "stocks": len(picked),
        "stocks_ok": agg.get("stocks_ok"),
        "files_scanned": scanned,
        "jaccard_pooled": _r(jac, 4),
        "jaccard_macro": _r(jac_macro, 4),
        "n_a": agg.get("n_a"), "n_b": agg.get("n_b"),
        "identical_stocks": agg.get("identical_stocks"),
    }
    baseline = {"jaccard_pooled": 0.0537, "bands": "<0.10 PASS / 0.10~0.90 WARN / >0.90 FAIL"}
    if jac is None:
        return "FAIL", metrics, baseline
    if jac > H2_FAIL:
        st = "FAIL"
    elif jac >= H2_PASS:
        st = "WARN"
    else:
        st = "PASS"
    return st, metrics, baseline


# ── H3 前视检查 ───────────────────────────────────────────────
def h3(workers):
    import chanlun_conformance as cc

    picked, _ = cc._pick_universe(H3_N, H2_SEED)
    rows = _pmap(cc._stage3_job, picked, workers)
    ok = [r for r in rows if not r.get("skip")]
    lag0 = sum(r[s]["lag_zero"] for r in ok for s in ("A", "B"))
    missed = sum(r[s]["lookahead_missed"] for r in ok for s in ("A", "B"))
    n_full = sum(r[s]["n_full"] for r in ok for s in ("A", "B"))
    lag_n = sum(r[s]["lag_n"] for r in ok for s in ("A", "B"))
    metrics = {
        "stocks": len(picked), "stocks_ok": len(ok),
        "lag0": lag0,
        "n_full_signals": n_full,
        "lag_n": lag_n,
        "lookahead_missed": missed,
        "days_replayed": sum(r["days"] for r in ok),
    }
    baseline = {"lag0": 0, "lookahead_missed": 0,
                "note": "lag0>0 表示信号在自己的 dt 当天即可见 = 前视"}
    if not ok:
        return "FAIL", metrics, baseline
    return ("FAIL" if lag0 > 0 else "PASS"), metrics, baseline


# ── H4 v4 成本模型对账 ────────────────────────────────────────
def h4(workers):
    import chanlun_cost_model as cm

    rows = [json.loads(li) for li in open(POOL, encoding="utf-8") if li.strip()]
    # 保留全部类型参与去重（成本模型基线是在六类混排上按 need 去重的），
    # 类型筛选交给 _one 内部，否则 1买 的样本集会与基线不同。
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    # 去重窗口必须与成本模型默认口径一致（need = max(holds) = 20），否则样本集不同
    need = max(HOLDS)
    jobs = [(c, by_code[c], [int(H4_HOLD)], need, True,
             ["standard"], [float(H4_NOTIONAL)], [H4_TYPE]) for c in sorted(by_code)]
    res = []
    for part in _pmap(cm._one, jobs, workers, chunksize=4):
        res.extend(part)

    cells = [r["pol"][H4_HOLD] for r in res if H4_HOLD in r["pol"]]
    if not cells:
        return "FAIL", {"error": "无 1买 10 日样本"}, {}

    gross = cm._blk([c["alpha"] for c in cells])
    fm_t = (cm._fm(res, H4_HOLD) or {}).get("t")
    net_vals = [c["alpha"] - c["cost"]["standard"][str(H4_NOTIONAL)]["t"]
                for c in cells
                if c["alpha"] is not None and c["cost"].get("standard", {}).get(str(H4_NOTIONAL))]
    net = cm._blk(net_vals)

    base = json.load(open(COST_REF, encoding="utf-8"))["main"]["types"][H4_TYPE][H4_HOLD]
    b_std = base["net"]["standard"][str(H4_NOTIONAL)]
    b_gross = (base["gross_alpha"] or {}).get("avg")
    b_t = (base["fm_gross"] or {}).get("t")
    b_net = (b_std.get("net_alpha") or {}).get("avg")
    b_n = base.get("n")

    mine = {"gross_alpha": (gross or {}).get("avg"), "fm_t": fm_t,
            "net_alpha": (net or {}).get("avg"), "n": len(cells)}
    ref = {"gross_alpha": b_gross, "fm_t": b_t, "net_alpha": b_net}
    diff = {k: (None if mine[k] is None or ref[k] is None
                else round(float(mine[k]) - float(ref[k]), 3)) for k in ref}

    metrics = {"type": H4_TYPE, "hold": int(H4_HOLD), "notional": H4_NOTIONAL,
               "n": len(cells), "gross_alpha": mine["gross_alpha"], "fm_t": mine["fm_t"],
               "net_alpha": mine["net_alpha"], "diff": diff}
    baseline = {"n": b_n, "gross_alpha": b_gross, "fm_t": b_t, "net_alpha": b_net,
                "tolerances": {"gross_alpha_pp": H4_GROSS_TOL, "fm_t": H4_T_TOL,
                               "net_alpha_pp": H4_NET_TOL}}

    st, extra = _h4_judge(mine, ref, b_n)
    metrics.update(extra)
    return st, metrics, baseline


def _h4_judge(mine, ref, b_n):
    """H4 判定（纯函数，便于单测）。返回 (status, metrics_extra)。

    判据分层，从严到宽：
      FAIL  净 alpha |Δ| > H4_NET_TOL —— 结论性指标，任何情况下都严格
      WARN  毛 alpha |Δ| > H4_GROSS_TOL —— 均值型指标，样本集变了也应稳定
      WARN  FM |t| >= 1.96 —— 「无 edge」结论动摇，必须人工复核
      PASS  样本集变了（n 不同）且结论未变 —— FM t 漂移源于行情新增交易日
      WARN  样本集未变却 FM t 漂移 > H4_T_TOL —— 真回归
      PASS  其余
    """
    d_net = (None if mine["net_alpha"] is None or ref["net_alpha"] is None
             else round(float(mine["net_alpha"]) - float(ref["net_alpha"]), 3))
    d_g = (None if mine["gross_alpha"] is None or ref["gross_alpha"] is None
           else round(float(mine["gross_alpha"]) - float(ref["gross_alpha"]), 3))
    d_t = (None if mine["fm_t"] is None or ref["fm_t"] is None
           else round(float(mine["fm_t"]) - float(ref["fm_t"]), 3))
    fm_t = mine["fm_t"]

    # 样本集变化感知：基线 FM t 是 n 的强函数。行情每新增一个交易日，就有一批
    # 持仓刚好满持期、从「alpha 算不出」变成「可结算」，样本集扩张，FM t 随之
    # 漂移（实测 2026-11-05 新增一天：n 51,590→51,671，FM t 1.07→0.81）。
    # 这属正常数据增长，不是代码回归——但结论边界仍须死守。
    n_changed = b_n is not None and mine["n"] != b_n
    concl_ok = fm_t is not None and abs(float(fm_t)) < H4_FM_CONCLUSION
    extra = {"n_baseline": b_n, "sample_set_changed": bool(n_changed),
             "conclusion_holds": bool(concl_ok)}

    if d_net is not None and abs(d_net) > H4_NET_TOL:
        return "FAIL", extra
    if d_g is not None and abs(d_g) > H4_GROSS_TOL:
        return "WARN", extra
    if not concl_ok:
        return "WARN", extra          # 结论动摇，须人工复核
    if n_changed:
        extra["note"] = ("样本集 n %s→%s（行情新增交易日所致），FM t %s→%s 属正常漂移；"
                         "净 alpha Δ=%+0.3fpp 仍在容差内，FM |t|<1.96 结论未变"
                         % (b_n, mine["n"], ref["fm_t"], fm_t, d_net))
        return "PASS", extra
    if d_t is not None and abs(d_t) > H4_T_TOL:
        return "WARN", extra          # 样本集未变却漂了 → 真回归
    return "PASS", extra


# ── H5 数据新鲜度 ─────────────────────────────────────────────
def h5(_workers=1):
    files = sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
    if not files:
        return "FAIL", {"n_files": 0, "error": "data/ashare 无 parquet"}, {"max_days_lag": 0}

    import pyarrow.parquet as pq

    mx, n_ok, n_miss = None, 0, 0
    for f in files:
        try:
            pf = pq.ParquetFile(f)
            ci = pf.metadata.schema.names.index("trade_date")
            st = pf.metadata.row_group(0).column(ci).statistics
            if st is None or st.max is None:
                n_miss += 1
                continue
            v = int(st.max)
            n_ok += 1
            if mx is None or v > mx:
                mx = v
        except Exception:
            n_miss += 1
    if mx is None:
        return "FAIL", {"n_files": len(files), "error": "无法读取 trade_date"}, {}

    d = _dt.datetime.strptime(str(mx), "%Y%m%d").date()
    lag = (_dt.date.today() - d).days
    metrics = {"n_files": len(files), "n_read_ok": n_ok, "n_no_stat": n_miss,
               "max_trade_date": d.isoformat(), "days_lag": lag}
    baseline = {"max_days_lag": H5_MAX_DAYS}
    return ("WARN" if lag > H5_MAX_DAYS else "PASS"), metrics, baseline


# ── 输出 ──────────────────────────────────────────────────────
CHECKS = [
    ("H1", "闸门复现", h1),
    ("H2", "口径一致性", h2),
    ("H3", "前视检查", h3),
    ("H4", "v4 对账", h4),
    ("H5", "数据新鲜度", h5),
]


def _brief(code, m):
    if code == "H1":
        return "18格卡G1 %d/%d  最大FM_t=%s  n=%s" % (
            m.get("n_stuck_at_g1", 0), m.get("n_cells", 0), m.get("max_fm_t"), m.get("n_signals"))
    if code == "H2":
        return "Jaccard=%s  票=%s  信号A/B=%s/%s" % (
            m.get("jaccard_pooled"), m.get("stocks_ok"), m.get("n_a"), m.get("n_b"))
    if code == "H3":
        return "lag0=%s  missed=%s  全历史信号=%s" % (
            m.get("lag0"), m.get("lookahead_missed"), m.get("n_full_signals"))
    if code == "H4":
        d = m.get("diff") or {}
        return "毛a=%+.3f FM_t=%.2f 净a=%+.3f Δ净=%s" % (
            m.get("gross_alpha") or 0, m.get("fm_t") or 0, m.get("net_alpha") or 0, d.get("net_alpha"))
    if code == "H5":
        return "最新=%s  滞后=%s天  文件=%s" % (
            m.get("max_trade_date"), m.get("days_lag"), m.get("n_files"))
    return ""


def _base(code, b):
    if code == "H1":
        return "18/18 卡G1"
    if code == "H2":
        return "0.0537 (<0.10)"
    if code == "H3":
        return "lag0=0"
    if code == "H4":
        return "毛%+.3f FM_t=%.2f 净%+.3f" % (
            b.get("gross_alpha") or 0, b.get("fm_t") or 0, b.get("net_alpha") or 0)
    if code == "H5":
        return "<=5天"
    return ""


def main():
    ap = argparse.ArgumentParser(description="缠论系统体检（一条命令）")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--skip", default="", help="跳过体检项，如 H4")
    a = ap.parse_args()
    workers = max(1, min(MAX_WORKERS, a.workers))
    skip = {s.strip().upper() for s in a.skip.split(",") if s.strip()}

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG, mode="w", encoding="utf-8")],
        force=True,
    )

    t_all = time.time()
    log.info("healthcheck start workers=%d", workers)
    results = {}
    for code, name, fn in CHECKS:
        if code in skip:
            continue
        t0 = time.time()
        log.info("%s %s start", code, name)
        try:
            st, metrics, baseline = fn(workers)
        except Exception as exc:  # 单项炸掉不影响其它项，如实记 FAIL
            log.exception("%s failed", code)
            st, metrics, baseline = "FAIL", {"error": "%s: %s" % (type(exc).__name__, exc)}, {}
        el = round(time.time() - t0, 1)
        results[code] = {"item": name, "status": st, "metrics": metrics,
                         "baseline": baseline, "elapsed_s": el}
        log.info("%s %s -> %s (%.1fs) %s", code, name, st, el,
                 json.dumps(metrics, ensure_ascii=False)[:400])

    order = {"PASS": 0, "WARN": 1, "FAIL": 2}
    overall = "PASS"
    for r in results.values():
        if order[r["status"]] > order[overall]:
            overall = r["status"]
    total = round(time.time() - t_all, 1)

    # 控制台表格
    w1, w2, w3, w4 = 16, 6, 44, 26
    print("%s %s %s %s" % (_pad("体检项", w1), _pad("状态", w2),
                           _pad("关键数字", w3), _pad("基线值", w4)))
    print("-" * (w1 + w2 + w3 + w4 + 3))
    for code, name, _fn in CHECKS:
        r = results.get(code)
        if not r:
            continue
        label = "%s %s" % (code, name)
        print("%s %s %s %s" % (_pad(label, w1), _pad(r["status"], w2),
                               _pad(_brief(code, r["metrics"]), w3),
                               _pad(_base(code, r["baseline"]), w4)))
    print("-" * (w1 + w2 + w3 + w4 + 3))
    print("总判定: %s   耗时 %.1fs   明细 -> %s" % (overall, total, a.out))

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": total,
        "workers": workers,
        "checks": results,
        "overall": overall,
        "exit_code": 1 if overall == "FAIL" else 0,
        "thresholds": {
            "H2_jaccard": {"pass_below": H2_PASS, "fail_above": H2_FAIL},
            "H3_lag0": 0,
            "H4": {"gross_pp": H4_GROSS_TOL, "fm_t": H4_T_TOL, "net_pp": H4_NET_TOL},
            "H5_days": H5_MAX_DAYS,
        },
    }
    with atomic_io.replacing(a.out) as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # 滚动运行日志：每次追加一行摘要。计划任务直接调 python.exe，不用
    # bat/cmd 做重定向——本仓路径含中文，走 shell 会有编码坑。
    try:
        hist = os.path.join(os.path.dirname(a.out) or ".",
                            "chanlun_healthcheck_log.jsonl")
        with open(hist, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": out["generated_at"],
                "overall": overall,
                "elapsed_s": total,
                "fail": [k for k, v in results.items() if v["status"] == "FAIL"],
                "warn": [k for k, v in results.items() if v["status"] == "WARN"],
            }, ensure_ascii=False) + "\n")
    except Exception as exc:  # 日志失败不影响体检结论
        log.warning("append history failed: %r", exc)

    log.info("overall=%s elapsed=%.1fs", overall, total)

    # FAIL 走统一告警通道：alert_fail.py 复用 news_collector/send_feishu.py，
    # 自身已做「告警失败不反噬任务」处理。CHANLUN_HC_NO_ALERT=1 可静默
    # （自测或批量重跑时用），避免刷屏。
    if out["exit_code"] and not os.environ.get("CHANLUN_HC_NO_ALERT"):
        try:
            alert = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "alert_fail.py")
            if os.path.exists(alert):
                subprocess.run(
                    [sys.executable, alert, "ChanlunHealthcheck",
                     str(out["exit_code"]), LOG],
                    capture_output=True, text=True, timeout=90,
                )
                log.info("alert_fail dispatched")
            else:
                log.warning("alert_fail.py 缺失，跳过告警")
        except Exception as exc:  # 告警不能反噬体检
            log.warning("alert dispatch failed: %r", exc)

    return out["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
