# -*- coding: utf-8 -*-
"""多年 PIT 扩样本回填（P0-2：让统计口径可信的唯一手段）。

为什么需要
----------
产线池 `data/chanlun_pool.jsonl` 的有效样本只有 2026-04~2026-09 六个月，且
2026-08 单月占 43%（1503 笔中 644 笔）。在这种样本结构上做的任何阈值/权重
调整都是在拟合那一段行情。本脚本把窗口从产线 `BACKFILL_DAYS=120` 扩到
`--since 20231001`（≈750 个交易日），并**与产线池物理隔离**。

隔离的理由（重要）
------------------
`chanlun_scan._open_positions` 会为池内所有「账本里没有」的行开模拟仓位。
若把 3 年历史行直接写进产能池，下一轮 update_paper 会一次性开出上万笔早已
到期的历史仓位，污染纸面账本与门户推送。故输出到独立文件
`data/chanlun_pool_ext.jsonl`，只供研究/评估消费，产线零影响。

PIT 口径（与产线一致性是本脚本的唯一硬要求）
-------------------------------------------
1. 信号可见性：对每个候选信号 (type, dt)，从 i0+1 起逐根补 bar 重放
   `analyze`，取**最早可见**的 m 作为确认滞后 lag；重放窗口与产线一致取
   尾部 `--bars 500`（产线 LIMIT_BARS）。超过 `--max-lag 60` 仍不可见 → 丢弃
   并计数（这部分是「全窗可见但产线窗口不可见」的信号，本身是发现）。
2. 入场：确认日（dt+lag）收盘价，与产线一致。
3. 出场：确认日之后出现反向信号 → 反向信号确认日收盘提前离场；
   否则满 HOLD=20 个交易日按收盘离场。
4. 基准/alpha：见 `chanlun_alpha`（等权全A，滚动 250 日 beta）。

两种检出模式
------------
`--mode fast`（默认）：全窗 analyze 出候选 → 逐信号重放定 lag。快（≈5min/12核），
    与产线 `--backfill` 的做法同构。
`--mode daily`：逐日重放（每天 analyze 一次尾部窗口），完全复刻产线每日扫描。
    慢 ~7 倍，用于在抽样子集上度量 fast 模式的偏差（`--limit 300`）。

用法
----
    python chanlun_ext_backfill.py --since 20231001 --workers 12
    python chanlun_ext_backfill.py --mode daily --limit 300 --out data/chanlun_pool_ext_daily.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor, as_completed
import atomic_io  # noqa: E402  (P2-c 统一原子写)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
ASHARE = os.path.join(DATA, "ashare")
OUT = os.path.join(DATA, "chanlun_pool_ext.jsonl")
EVAL = os.path.join(DATA, "chanlun_ext_eval.json")
MIN_BARS = 120
HOLD = 20
TYPES = ("1买", "2买", "3买", "1卖", "2卖", "3卖")
FACTORS = ("mbei", "bei", "bei2", "vv", "tbe", "conf", "div_area", "div_dif",
           "pv_state", "pv_fv", "npv")


def _d8(x):
    """日期统一成 8 位无分隔符，用于比较。

    **必须归一后再比**：`bars[*]["dt"]` 是 `2023-09-22`（带横线），而 `--since`
    是 `20230701`（8 位）。直接字符串比较时 `'-'`(0x2D) < `'0'`(0x30)，于是
    「2023-xx-xx」全部被判为 < 「20230701」而静默丢弃——实测让 `--since 20231001`
    只能回出 2024-01-02 之后的数据，把 2023 全年的样本凭空抹掉。
    """
    return str(x or "").replace("-", "")[:8]


def _sign(t):
    return 1.0 if str(t).endswith("买") else -1.0


def _seg(bars, L, win):
    """取 [0, L) 的尾部 win 根（win<=0 表示全窗），复刻产线扫描窗口。"""
    if win and win > 0:
        return bars[max(0, L - win):L]
    return bars[:L]


def _find_lag(bars, i0, sig, win, max_lag, cache=None):
    """从 i0+1 起逐根补 bar，返回该信号最早可见的 m；否则 None。

    cache: {L: set((type, dt))} 票内截断长度→可见信号集。多信号常共用同一
    截断长度（同日确认），命中缓存可省掉重复 analyze（实测量级 30~50%）。
    """
    from chanlun_signal import analyze

    t, dt = sig["type"], sig["dt"]
    for m in range(1, max_lag + 1):
        L = i0 + 1 + m
        if L > len(bars):
            break
        if cache is not None and L in cache:
            keys = cache[L]
        else:
            keys = {(s["type"], s["dt"]) for s in analyze(_seg(bars, L, win))["signals"]}
            if cache is not None:
                cache[L] = keys
        if (t, dt) in keys:
            return m
    return None


def _rows_daily(bars, win, since, max_lag, fname):
    """逐日重放：完全复刻产线每日扫描，用于度量 fast 模式偏差。"""
    from chanlun_signal import analyze

    idx = {b["dt"]: i for i, b in enumerate(bars)}
    seen = set()
    out = []
    for L in range(MIN_BARS, len(bars) + 1):
        st = analyze(_seg(bars, L, win))
        det = bars[L - 1]["dt"]
        if _d8(det) < _d8(since):
            continue
        for s in st["signals"]:
            key = (s["type"], s["dt"])
            if key in seen:
                continue
            i0 = idx.get(s["dt"])
            if i0 is None:
                continue
            lag = L - 1 - i0
            if lag < 1 or lag > max_lag:
                # lag==0 表示当日即可见（非法，产线也要求 m>=1）；超窗丢弃
                continue
            seen.add(key)
            out.append(_mk(bars, fname, s, i0, lag))
    return out


def _mk(bars, fname, sig, i0, lag):
    # 「确认日」语义断言：detect_date 存的是确认日 bars[i0+lag]["dt"]，不是
    # 结构端点 sig["dt"]。全仓 348 处引用都以确认日解释它，但字段名极易被
    # 误读成端点日——误用会让回测凭空获得 14 天（全样本 lag 中位 14、最大 60）
    # 的前视。此处用断言把语义钉死，代价为零。
    assert lag >= 1, "lag 必须 >=1（产线要求 m>=1，lag=0 表示当日可见）"
    assert 0 <= i0 < len(bars) and i0 + lag < len(bars), "lag 越界"
    assert bars[i0]["dt"] == sig["dt"], "i0 与信号结构端点不对应"
    assert bars[i0 + lag]["dt"] >= bars[i0]["dt"], "确认日不得早于结构端点日"
    ei = i0 + lag
    return {
        "code": fname[:-8].replace("_", "."),
        "type": sig["type"],
        "dt": sig["dt"],
        "lag": lag,
        "detect_date": bars[ei]["dt"],
        "price": sig.get("price"),
        "entry_px": bars[ei]["c"],
        "entry_i": ei,
        "depth_bars": len(bars),
        **{k: sig.get(k) for k in FACTORS},
    }


def _rows_fast(bars, win, since, max_lag, fname, stats):
    """全窗出候选 → 逐信号重放定 lag。

    检出用**全窗**（深结构，漏报少），可见性校验用**尾部 win 根**（产线扫描
    窗口）。两者分工的理由：候选若用尾部 500 根检出，就只能覆盖最近约 500
    个交易日，扩样本失效；而可见性必须按当时真实可得的数据判，否则又变成
    未来函数。候选在尾部窗内始终不可见 → 计入 drop_invisible 丢弃。
    """
    from chanlun_signal import analyze

    st = analyze(bars)
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    cache = {}
    out = []
    for s in st["signals"]:
        i0 = idx.get(s["dt"])
        if i0 is None:
            continue
        lag = _find_lag(bars, i0, s, win, max_lag, cache)
        if lag is None:
            stats["drop_invisible"] += 1
            continue
        if _d8(bars[i0 + lag]["dt"]) < _d8(since):
            continue
        out.append(_mk(bars, fname, s, i0, lag))
    return out


def work(job):
    """单票任务：返回 {code, rows, drop, err}。异常不吞，原样上报。"""
    fname, since, win, max_lag, mode = job
    res = {"file": fname, "rows": [], "drop_invisible": 0, "err": None}
    try:
        import chanlun_scan as cs

        bars = cs._bars_from_parquet(os.path.join(ASHARE, fname))
        if not bars or len(bars) < MIN_BARS:
            return res
        if mode == "daily":
            res["rows"] = _rows_daily(bars, win, since, max_lag, fname)
        else:
            st = {"drop_invisible": 0}
            res["rows"] = _rows_fast(bars, win, since, max_lag, fname, st)
            res["drop_invisible"] = st["drop_invisible"]
        for r in res["rows"]:
            r["_bars_len"] = len(bars)
    except Exception as exc:  # 单票失败不阻断全局，但要能看到
        res["err"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return res


def settle(rows, hold=HOLD):
    """按票内时间序列结算：反向信号提前离场 / 满 hold 离场 / 未到期。

    依赖 bars 已不在 worker 内 → 这里只做时序判定，价格与 alpha 在
    `finalize` 里统一取（避免 worker 回传整段 bars 造成 IPC 膨胀）。
    """
    by = {}
    for r in rows:
        by.setdefault(r["code"], []).append(r)
    out = []
    for code, lst in by.items():
        lst.sort(key=lambda x: x["entry_i"])
        marks = [(r["entry_i"], _sign(r["type"])) for r in lst]
        idxs = [m[0] for m in marks]
        for r in lst:
            ei = r["entry_i"]
            my = _sign(r["type"])
            exit_i, reason = None, None
            if ei + hold < r["_bars_len"]:
                exit_i, reason = ei + hold, "hold20"
            k = bisect_right(idxs, ei)  # 反向信号须严格晚于入场
            for j in range(k, len(marks)):
                mi, ms = marks[j]
                if ms == my:
                    continue
                if exit_i is not None and mi >= exit_i:
                    break
                exit_i, reason = mi, "reverse"
                break
            r["exit_i"] = exit_i
            r["exit_reason"] = reason
            out.append(r)
    return out


def finalize(rows, hold=HOLD):
    """补齐出场价、raw、beta、idx_ret、alpha（需要读 parquet）。"""
    import chanlun_alpha as ca
    import chanlun_scan as cs

    bars_cache = {}
    out = []
    for r in rows:
        code = r["code"]
        if code not in bars_cache:
            bars_cache[code] = cs._bars_from_parquet(
                os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
            ) or []
        bars = bars_cache[code]
        ei = r["entry_i"]
        if ei >= len(bars):
            continue
        r["entry_date"] = bars[ei]["dt"]
        r["entry_px"] = bars[ei]["c"]
        xi = r.pop("exit_i", None)
        if xi is None or xi >= len(bars):
            r.update(exit_date=None, exit_px=None, days_held=None,
                     ret=None, status="open")
            out.append(r)
            continue
        move = bars[xi]["c"] / r["entry_px"] - 1.0
        raw = move if _sign(r["type"]) > 0 else -move
        ann = ca.annotate(code, raw, r["entry_date"], bars[xi]["dt"], bars[ei]["dt"])
        r.update(
            exit_date=bars[xi]["dt"],
            exit_px=bars[xi]["c"],
            days_held=xi - ei,
            ret=round(raw, 6),
            status="closed",
            **ann,
        )
        out.append(r)
    return out


def _tstat(a):
    import numpy as np

    a = np.asarray([x for x in a if x is not None], dtype=float)
    n = len(a)
    if n < 2:
        return None
    sd = a.std(ddof=1)
    return round(float(a.mean() / (sd / np.sqrt(n))), 2) if sd > 0 else 0.0


def _blk(vals):
    import numpy as np

    a = np.asarray([v for v in vals if v is not None], dtype=float)
    if not len(a):
        return None
    n = len(a)
    s = np.sort(a)
    trimmed = s[: int(n * 0.95)] if n >= 40 else s  # 剔最顶 5%（左尾保留）
    return {
        "n": n,
        "win": round(float((a > 0).mean()) * 100, 1),
        "avg": round(float(a.mean()) * 100, 2),
        "med": round(float(np.median(a)) * 100, 2),
        "t": _tstat(a),
        "avg_t5": round(float(trimmed.mean()) * 100, 2) if len(trimmed) else None,
    }


def evaluate(rows, tag="ext"):
    """产出 raw / alpha 双口径表，按类型、类型×年、类型×beta 档切分。"""
    closed = [r for r in rows if r.get("status") == "closed" and r.get("ret") is not None]
    out = {"tag": tag, "n_rows": len(rows), "n_closed": len(closed),
           "range": [min((r["entry_date"] for r in closed), default=None),
                     max((r["entry_date"] for r in closed), default=None)],
           "types": {}, "by_year": {}, "by_beta": {}}
    for t in TYPES:
        g = [r for r in closed if r["type"] == t]
        if not g:
            continue
        out["types"][t] = {
            "raw": _blk([r["ret"] for r in g]),
            "alpha": _blk([r.get("alpha") for r in g]),
            "beta_mean": round(float(sum(r.get("beta") or 1.0 for r in g) / len(g)), 3),
            "beta_est_share": round(
                sum(1 for r in g if r.get("beta_est")) * 100.0 / len(g), 1),
            "lag_med": (
                sorted(r["lag"] for r in g if r.get("lag") is not None)[
                    sum(1 for r in g if r.get("lag") is not None) // 2
                ]
                if any(r.get("lag") is not None for r in g) else None
            ),
            "hold20_share": round(
                sum(1 for r in g if r["exit_reason"] == "hold20") * 100.0 / len(g), 1),
        }
        byy = {}
        for r in g:
            byy.setdefault(r["entry_date"][:4], []).append(r)
        out["by_year"][t] = {
            y: {"n": len(v), "raw": _blk([x["ret"] for x in v])["avg"],
                "alpha": (_blk([x.get("alpha") for x in v]) or {}).get("avg"),
                "t": (_blk([x.get("alpha") for x in v]) or {}).get("t")}
            for y, v in sorted(byy.items())
        }
        bands = {"<0.8": [], "0.8-1.2": [], "1.2-1.6": [], ">1.6": []}
        for r in g:
            b = r.get("beta") or 1.0
            k = "<0.8" if b < 0.8 else ("0.8-1.2" if b <= 1.2 else
                                        ("1.2-1.6" if b <= 1.6 else ">1.6"))
            bands[k].append(r)
        out["by_beta"][t] = {
            k: {"n": len(v),
                "alpha": (_blk([x.get("alpha") for x in v]) or {}).get("avg"),
                "t": (_blk([x.get("alpha") for x in v]) or {}).get("t")}
            for k, v in bands.items() if v
        }
    return out


def _write_jsonl(path, rows):
    with atomic_io.replacing(path) as f:
        for r in rows:
            # 下划线开头是内部字段（_bars_len 等），不落盘
            f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")},
                               ensure_ascii=False) + "\n")


def refix_hold(path_in, path_out, path_eval, hold=HOLD):
    """后处理：把已回填结果改成**固定持有 hold 日**出场（不重跑信号检测）。

    为什么需要：扩样本自带稠密反向信号集，`settle` 的反向离场会比产线（池内
    稀疏信号）早得多，导致同一批信号在扩样本里持有期偏短，与产线账本不可比。
    固定持有口径消除这条路径依赖，才能把「信号质量」与「离场规则」分开看。
    """
    import chanlun_scan as cs

    rows = [json.loads(li) for li in open(path_in, encoding="utf-8") if li.strip()]
    cache = {}
    for r in rows:
        code = r["code"]
        if code not in cache:
            cache[code] = cs._bars_from_parquet(
                os.path.join(ASHARE, code.replace(".", "_") + ".parquet")) or []
        bars = cache[code]
        ei = next((i for i, b in enumerate(bars) if b["dt"] == r.get("entry_date")), None)
        if ei is None:
            r["status"] = "unmapped"
            continue
        r["entry_i"] = ei
        r["_bars_len"] = len(bars)
        r["exit_i"] = ei + hold if ei + hold < len(bars) else None
        r["exit_reason"] = "hold%d" % hold if r["exit_i"] is not None else None
    rows = finalize(rows, hold)
    _write_jsonl(path_out, rows)
    ev = evaluate(rows, tag="refix-hold%d|%s" % (hold, os.path.basename(path_in)))
    with atomic_io.replacing(path_eval) as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    print("== %s ==" % ev["tag"])
    print("%-4s %6s %8s %7s %6s %8s %7s" % ("type", "n", "raw%", "t_raw", "win%",
                                            "alpha%", "t_alp"))
    for t, v in ev["types"].items():
        rw, al = v["raw"], v["alpha"]
        print("%-4s %6d %+8.2f %7.2f %6.1f %+8.2f %7s"
              % (t, rw["n"], rw["avg"], rw["t"] or 0, rw["win"],
                 al["avg"] if al else 0, (al or {}).get("t")))
    print("-> %s\n-> %s" % (path_out, path_eval))
    return ev


def main():
    ap = argparse.ArgumentParser(description="chanlun 多年 PIT 扩样本回填")
    ap.add_argument("--since", default="20231001", help="确认日下限 YYYYMMDD")
    ap.add_argument("--bars", type=int, default=500,
                    help="重放窗口（产线 LIMIT_BARS=500；0=全窗）")
    ap.add_argument("--max-lag", type=int, default=60)
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（抽样验证）")
    ap.add_argument("--mode", choices=("fast", "daily"), default="fast")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--eval", default=EVAL)
    ap.add_argument("--shard", default="",
                    help="i/n 分片（交错 files[i::n]，避免按代码前缀造成板块偏置）")
    ap.add_argument("--merge", default="",
                    help="逗号分隔的已结算 jsonl，合并后只做评估（不重算）")
    ap.add_argument("--refix-hold", action="store_true",
                    help="不重跑检测，把 --out 里已有结果改成固定持有口径")
    a = ap.parse_args()

    if a.merge:
        rows = []
        for p in a.merge.split(","):
            rows += [json.loads(li) for li in open(p.strip(), encoding="utf-8") if li.strip()]
        ev = evaluate(rows, tag="merge|%d files" % len(a.merge.split(",")))
        with atomic_io.replacing(a.eval) as f:
            json.dump(ev, f, ensure_ascii=False, indent=1)
        print("== %s == rows=%d closed=%d range=%s~%s"
              % (ev["tag"], len(rows), ev["n_closed"], ev["range"][0], ev["range"][1]))
        print("%-4s %6s %8s %7s %6s %8s %7s" % ("type", "n", "raw%", "t_raw",
                                                "win%", "alpha%", "t_alp"))
        for t, v in ev["types"].items():
            rw, al = v["raw"], v["alpha"]
            print("%-4s %6d %+8.2f %7.2f %6.1f %+8.2f %7s"
                  % (t, rw["n"], rw["avg"], rw["t"] or 0, rw["win"],
                     al["avg"] if al else 0, (al or {}).get("t")))
        print("-> %s" % a.eval)
        return

    if a.refix_hold:
        refix_hold(a.out, a.out.replace(".jsonl", "_fixhold.jsonl"),
                   a.eval.replace(".json", "_fixhold.json"), a.hold)
        return

    files = sorted(f for f in os.listdir(ASHARE) if f.endswith(".parquet"))
    files = [f for f in files if f[:-8] not in ("000001_SH",)]
    if a.limit:
        step = max(1, len(files) // a.limit)
        files = files[::step][: a.limit]
    if a.shard:
        sh, tot = (int(x) for x in a.shard.split("/"))
        files = files[sh::tot]
    print("ext backfill: files=%d since=%s win=%s mode=%s workers=%d"
          % (len(files), a.since, a.bars or "full", a.mode, a.workers))

    a.since = _d8(a.since)
    jobs = [(f, a.since, a.bars, a.max_lag, a.mode) for f in files]
    t0 = time.time()
    rows, errs, drop_inv, done = [], [], 0, 0
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(work, j): j[0] for j in jobs}
        for fu in as_completed(futs):
            done += 1
            r = fu.result()
            rows.extend(r["rows"])
            drop_inv += r["drop_invisible"]
            if r["err"]:
                errs.append((r["file"], r["err"]))
            if done % 200 == 0:
                print("  ..%d/%d files rows=%d drop_invisible=%d %.0fs"
                      % (done, len(files), len(rows), drop_inv, time.time() - t0))
            if done % 1000 == 0:
                # 浅拷贝：settle/finalize 会就地改写 row（exit_i/entry_px），
                # 直接传原引用会让后续完整结算拿不到 _bars_len（已踩过一次）
                _write_jsonl(a.out, finalize(settle([dict(r) for r in rows], a.hold), a.hold))
                print("  checkpoint -> %s" % a.out)

    rows = finalize(settle(rows, a.hold), a.hold)
    _write_jsonl(a.out, rows)
    ev = evaluate(rows, tag="%s|since=%s|win=%s|hold=%d"
                  % (a.mode, a.since, a.bars or "full", a.hold))
    ev["elapsed_s"] = round(time.time() - t0, 1)
    ev["drop_invisible"] = drop_inv
    ev["errors"] = errs[:20]
    with atomic_io.replacing(a.eval) as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)

    print("\n== %s ==" % ev["tag"])
    print("rows=%d closed=%d range=%s~%s drop_invisible=%d errs=%d %.0fs"
          % (len(rows), ev["n_closed"], ev["range"][0], ev["range"][1],
             drop_inv, len(errs), ev["elapsed_s"]))
    print("%-4s %5s %7s %8s %6s %7s %8s %6s %6s %5s"
          % ("type", "n", "raw%", "t_raw", "win%", "alpha%", "t_alpha", "med%",
             "beta", "lag"))
    for t, v in ev["types"].items():
        rw, al = v["raw"], v["alpha"]
        print("%-4s %5d %+7.2f %8.2f %6.1f %+7.2f %8s %6.2f %6.2f %5d"
              % (t, rw["n"], rw["avg"], rw["t"] or 0, rw["win"],
                 al["avg"] if al else 0, (al or {}).get("t"), rw["med"],
                 v["beta_mean"], v["lag_med"]))
    print("-> %s\n-> %s" % (a.out, a.eval))


if __name__ == "__main__":
    main()
