# -*- coding: utf-8 -*-
"""beta 时变修正验证：regime 分组里的 |t|>20 是真实 edge 还是 beta 误估。

问题
----
产线 alpha 用「入场前 250 日等权 OLS」估计 beta。等权意味着 1 年前的配对
与昨天同等重要，风格切换（大小盘、成长价值）后 beta 会滞后 1~2 个季度。
若某类信号恰好在此窗口内集中在高/低 beta 股，alpha 会被系统性误估，
表现为「regime 分组 t 值极端」——看起来像强 edge，实际是 beta 没跟上。

做法
----
同一批信号（daily 全量扩样本）分别用两套 beta 算 alpha：
  · ols250   现行口径（等权 250 日）
  · ewma45   指数加权，半衰期 45 日（近端权重高，跟随更快）
再按「入场前 60 日等权全A涨跌」分 up/flat/down 三组，对比两套 t 值。
若 ewma 下极端 t 显著收敛 → 原结论是 beta 误估，regime 门控不可采信。

输出
----
data/chanlun_beta_tv_check.json

用法:
    python chanlun_beta_tv.py
    python chanlun_beta_tv.py --types 1买,1卖 --limit 800
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import atomic_io  # noqa: E402  (P2-c 统一原子写)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
DEFAULT_POOL = os.path.join(DATA, "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(DATA, "chanlun_beta_tv_check.json")
HALFLIFE = 45


def _wbeta(sd, sr, asof, bpos, bret, win=250, halflife=HALFLIFE, min_pairs=40):
    """EWMA beta：只用 asof 之前的配对，近端权重 1，按半衰期衰减。"""
    asof = str(asof).replace("-", "")[:8]
    end = -1
    lo_, hi_ = 0, len(sd) - 1
    while lo_ <= hi_:
        mid = (lo_ + hi_) // 2
        if sd[mid] <= asof:
            end, lo_ = mid, mid + 1
        else:
            hi_ = mid - 1
    if end <= 0:
        return None, 0
    xs, ys = [], []
    for t in range(max(0, end - win + 1), end + 1):
        if sd[t] >= asof:
            break
        j = bpos.get(sd[t])
        if j is None:
            continue
        xs.append(bret[j])
        ys.append(sr[t])
    n = len(xs)
    if n < min_pairs:
        return None, n
    x = np.asarray(xs)
    y = np.asarray(ys)
    w = 0.5 ** (np.arange(n - 1, -1, -1) / float(halflife))
    sw = w.sum()
    xm = float((w * x).sum() / sw)
    ym = float((w * y).sum() / sw)
    vx = float((w * (x - xm) ** 2).sum() / sw)
    if vx <= 1e-12:
        return None, n
    b = float((w * (x - xm) * (y - ym)).sum() / sw / vx)
    if not np.isfinite(b):
        return None, n
    return round(max(-0.5, min(3.0, b)), 3), n


def _one(job):
    code, rows, dedup_n = job
    import chanlun_alpha as ca

    sd, sr = ca.stock_ret(code)
    if sd is None:
        return []
    b = ca.bench_ew()
    if dedup_n:  # 同票相邻信号至少间隔 dedup_n 个交易日，消除重叠样本
        kept, last_i = [], -10**9
        for r in sorted(rows, key=lambda x: x.get("entry_date") or ""):
            e = ca._norm_dt(r.get("entry_date"))
            i = ca._pos_le(sd, e)
            if i < 0 or i - last_i < dedup_n:
                continue
            last_i = i
            kept.append(r)
        rows = kept
    out = []
    for r in rows:
        ed, xd = r.get("entry_date"), r.get("exit_date")
        raw, a_ols, b_ols = r.get("ret"), r.get("alpha"), r.get("beta")
        if raw is None or ed is None or xd is None:
            continue
        br = ca.bench_window_ret(ed, xd, bench=b)
        if br is None:
            continue
        b_ew, npair = _wbeta(sd, sr, ed, b["pos"], b["ret"])
        if b_ew is None:
            continue
        out.append(
            {"code": code, "type": r["type"], "entry": ed,
             "b_ols": b_ols if b_ols is not None else 1.0,
             "b_ew": b_ew, "idx": br, "raw": raw,
             "a_ols": a_ols if a_ols is not None else raw - (b_ols or 1.0) * br,
             "a_ew": raw - b_ew * br}
        )
    return out


def _fm(rows, key):
    """Fama-MacBeth：先按入场月份聚成月度均值，再对月度序列算 t。

    横截面相关（同期股票共享市场冲击）会让名义 n 的 t 值严重膨胀，
    按月聚类后的 t 才是「这个信号能否跨期复用」的判据。
    """
    by = {}
    for r in rows:
        v = r.get(key)
        if v is None:
            continue
        m = (r.get("entry") or "")[:7]
        if not m:
            continue
        by.setdefault(m, []).append(v)
    if len(by) < 6:
        return None
    mo = [sum(v) / len(v) for v in by.values()]
    mu = sum(mo) / len(mo)
    var = sum((x - mu) ** 2 for x in mo) / (len(mo) - 1)
    return {"months": len(mo), "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / len(mo) ** 0.5), 2) if var > 0 else None}


def _blk(v):
    v = [x for x in v if x is not None]
    n = len(v)
    if n < 2:
        return None
    mu = sum(v) / n
    var = sum((x - mu) ** 2 for x in v) / (n - 1)
    return {"n": n, "avg": round(mu * 100, 3),
            "t": round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None,
            "win": round(sum(1 for x in v if x > 0) * 100.0 / n, 1)}


def main():
    ap = argparse.ArgumentParser(description="beta 时变修正验证")
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--types", default="1买,2买,3买,1卖,2卖,3卖")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dedup", type=int, default=0,
                    help="同票相邻信号最小交易日间隔（>0 生效，建议 60）")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    types = [t.strip() for t in a.types.split(",") if t.strip()]
    rows = [json.loads(li) for li in open(a.pool, encoding="utf-8") if li.strip()]
    rows = [r for r in rows if r["type"] in types and r.get("status") == "closed"]
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    codes = sorted(by_code)
    if a.limit:
        codes = codes[: a.limit]
    print("信号=%d 标的=%d halflife=%d dedup=%s"
          % (len(rows), len(codes), HALFLIFE, a.dedup or "off"))

    t0 = time.time()
    res = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for part in ex.map(_one, [(c, by_code[c], a.dedup) for c in codes],
                           chunksize=8):
            res.extend(part)
    print("算完 %d 笔，用时 %.0fs" % (len(res), time.time() - t0))

    # 1) 两套 beta 的差异
    db = [r["b_ew"] - r["b_ols"] for r in res]
    absdb = sorted(abs(x) for x in db)
    n = len(db)
    print("\n=== beta 估计差异（ewma45 - ols250）===")
    print("  均值 %+0.3f  中位 %+0.3f  |差|中位 %.3f  p90 %.3f  p99 %.3f"
          % (sum(db) / n, sorted(db)[n // 2], absdb[n // 2],
             absdb[int(n * 0.9)], absdb[int(n * 0.99)]))
    big = sum(1 for x in absdb if x > 0.3)
    print("  |差|>0.3 的占比 %.1f%%（%d 笔）→ beta 误估会直接放大成 alpha 误差"
          % (big * 100.0 / n, big))

    # 2) 全样本 alpha 两套对比
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "halflife": HALFLIFE, "dedup": a.dedup, "n": n,
           "beta_diff": {"mean": round(sum(db) / n, 4),
                         "med": round(sorted(db)[n // 2], 4),
                         "p90_abs": round(absdb[int(n * 0.9)], 4),
                         "share_gt_0.3": round(big * 100.0 / n, 1)},
           "types": {}}
    print("\n=== alpha：ols250 vs ewma45 ===")
    print("  %-4s %6s | %-20s | %-20s | %s"
          % ("type", "n", "ols250", "ewma45", "FM 月度聚类"))
    agg = {}
    for r in res:
        agg.setdefault(r["type"], []).append(r)
    for t in types:
        lst = agg.get(t) or []
        if not lst:
            continue
        o = _blk([x["a_ols"] for x in lst])
        e = _blk([x["a_ew"] for x in lst])
        fm = _fm(lst, "a_ols")
        out["types"][t] = {"ols": o, "ewma": e, "fm": fm,
                           "beta_mean_ols": round(sum(x["b_ols"] for x in lst) / len(lst), 3),
                           "beta_mean_ew": round(sum(x["b_ew"] for x in lst) / len(lst), 3)}
        print("  %-4s %6d | %+7.2f (t=%6s) | %+7.2f (t=%6s) | FM %+7.2f (t=%6s, %s月)"
              % (t, len(lst), o["avg"], o["t"], e["avg"], e["t"],
                 (fm or {}).get("avg", 0), (fm or {}).get("t"), (fm or {}).get("months")))

    # 3) regime 分组：两套 beta 下 t 值是否收敛
    import chanlun_alpha as ca

    b = ca.bench_ew()
    ret, pos = b["ret"], b["pos"]

    def regime(d, win=60, th=0.05):
        j = pos.get(ca._norm_dt(d))
        if j is None or j < win:
            return None
        rr = float(np.prod(1 + ret[j - win:j]) - 1)
        return "up" if rr > th else ("down" if rr < -th else "flat")

    print("\n=== regime 分组（入场前 60 日等权全A）两套 beta 对比 ===")
    out["regime"] = {}
    for t in types:
        lst = agg.get(t) or []
        if not lst:
            continue
        g = {"up": [], "flat": [], "down": []}
        for x in lst:
            k = regime(x["entry"])
            if k:
                g[k].append(x)
        cell = {}
        print("  [%s]" % t)
        for k in ("up", "flat", "down"):
            if len(g[k]) < 10:
                continue
            o = _blk([x["a_ols"] for x in g[k]])
            e = _blk([x["a_ew"] for x in g[k]])
            cell[k] = {"ols": o, "ewma": e, "fm": _fm(g[k], "a_ols")}
            flag = ""
            for blk in (o, e):
                if blk and blk["t"] is not None and abs(blk["t"]) > 10:
                    flag = "  ← 极端"
            fm = cell[k]["fm"] or {}
            print("    %-5s n=%-5d ols %+7.2f(t=%6s) | ewma %+7.2f(t=%6s) | FM %+7.2f(t=%6s)%s"
                  % (k, len(g[k]), o["avg"], o["t"], e["avg"], e["t"],
                     fm.get("avg", 0), fm.get("t"), flag))
        out["regime"][t] = cell

    with atomic_io.replacing(a.out) as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n-> %s" % a.out)


if __name__ == "__main__":
    main()
