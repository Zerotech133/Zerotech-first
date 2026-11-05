# -*- coding: utf-8 -*-
"""缠论双口径一致性审计（P0）· 只读审计，不修复任何生产模块。

背景：同一模块库里存在两条互不相通的信号判定路径，历史上从未做过一致性
校验——
  A 口径 = chanlun_bi.analyze(bars, closes=...)   中枢法（K151-154）
      signals 元素字段实测为 {"d","p","t","sub"}，t ∈ b1/b2/b3/s1/s2/s3
      （接口文档写的 dt/type 与实现不符，本脚本以实测字段为准）
  B 口径 = chanlun_signal.analyze(bars)            线段法
      signals 元素字段实测为 {"type","dt","price","note",...}

三个阶段：
  阶段一 全历史一次性对比（100 只，seed=42，清洗后 ≥500 根）
  阶段二 分歧四类归因（每一条分歧归入且仅归入一类，合计 100%）
  阶段三 逐日滚动重放（20 只，每个交易日 bars[:t+1] 截断重放两套口径）

约束：本文件不修改任何 chanlun_*.py；输出 data/chanlun_conformance.json。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import chanlun_scan  # noqa: E402
import chanlun_bi  # noqa: E402
import chanlun_signal  # noqa: E402

SEED = 42
N_STAGE1 = 100
MIN_BARS = 500
WORKERS = 10
N_STAGE3 = 20
STAGE3_WARMUP = 120  # 在线重放至少需要的 K 线根数
OUT_JSON = os.path.join(HERE, "data", "chanlun_conformance.json")

TYPES = ["1买", "2买", "3买", "1卖", "2卖", "3卖"]
MAP_A = {"b1": "1买", "b2": "2买", "b3": "3买", "s1": "1卖", "s2": "2卖", "s3": "3卖"}
_DT_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

log = logging.getLogger("conf")


# ---------------------------------------------------------------- 数据加载
def _load(path):
    """parquet → 清洗后 bars（额外剔除非法 dt 行，两套口径共用同一份数据）。

    _bars_from_parquet 只按字符串升序排 dt、不校验日历合法性，脏行（如
    '2026-10-16'）会排到序列末尾并污染结构，故此处补一道日历过滤。
    """
    bars = chanlun_scan._bars_from_parquet(path)
    if not bars:
        return None, 0
    keep = [b for b in bars if _DT_RE.match(b["dt"])]
    return keep, len(bars) - len(keep)


def _norm_a(st):
    out = []
    for s in st.get("signals") or []:
        t = MAP_A.get(s.get("t"))
        if t is None:
            continue
        out.append({"dt": s["d"], "type": t, "price": s.get("p"), "sub": s.get("sub")})
    return out


def _norm_b(st):
    out = []
    for s in st.get("signals") or []:
        if s.get("type") not in TYPES:
            continue
        out.append({"dt": s["dt"], "type": s["type"], "price": s.get("price")})
    return out


def _pairs(sigs):
    return {(s["dt"], s["type"]) for s in sigs}


def _run_both(bars):
    closes = [b["c"] for b in bars]
    A = chanlun_bi.analyze(bars, closes=closes)
    B = chanlun_signal.analyze(bars)
    return A, B


# ---------------------------------------------------------------- 阶段一/二
def _stock_job(args):
    code, path = args
    t0 = time.time()
    try:
        bars, dropped = _load(path)
        if not bars or len(bars) < MIN_BARS:
            return {"code": code, "skip": "bars<%d" % MIN_BARS, "n_bars": len(bars or [])}
        A, B = _run_both(bars)
        sa, sb = _norm_a(A), _norm_b(B)
        pa, pb = _pairs(sa), _pairs(sb)
        dts = [b["dt"] for b in bars]
        idx = {d: i for i, d in enumerate(dts)}

        inter, union = pa & pb, pa | pb
        per_type = {}
        for t in TYPES:
            a = {p for p in pa if p[1] == t}
            b = {p for p in pb if p[1] == t}
            i = a & b
            per_type[t] = {
                "n_a": len(a),
                "n_b": len(b),
                "hit": len(i),
                "jaccard": (len(i) / len(a | b)) if (a | b) else None,
                "recall_a": (len(i) / len(a)) if a else None,  # A 被 B 复现率
                "precision_ba": (len(i) / len(b)) if b else None,  # B 中 A 认可率
            }

        # 结构层一致性（中枢/线段/笔是否为同一份）
        pivot_eq = A["pivots"] == B["pivots"]
        seg_eq = A["segments"] == B["segments"]
        bi_eq = A["bis"] == B["bis"]

        # 结构不一致区间（仅当结构层不等时才存在，用于归因 1/2）
        def _diff_windows(ma, mb):
            ka = {(x["s_dt"], x["e_dt"]) for x in ma}
            kb = {(x["s_dt"], x["e_dt"]) for x in mb}
            wins = []
            for s_dt, e_dt in (ka ^ kb):
                i0 = idx.get(s_dt, 0)
                i1 = idx.get(e_dt, i0)
                wins.append((i0 - 5, i1 + 5))
            return wins

        pv_win = [] if pivot_eq else _diff_windows(A["pivots"], B["pivots"])
        sg_win = [] if seg_eq else _diff_windows(A["segments"], B["segments"])

        def _in(wins, i):
            return any(a <= i <= b for a, b in wins)

        # 阶段二：对称差中的每一条，归入且仅归入一类
        # 1 中枢定义差 > 2 线段终结差 > 3 确认滞后(≤3日；4-10日记 3b) > 4 覆盖缺口(>10日或无)
        attr = {"1_中枢定义差": 0, "2_线段终结差": 0, "3_确认滞后差": 0, "4_覆盖缺口": 0}
        sub3 = {"3a_≤3日": 0, "3b_4-10日": 0}
        attr_rows = []
        gap_dists = []
        for p in sorted(pa ^ pb):
            dt, tp = p
            side = "A_only" if p in pa else "B_only"
            other = pb if side == "A_only" else pa
            i = idx.get(dt)
            if i is None:
                continue
            cls = None
            if pv_win and _in(pv_win, i):
                cls = "1_中枢定义差"
            elif sg_win and _in(sg_win, i):
                cls = "2_线段终结差"
            else:
                ds = [abs(idx[q[0]] - i) for q in other if q[1] == tp and q[0] in idx]
                near = min(ds) if ds else None
                if near is not None and near <= 3:
                    cls = "3_确认滞后差"
                    sub3["3a_≤3日"] += 1
                elif near is not None and near <= 10:
                    cls = "3_确认滞后差"
                    sub3["3b_4-10日"] += 1
                else:
                    cls = "4_覆盖缺口"
                    gap_dists.append(near if near is not None else -1)
            attr[cls] += 1
            attr_rows.append({"code": code, "side": side, "dt": dt, "type": tp, "cls": cls})

        return {
            "code": code,
            "skip": None,
            "n_bars": len(bars),
            "dropped_bad_dt": dropped,
            "n_a": len(pa),
            "n_b": len(pb),
            "inter": len(inter),
            "union": len(union),
            "jaccard": (len(inter) / len(union)) if union else None,
            "identical": pa == pb,
            "per_type": per_type,
            "pivot_eq": pivot_eq,
            "seg_eq": seg_eq,
            "bi_eq": bi_eq,
            "n_pivots": len(A["pivots"]),
            "attr": attr,
            "sub3": sub3,
            "attr_rows": attr_rows,
            "gap_dists": gap_dists,
            "sec": round(time.time() - t0, 3),
        }
    except Exception as e:  # 单票异常不拖垮全量
        log.exception("stock fail %s", code)
        return {"code": code, "skip": "error:%s" % e}


# ---------------------------------------------------------------- 阶段三
def _stage3_job(args):
    """逐日滚动重放：每个交易日 t 只用 bars[:t+1] 重算两套口径。"""
    code, path = args
    t0 = time.time()
    try:
        bars, _ = _load(path)
        if not bars or len(bars) < MIN_BARS:
            return {"code": code, "skip": "bars<%d" % MIN_BARS}
        A, B = _run_both(bars)
        full = {"A": _pairs(_norm_a(A)), "B": _pairs(_norm_b(B))}
        dts = [b["dt"] for b in bars]
        idx = {d: i for i, d in enumerate(dts)}
        n = len(bars)
        res = {}
        for side in ("A", "B"):
            res[side] = {
                "day_total": 0,
                "day_equal": 0,
                "day_signal": 0,  # 全历史或在线任一非空的日子（去掉空-空噪声）
                "day_equal_signal": 0,
                "online_first": {},  # pair -> 首次在线可见的 t（bar 索引）
                "online_day_mismatch": [],
            }
        for t in range(STAGE3_WARMUP, n):
            sub = bars[: t + 1]
            day = dts[t]
            try:
                onA = _pairs(_norm_a(chanlun_bi.analyze(sub, closes=[b["c"] for b in sub])))
                onB = _pairs(_norm_b(chanlun_signal.analyze(sub)))
            except Exception:
                log.exception("replay fail %s t=%d", code, t)
                continue
            for side, cur in (("A", onA), ("B", onB)):
                r = res[side]
                r["day_total"] += 1
                now = {p for p in cur if p[0] == day}
                fullnow = {p for p in full[side] if p[0] == day}
                if fullnow or now:
                    r["day_signal"] += 1
                    if now == fullnow:
                        r["day_equal_signal"] += 1
                if now == fullnow:
                    r["day_equal"] += 1
                elif fullnow - now or now - fullnow:
                    r["online_day_mismatch"].append(
                        {"dt": day, "full_only": sorted(fullnow - now), "online_only": sorted(now - fullnow)}
                    )
                for p in cur:
                    r["online_first"].setdefault(p, t)
        out = {}
        for side in ("A", "B"):
            r = res[side]
            online = set(r["online_first"])
            f = full[side]
            missed = f - online  # 全历史有、任何在线截断都产不出 → 前视
            extra = online - f  # 在线有、全历史无 → 路径依赖
            lag = [r["online_first"][p] - idx[p[0]] for p in (f & online) if r["online_first"][p] > idx[p[0]]]
            both_n = len(f & online)
            out[side] = {
                "n_full": len(f),
                "n_online_union": len(online),
                "set_jaccard": (both_n / len(f | online)) if (f | online) else None,
                "day_total": r["day_total"],
                "day_equal": r["day_equal"],
                "day_equal_rate": (r["day_equal"] / r["day_total"]) if r["day_total"] else None,
                "day_signal": r["day_signal"],
                "day_equal_signal": r["day_equal_signal"],
                "day_equal_rate_signal": (r["day_equal_signal"] / r["day_signal"]) if r["day_signal"] else None,
                "lag_zero": both_n - len(lag),
                "lookahead_missed": len(missed),
                "missed_pairs": sorted(missed)[:200],
                "extra_online": len(extra),
                "extra_pairs": sorted(extra)[:200],
                "lag_n": len(lag),
                "lag_median": statistics.median(lag) if lag else None,
                "lag_max": max(lag) if lag else None,
                "mismatch_days": len(r["online_day_mismatch"]),
                "mismatch_sample": r["online_day_mismatch"][:20],
            }
        # 两套口径「同时在线可得」的交集对比（同一 t 上两条路径的可比性）
        both = set(res["A"]["online_first"]) & set(res["B"]["online_first"])
        return {
            "code": code,
            "skip": None,
            "n_bars": n,
            "days": res["A"]["day_total"],
            "A": out["A"],
            "B": out["B"],
            "online_both_n": len(both),
            "sec": round(time.time() - t0, 3),
        }
    except Exception as e:
        log.exception("stage3 fail %s", code)
        return {"code": code, "skip": "error:%s" % e}


# ---------------------------------------------------------------- 汇总
def _pick_universe(n, seed):
    files = sorted(f for f in os.listdir(os.path.join(HERE, "data", "ashare")) if f.endswith(".parquet"))
    rnd = random.Random(seed)
    # 先抽 3n 候选，按清洗后根数过滤，补足 n 只
    rnd.shuffle(files)
    picked, scanned = [], 0
    for f in files:
        scanned += 1
        path = os.path.join(HERE, "data", "ashare", f)
        bars, _ = _load(path)
        if bars and len(bars) >= MIN_BARS:
            picked.append((f[:-8].replace("_", "."), path))
            if len(picked) >= n:
                break
        if scanned > 60 * n:
            break
    return picked, scanned


def _agg_stage1(rows):
    ok = [r for r in rows if not r.get("skip")]
    pooled_i = sum(r["inter"] for r in ok)
    pooled_u = sum(r["union"] for r in ok)
    by_type = {}
    for t in TYPES:
        n_a = sum(r["per_type"][t]["n_a"] for r in ok)
        n_b = sum(r["per_type"][t]["n_b"] for r in ok)
        hit = sum(r["per_type"][t]["hit"] for r in ok)
        union = n_a + n_b - hit
        by_type[t] = {
            "n_a": n_a,
            "n_b": n_b,
            "hit": hit,
            "jaccard": (hit / union) if union else None,
            "recall_a": (hit / n_a) if n_a else None,
            "precision_ba": (hit / n_b) if n_b else None,
            "stocks_a": sum(1 for r in ok if r["per_type"][t]["n_a"] > 0),
            "stocks_b": sum(1 for r in ok if r["per_type"][t]["n_b"] > 0),
        }
    attr_tot = {k: sum(r["attr"][k] for r in ok) for k in
                ("1_中枢定义差", "2_线段终结差", "3_确认滞后差", "4_覆盖缺口")}
    sub3 = {k: sum(r["sub3"][k] for r in ok) for k in ("3a_≤3日", "3b_4-10日")}
    tot = sum(attr_tot.values())
    gap = []
    for r in ok:
        gap.extend(r["gap_dists"])
    jac = [r["jaccard"] for r in ok if r["jaccard"] is not None]
    return {
        "stocks_ok": len(ok),
        "stocks_skip": len(rows) - len(ok),
        "bars_median": statistics.median([r["n_bars"] for r in ok]) if ok else None,
        "bars_min": min([r["n_bars"] for r in ok], default=None),
        "bars_max": max([r["n_bars"] for r in ok], default=None),
        "dropped_bad_dt": sum(r["dropped_bad_dt"] for r in ok),
        "n_a": sum(r["n_a"] for r in ok),
        "n_b": sum(r["n_b"] for r in ok),
        "hit": pooled_i,
        "union": pooled_u,
        "jaccard_pooled": (pooled_i / pooled_u) if pooled_u else None,
        "jaccard_macro": (sum(jac) / len(jac)) if jac else None,
        "jaccard_median": statistics.median(jac) if jac else None,
        "identical_stocks": sum(1 for r in ok if r["identical"]),
        "identical_rate": (sum(1 for r in ok if r["identical"]) / len(ok)) if ok else None,
        "zero_signal_stocks": sum(1 for r in ok if r["union"] == 0),
        "pivot_eq_rate": (sum(1 for r in ok if r["pivot_eq"]) / len(ok)) if ok else None,
        "seg_eq_rate": (sum(1 for r in ok if r["seg_eq"]) / len(ok)) if ok else None,
        "bi_eq_rate": (sum(1 for r in ok if r["bi_eq"]) / len(ok)) if ok else None,
        "by_type": by_type,
        "attr_counts": attr_tot,
        "attr_share": {k: (v / tot) if tot else None for k, v in attr_tot.items()},
        "attr_total": tot,
        "sub3": sub3,
        "gap_dist_median": statistics.median([g for g in gap if g >= 0]) if any(g >= 0 for g in gap) else None,
        "gap_no_counterpart": sum(1 for g in gap if g < 0),
        "n_pivots_avg": (sum(r["n_pivots"] for r in ok) / len(ok)) if ok else None,
    }


def _agg_stage3(rows):
    ok = [r for r in rows if not r.get("skip")]
    out = {"stocks_ok": len(ok), "stocks_skip": len(rows) - len(ok)}
    for side in ("A", "B"):
        dt = sum(r[side]["day_total"] for r in ok)
        de = sum(r[side]["day_equal"] for r in ok)
        ds = sum(r[side]["day_signal"] for r in ok)
        des = sum(r[side]["day_equal_signal"] for r in ok)
        nf = sum(r[side]["n_full"] for r in ok)
        nu = sum(r[side]["n_online_union"] for r in ok)
        hit = nf - sum(r[side]["lookahead_missed"] for r in ok)
        out[side] = {
            "n_full": nf,
            "n_online_union": nu,
            "set_jaccard": (hit / (nf + nu - hit)) if (nf + nu - hit) else None,
            "day_total": dt,
            "day_equal": de,
            "day_equal_rate": (de / dt) if dt else None,
            "day_signal": ds,
            "day_equal_signal": des,
            "day_equal_rate_signal": (des / ds) if ds else None,
            "lag_zero": sum(r[side]["lag_zero"] for r in ok),
            "lookahead_missed": sum(r[side]["lookahead_missed"] for r in ok),
            "extra_online": sum(r[side]["extra_online"] for r in ok),
            "lag_n": sum(r[side]["lag_n"] for r in ok),
            "lag_ratio": (sum(r[side]["lag_n"] for r in ok) / nf) if nf else None,
            "lag_median": statistics.median(
                [r[side]["lag_median"] for r in ok if r[side]["lag_median"] is not None]
            ) if any(r[side]["lag_median"] is not None for r in ok) else None,
            "lag_max": max([r[side]["lag_max"] for r in ok if r[side]["lag_max"] is not None], default=None),
            "stocks_with_missed": sum(1 for r in ok if r[side]["lookahead_missed"] > 0),
        }
    out["days_total"] = sum(r["days"] for r in ok)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--stage1", type=int, default=N_STAGE1)
    ap.add_argument("--stage3", type=int, default=N_STAGE3)
    ap.add_argument("--no-stage3", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    t_all = time.time()
    wk = max(1, min(10, args.workers))
    log.info("workers=%d seed=%d min_bars=%d", wk, SEED, MIN_BARS)
    log.info("use_hfq=%s", chanlun_scan._use_hfq())

    uni1, scanned = _pick_universe(args.stage1, SEED)
    log.info("universe stage1: %d 只（扫描候选 %d）", len(uni1), scanned)

    res1 = []
    with ProcessPoolExecutor(max_workers=wk) as ex:
        for i, r in enumerate(ex.map(_stock_job, uni1, chunksize=2), 1):
            res1.append(r)
            if i % 20 == 0:
                log.info("stage1 %d/%d", i, len(uni1))
    s1 = _agg_stage1(res1)

    res3, s3 = [], None
    if not args.no_stage3:
        uni3 = uni1[: args.stage3]
        log.info("universe stage3: %d 只，逐日重放（warmup=%d）", len(uni3), STAGE3_WARMUP)
        with ProcessPoolExecutor(max_workers=wk) as ex:
            for i, r in enumerate(ex.map(_stage3_job, uni3, chunksize=1), 1):
                res3.append(r)
                log.info("stage3 %d/%d (%s, %.1fs)", i, len(uni3), r["code"], r.get("sec", 0))
        s3 = _agg_stage3(res3)

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {
            "seed": SEED,
            "stage1_n": len(uni1),
            "stage3_n": len(res3),
            "min_bars": MIN_BARS,
            "workers": wk,
            "stage3_warmup": STAGE3_WARMUP,
            "stage3_step": 1,
            "use_hfq": chanlun_scan._use_hfq(),
            "field_map_note": "A 实测字段 d/p/t→dt/type(dt,type 由脚本映射)；B 为 type/dt/price",
            "elapsed_sec": None,
        },
        "stage1": s1,
        "stage2": {
            "attr_counts": s1["attr_counts"],
            "attr_share": s1["attr_share"],
            "sub3": s1["sub3"],
            "gap_no_counterpart": s1["gap_no_counterpart"],
            "gap_dist_median": s1["gap_dist_median"],
        },
        "stage3": s3,
        "stocks_stage1": res1,
        "stocks_stage3": res3,
    }
    out["config"]["elapsed_sec"] = round(time.time() - t_all, 1)
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ---- 控制台摘要（便于 grep）
    print("\n== STAGE1 ==")
    print("stocks=%d skip=%d  A=%d B=%d hit=%d union=%d" % (
        s1["stocks_ok"], s1["stocks_skip"], s1["n_a"], s1["n_b"], s1["hit"], s1["union"]))
    print("jaccard_pooled=%.4f macro=%.4f median=%.4f" % (
        s1["jaccard_pooled"], s1["jaccard_macro"], s1["jaccard_median"]))
    print("identical_stocks=%d/%d (%.1f%%)" % (
        s1["identical_stocks"], s1["stocks_ok"], 100 * s1["identical_rate"]))
    print("struct_equal: pivots=%.3f segments=%.3f bis=%.3f" % (
        s1["pivot_eq_rate"], s1["seg_eq_rate"], s1["bi_eq_rate"]))
    for t in TYPES:
        x = s1["by_type"][t]
        print("TYPE %s A=%-5d B=%-5d hit=%-4d J=%s R(A)=%s P(B->A)=%s" % (
            t, x["n_a"], x["n_b"], x["hit"],
            ("%.4f" % x["jaccard"]) if x["jaccard"] is not None else "-",
            ("%.4f" % x["recall_a"]) if x["recall_a"] is not None else "-",
            ("%.4f" % x["precision_ba"]) if x["precision_ba"] is not None else "-"))
    print("\n== STAGE2 ==")
    for k, v in s1["attr_counts"].items():
        print("ATTR %s n=%d share=%.4f" % (k, v, v / s1["attr_total"] if s1["attr_total"] else 0))
    print("SUB3", s1["sub3"], "gap_no_counterpart=", s1["gap_no_counterpart"],
          "gap_dist_median=", s1["gap_dist_median"])
    if s3:
        print("\n== STAGE3 ==")
        print("stocks=%d days=%d step=1" % (s3["stocks_ok"], s3["days_total"]))
        for side in ("A", "B"):
            x = s3[side]
            print("SIDE %s full=%d online_union=%d setJ=%.4f | daysig=%d equal=%d (%.4f) | dayall=%d/%d (%.4f) | lag0=%d lag_n=%d lag_ratio=%.4f lag_med=%s lag_max=%s | missed=%d extra=%d" % (
                side, x["n_full"], x["n_online_union"], x["set_jaccard"] or 0,
                x["day_signal"], x["day_equal_signal"], x["day_equal_rate_signal"] or 0,
                x["day_equal"], x["day_total"], x["day_equal_rate"] or 0,
                x["lag_zero"], x["lag_n"], x["lag_ratio"] or 0, x["lag_median"], x["lag_max"],
                x["lookahead_missed"], x["extra_online"]))
    print("\nJSON -> %s  (%.1fs)" % (OUT_JSON, out["config"]["elapsed_sec"]))


if __name__ == "__main__":
    main()
