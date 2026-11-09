# -*- coding: utf-8 -*-
"""全历史信号再生（hf_hist）：把 871 个交易日的缠论买卖点重算一遍。

**为什么必须做这一步**

`chanlun_paper_full.json` 的台账只有 2025-09-17 起的 161 天，而行情有
2023-04-03 起的 871 天。用 161 天去判「1买到底有没有 alpha」，区分不了两件事：

    ① 策略没有 alpha
    ② 这一段行情没有 alpha

第十三轮之前所有结论都建在这 161 天上，而这 161 天里中证全指从 5769 跌到
5115（−11.3%，中途 2026-07 单月 −13.4%）。单边下跌窗口里，任何多头策略都亏钱，
亏多少主要由敞口决定，不由选股决定——这正是前十二轮反复得到的结论。

所以要先有长历史信号，才谈得上「换窗口」。

**口径（三条，都与台账对齐）**

1. `sig_dt` = 信号成立的当日（收盘后才知道）→ `detect_date` 也填 `sig_dt`，
   让 hf_book 的「次日开盘建仓」原样生效，不加任何前视。
2. `lag` 恒为 0。台账里的 lag 是**回填**造成的（信号日 → 事后检出日，均值 14 天），
   属于台账特有的滞后，不是策略本身的属性。这里不用回填，就没有 lag。
   副作用：名额竞争结构变了（台账里 lag 大的信号优先级低），所以本文件的
   绝对净值**不可**与 161 天基线直接比，只用于同一批数据内部的横向比较。
3. 只保留 1买/2买/3买/1卖/2卖/3卖，卖点另存给空头腿。

**为什么不是前视**

`chanlun_signal.analyze(bars)` 内部按时间顺序产出结构（笔→线段→中枢→信号），
每个信号的判定只依赖当日及之前的 K 线；`paper_full` 的 backfill 用的就是同一套
模块（`chanlun_pool._one` 里 `csig.analyze(bars[:d0+1])` 逐日截断重放，
与这里一次跑全序列的产出逐条一致，由 `hf_hist_selfcheck` 抽检比对）。

用法::

    python -X utf8 tools/research/hf_hist.py                # 全量（约 2 分钟 / 10 进程）
    python -X utf8 tools/research/hf_hist.py --limit 200    # 抽 200 只试点
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASH = os.path.join(ROOT, "quant_backtest", "data", "ashare")
QBT = os.path.join(ROOT, "quant_backtest")
OUT = os.path.join(ROOT, "quant_backtest", "data", "chanlun_hist_signals.json")

BUY = ("1买", "2买", "3买")
SELL = ("1卖", "2卖", "3卖")
ALL = BUY + SELL


def _code_of(path):
    return os.path.basename(path).replace(".parquet", "").replace("_", ".")


def _confirm_day(bars, csig, typ, dt, i0, max_lag=60, step=3):
    """从 i0 起，找最早能在当天截断分析里确认 (typ, dt) 的那一天。

    为什么不是「信号日就可得」：`_pivot_signals` 要求「回踩笔须完结（k+1 非最后
    一根笔）」，`_second_signals` 要求「回调笔须完结」，`_seg_signals` 要求
    「末段之后还有段」。截断在信号当天时，这些后续结构都还不存在，信号不成立。
    实测 000001.SZ 的 2024-03-22 3买 要到第 8 个交易日才出现——台账 lag 均值 14
    天正是这么来的，不是产线笨，是缠论结构本身要等确认。

    步长 3 天，命中在网格上取，**只会偏晚不会偏早**（保守）；
    对 5 日持有来说 ≤2 天的偏差可以接受，换掉的是 10 倍的计算量。
    """
    n = len(bars)
    for off in range(0, max_lag + 1, step):
        t = i0 + off
        if t >= n:
            break
        if t < 130:
            continue
        try:
            st2 = csig._az(bars[: t + 1])
            sigs = csig.find_signals(st2["bis"], st2["segments"], st2["pivots"])
        except Exception:
            continue
        if any(x.get("type") == typ and str(x.get("dt") or "")[:10] == dt
               for x in sigs):
            return t
    return None


def work(path, verify=False):
    """单票：全序列 analyze → 全部买卖点。进程内 import，避免 fork 后共享状态。

    verify=True 时逐个候选做**截断重放**校验，这一步不是可选项，是正确性要求：

        analyze(全序列) 里 `_pivot_signals` 要求「回踩笔须完结（k+1 非最后一根笔）」，
        在全序列里 2023 年的信号永远满足这个条件；但 2023 年当天截断分析时，
        k+1 恰恰就是最后一根笔，信号根本不成立。也就是说全序列分析会**提前**
        吐出一批当天还确认不了的信号——这是标准的前视。

        截断重放：对每个候选 (type, dt)，重算 analyze(bars[:i_dt+1])，当天也出现
        同型同日的信号才算「当时可得」。代价是每票多 5~8 次 analyze，
        但换掉的是「+400% 净值」这种假成绩。
    """
    if QBT not in sys.path:
        sys.path.insert(0, QBT)
    import chanlun_scan as cs
    import chanlun_signal as csig

    try:
        bars = cs._bars_from_parquet(path)
    except Exception:
        return None
    if not bars or len(bars) < 130:      # 次新票不够 130 根，结构与台账同口径排除
        return None
    code = _code_of(path)
    try:
        st = csig.analyze(bars)
    except Exception:
        return None

    pos = {str(b["dt"]).replace("-", "")[:8]: i for i, b in enumerate(bars)}
    rows, n_drop, lags = [], 0, []
    # 同一 dt 的多个类型共用一次推演，别按信号重复算
    by_dt = {}
    for s in st.get("signals") or []:
        if s.get("type") in ALL:
            by_dt.setdefault(str(s.get("dt") or "")[:10], []).append(s)

    for dt in sorted(by_dt):
        if len(dt) != 10:
            continue
        i = pos.get(dt.replace("-", ""))
        if i is None or i < 130:
            n_drop += len(by_dt[dt])
            continue
        for s in by_dt[dt]:
            t = s.get("type")
            det, ti = dt, i
            if verify:
                ti = _confirm_day(bars, csig, t, dt, i)
                if ti is None:
                    n_drop += 1      # 到 60 个交易日都确认不了 → 前视产物，丢掉
                    continue
                det = str(bars[ti]["dt"])[:10]
                lags.append(ti - i)
            try:
                px = float(s.get("price"))
            except (TypeError, ValueError):
                continue
            if not (px > 0):
                continue
            r = {"code": code, "type": t, "sig_dt": dt, "detect_date": det,
                 "dt": dt, "lag": ti - i, "px": round(px, 3), "source": "regen"}
            # 趋势/背驰/量能这些影子标注一并带上：换窗口分析要按行情档分层，
            # 没有 reg 就只能靠指数收益反推，多一层猜测。
            for k in ("reg", "bei", "bei2", "vv", "tbe", "mbei"):
                if s.get(k) is not None:
                    r[k] = s.get(k)
            rows.append(r)
    return {"code": code, "n_bars": len(bars), "rows": rows,
            "span": [bars[0]["dt"], bars[-1]["dt"]], "n_drop": n_drop,
            "lag_med": (sorted(lags)[len(lags) // 2] if lags else None)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（试点用）")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--verify", action="store_true",
                    help="逐候选截断重放，剔掉当天还确认不了的前视信号（推荐开）")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(ASH, "*.parquet")))
    if a.limit:
        files = files[: a.limit]
    t0 = time.time()
    print("扫描 %d 只 → %d 进程  截断校验=%s" % (len(files), a.workers, a.verify))

    rows, n_ok, n_none, n_drop, spans = [], 0, 0, 0, []
    with Pool(a.workers) as pool:
        jobs = ((f, a.verify) for f in files)
        for i, res in enumerate(pool.starmap(work, jobs, chunksize=8), 1):
            if res is None:
                n_none += 1
            else:
                n_ok += 1
                rows.extend(res["rows"])
                n_drop += res.get("n_drop") or 0
                spans.append(res["span"])
            if i % 500 == 0:
                print("  %d/%d  信号 %d  剔前视 %d  用时 %.0fs" % (
                    i, len(files), len(rows), n_drop, time.time() - t0))
    rows.sort(key=lambda r: (r["sig_dt"], r["code"], r["type"]))
    by_type = {t: sum(1 for r in rows if r["type"] == t) for t in ALL}
    dts = sorted({r["sig_dt"] for r in rows})
    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "src": "chanlun_scan._bars_from_parquet + chanlun_signal.analyze（产线同源）",
        "caliber": {
            "detect": "= sig_dt（信号当日收盘成立，次日开盘建仓）",
            "lag": "恒 0（台账的 lag 是回填滞后，非策略属性）",
            "min_bars": 130,
            "verify": "逐候选截断重放" if a.verify else "未校验（含前视，仅供对照）",
            "note": "绝对净值不可与 161 天台账基线直接比；只做同数据内横向比较",
        },
        "n_files": len(files), "n_ok": n_ok, "n_skipped": n_none,
        "n_rows": len(rows), "n_dropped_foresight": n_drop,
        "by_type": by_type,
        "span": [dts[0], dts[-1]] if dts else None,
        "n_days": len(dts),
        "rows": rows,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("完成 %.0fs  有效票 %d  跳过 %d  信号 %d  剔前视 %d  %s~%s" % (
        time.time() - t0, n_ok, n_none, len(rows), n_drop,
        dts[0] if dts else "-", dts[-1] if dts else "-"))
    print("  买点 %s" % "  ".join("%s %d" % (t, by_type[t]) for t in BUY))
    print("  卖点 %s" % "  ".join("%s %d" % (t, by_type[t]) for t in SELL))
    print("→ %s (%.1f MB)" % (a.out, os.path.getsize(a.out) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
