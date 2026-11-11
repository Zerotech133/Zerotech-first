# -*- coding: utf-8 -*-
"""逐票对比：同一个 cohort，cohort 结算口径 vs arena 成交口径，差在哪只票。"""
import json
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_arena as PA  # noqa: E402
import cohort_track as CT  # noqa: E402

COH = os.path.join(HERE, "cohorts")


def main():
    key = "20261106_auto"
    asof, pool = key.split("_")
    c = json.load(io.open(os.path.join(COH, "cohort_%s.json" % key), encoding="utf-8"))
    entry, m = c["entry_date"], c["maturities"]["1"]
    print("cohort %s  entry=%s  m=%s  n=%d" % (key, entry, m, c["n"]))

    de, ae = CT.load_daily(entry), CT.load_adjf(entry)
    dm, am = CT.load_daily(m), CT.load_adjf(m)
    print("行情装载: entry 有价 %d 只 / m 有价 %d 只" % (len(de), len(dm)))

    arena = [json.loads(l) for l in io.open(
        os.path.join(HERE, "arena", "S1", "trades.jsonl"), encoding="utf-8") if l.strip()]
    arena = {x["code"]: x for x in arena if x["sig"] == key}
    print("arena 同轨成交 %d 笔" % len(arena))

    cohort_rets, arena_rets, rows = [], [], []
    for code in c["codes"]:
        px = CT._adj_px(dm, am, code, "close")
        po = CT._adj_px(de, ae, code, "open")
        if px is None or po is None:
            continue
        r1 = px / po - 1
        cohort_rets.append(r1)
        a = arena.get(code)
        if a:
            ra = a["exit_px"] / a["entry_px"] - 1
            arena_rets.append(ra)
            rows.append((abs(ra - r1), code, r1, ra, a["entry_px"], po, a["exit_px"], px))
        else:
            rows.append((9, code, r1, None, None, po, None, px))

    print("\ncohort 口径均值 %.6f (%d 只)" % (sum(cohort_rets) / len(cohort_rets), len(cohort_rets)))
    if arena_rets:
        print("arena 毛收益均值 %.6f (%d 只，未扣费)" % (sum(arena_rets) / len(arena_rets), len(arena_rets)))

    rows.sort(reverse=True)
    print("\n差异最大的 6 只：")
    print("%-12s %9s %9s %9s %10s %10s" % ("code", "cohort毛", "arena毛", "差", "cohort买", "arena买"))
    for d, code, r1, ra, ap, po, ax, px in rows[:6]:
        print("%-12s %8.3f%% %9s %9s %10.4f %10s"
              % (code, r1 * 100,
                 ("%8.3f%%" % (ra * 100)) if ra is not None else "  未成交",
                 ("%8.3f%%" % (d * 100)) if ra is not None else "-",
                 po, ("%.4f" % ap) if ap else "-"))

    # 关键：cohort 里出现了但 arena 没买的票
    miss = [x for x in rows if x[3] is None]
    print("\ncohort 有、arena 未成交: %d 只 %s" % (len(miss), [x[1] for x in miss][:8]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
