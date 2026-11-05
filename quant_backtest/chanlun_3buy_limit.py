# -*- coding: utf-8 -*-
"""3买入场时机对照实验（P1-1：把「追突破」改成「等回踩」）。

假设
----
3买 在贝塔中性口径下稳定负超额（全量重建 n=1396：alpha -2.61%，t=-4.97），
但 `bei/bei2/vv` 因子在 3买 内部无区分度（各子集 -4.6%~-6.4% 全负），说明
问题不在「选哪只」，而在**入场时点**：3买 的确认条件（离开中枢后回踩不回
区间）天生滞后，确认时价格已离 ZG 一段，等于系统性追高。

对照三口径（同一批信号、同一出场规则，只换入场）
----------------------------------------------
A 基线：确认日收盘价入场（产线现行）
B 次日开盘入场（修正「23:35 才落数据、当日收盘不可成交」的假设）
C 限价回踩 ZG：确认日之后 5 个交易日内挂 ZG 限价，触价成交（低点≤ZG），
  不成交则**作废不做**（这是与 A/B 的本质区别：允许放弃交易）

出场统一为「入场后满 20 个交易日按收盘离场」，三口径可比；
反向信号提前离场在 `--rev-exit` 打开时附加（默认关闭，避免出场规则差异
混进入场对比）。

用法:
    python chanlun_3buy_limit.py                       # 默认读 ext 扩样本
    python chanlun_3buy_limit.py --pool data/chanlun_pool_ext_0.jsonl
    python chanlun_3buy_limit.py --types 3买,2买        # 顺带看别的类型
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
HOLD = 20
LIMIT_WIN = 5  # 限价单有效交易日数
OUT = os.path.join(DATA, "chanlun_entry_mode_eval.json")


def _blk(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if n < 2:
        return None
    mu = sum(vals) / n
    var = sum((v - mu) ** 2 for v in vals) / (n - 1)
    t = round(mu / (var ** 0.5 / (n ** 0.5)), 2) if var > 0 else None
    s = sorted(vals)
    return {"n": n, "win": round(sum(1 for v in vals if v > 0) * 100.0 / n, 1),
            "avg": round(mu * 100, 2), "med": round(s[n // 2] * 100, 2), "t": t}


def _sign(t):
    return 1.0 if str(t).endswith("买") else -1.0


def main():
    ap = argparse.ArgumentParser(description="3买 入场时机三口径对照")
    ap.add_argument("--pool", default=os.path.join(DATA, "chanlun_pool_ext_500.jsonl"))
    ap.add_argument("--types", default="3买")
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--limit-win", type=int, default=LIMIT_WIN)
    ap.add_argument("--rev-exit", action="store_true", help="附反向信号提前离场")
    ap.add_argument("--dips", default="1,2,3,5",
                    help="回撤买入深度（%），逗号分隔；空串关闭")
    a = ap.parse_args()

    import chanlun_alpha as ca
    import chanlun_bi as cb
    import chanlun_scan as cs

    types = [t.strip() for t in a.types.split(",") if t.strip()]
    a.dips = [float(x) for x in str(a.dips).replace(" ", "").split(",") if x]
    rows = [json.loads(li) for li in open(a.pool, encoding="utf-8") if li.strip()]
    rows = [r for r in rows if r["type"] in types]
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r)
    print("pool=%s types=%s rows=%d codes=%d"
          % (os.path.basename(a.pool), ",".join(types), len(rows), len(by_code)))

    acc = {}  # type → mode → [rets]
    acc_a = {}
    n_nozg = n_nofill = n_nofill_skip = n_dip_unfill = 0
    for code, lst in by_code.items():
        bars = cs._bars_from_parquet(
            os.path.join(ASHARE, code.replace(".", "_") + ".parquet")) or []
        if len(bars) < 130:
            continue
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        try:
            piv = cb.analyze(bars).get("pivots") or []
        except Exception:
            piv = []
        # 反向信号（供 --rev-exit 用）：确认日索引集合
        marks = sorted((idx[r["entry_date"]], _sign(r["type"]))
                       for r in lst if r.get("entry_date") in idx)
        mark_i = [m[0] for m in marks]

        for r in lst:
            ei = idx.get(r.get("entry_date"))
            if ei is None:
                continue
            sgn = _sign(r["type"])
            # 该信号所属中枢：e_dt <= 信号日 的最后一个
            zg = None
            for p in piv:
                if p["e_dt"] <= r["dt"]:
                    zg = p["zg"]
                else:
                    break
            if zg is None:
                n_nozg += 1

            def _exit(ei0, entry_px, entry_date):
                xi = ei0 + a.hold
                if xi >= len(bars):
                    return None
                if a.rev_exit:
                    for j, ms in marks:
                        if j > ei0 and ms != sgn and j < xi:
                            xi = j
                            break
                move = bars[xi]["c"] / entry_px - 1.0
                return move if sgn > 0 else -move, bars[xi]["dt"]

            # A 确认日收盘
            out = _exit(ei, bars[ei]["c"], r["entry_date"])
            if out:
                ret, xd = out
                acc.setdefault(r["type"], {}).setdefault("A_close", []).append(ret)
                acc_a.setdefault(r["type"], {}).setdefault("A_close", []).append(
                    ca.annotate(code, ret, r["entry_date"], xd, r["entry_date"])["alpha"])

            # B 次日开盘
            if ei + 1 < len(bars):
                out = _exit(ei + 1, bars[ei + 1]["o"], bars[ei + 1]["dt"])
                if out:
                    ret, xd = out
                    acc.setdefault(r["type"], {}).setdefault("B_next_open", []).append(ret)
                    acc_a.setdefault(r["type"], {}).setdefault("B_next_open", []).append(
                        ca.annotate(code, ret, bars[ei + 1]["dt"], xd, r["entry_date"])["alpha"])

            # C 限价回踩 ZG（5 日内触价，不成交作废）
            if zg is not None and zg < bars[ei]["c"]:
                fill_i = None
                for j in range(ei + 1, min(ei + 1 + a.limit_win, len(bars))):
                    if bars[j]["l"] <= zg:
                        fill_i = j
                        break
                if fill_i is None:
                    n_nofill += 1
                else:
                    px = min(zg, bars[fill_i]["o"])  # 跳空低开按开盘价成交（更优）
                    out = _exit(fill_i, px, bars[fill_i]["dt"])
                    if out:
                        ret, xd = out
                        acc.setdefault(r["type"], {}).setdefault("C_limit_zg", []).append(ret)
                        acc_a.setdefault(r["type"], {}).setdefault("C_limit_zg", []).append(
                            ca.annotate(code, ret, bars[fill_i]["dt"], xd, r["entry_date"])["alpha"])
            else:
                n_nofill_skip += 1  # ZG 不在下方 → 不存在「回踩」语义

            # D 回撤 d% 买入（ZG 太远导致 C 成交率过低时的替代方案）
            for d in a.dips:
                tgt = bars[ei]["c"] * (1 - d / 100.0)
                fill_i = None
                for j in range(ei + 1, min(ei + 1 + a.limit_win, len(bars))):
                    if bars[j]["l"] <= tgt:
                        fill_i = j
                        break
                if fill_i is None:
                    n_dip_unfill += 1
                    continue
                px = min(tgt, bars[fill_i]["o"])
                out = _exit(fill_i, px, bars[fill_i]["dt"])
                if out:
                    ret, xd = out
                    m = "D_dip%s%%" % d
                    acc.setdefault(r["type"], {}).setdefault(m, []).append(ret)
                    acc_a.setdefault(r["type"], {}).setdefault(m, []).append(
                        ca.annotate(code, ret, bars[fill_i]["dt"], xd, r["entry_date"])["alpha"])

    res = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "pool": os.path.basename(a.pool),
           "hold": a.hold, "limit_win": a.limit_win, "rev_exit": a.rev_exit,
           "n_no_pivot": n_nozg, "n_zg_not_below": n_nofill_skip,
           "n_limit_unfilled": n_nofill, "n_dip_unfilled": n_dip_unfill,
           "dips": a.dips, "types": {}}
    for t in types:
        modes = acc.get(t) or {}
        if not modes:
            continue
        base = len(modes.get("A_close") or [])
        res["types"][t] = {
            m: {"raw": _blk(v), "alpha": _blk((acc_a.get(t) or {}).get(m) or []),
                "fill_rate": round(len(v) * 100.0 / base, 1) if base else None}
            for m, v in modes.items()
        }
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(OUT + ".tmp", OUT)

    print("无中枢可定位 %d / ZG不低于现价 %d / 挂ZG未成交 %d / 回撤单未成交 %d"
          % (n_nozg, n_nofill_skip, n_nofill, n_dip_unfill))
    for t, modes in res["types"].items():
        print("\n[%s] 入场口径对照" % t)
        print("  %-14s %5s %7s %8s %8s %7s %6s"
              % ("mode", "n", "fill%", "raw%", "alpha%", "t_alp", "win%"))
        for m, v in modes.items():
            rw, al = v["raw"], v["alpha"]
            print("  %-14s %5d %7s %+8.2f %+8.2f %7s %6.1f"
                  % (m, rw["n"], v["fill_rate"], rw["avg"],
                     (al or {}).get("avg") or 0, (al or {}).get("t") or 0, rw["win"]))
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
