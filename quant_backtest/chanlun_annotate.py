# -*- coding: utf-8 -*-
"""缠论结构标注产物（ann-v1）：把「看得见的结构」导出成可复现的标注行。

定位
----
系统经七轮审计已判定「缠论结构不是收益信号」（18 格六信号 × 三持期全卡三道
闸，扣成本净 alpha FM t≈0）。本模块因此**不是信号层**，而是结构标注工具：
画图 / 人工研判辅助 / 回测对照组。它不产生方向判断，只如实回答「第 d0 天
按当天收盘可见的数据，结构长什么样、每个结构是哪天第一次被看见」。

三条语义铁律（写错即失去全部价值）
--------------------------------
1. **严格截断**：第 d0 行的全部内容只用 ``bars[:d0+1]`` 计算。实现方式是
   逐根重放（每个前缀长度跑一次 ``chanlun_signal.analyze``），不做任何
   「用全窗结果回填历史」的优化——那种优化正是前视的来源。
2. **确认日口径**：``visible_at`` = 该结构**首次可见**的那根 bar 的 dt，
   与 ``chanlun_ext_backfill._mk`` 的 ``detect_date = bars[i0+lag]["dt"]``
   完全同义：它是确认日，不是结构端点日。``lag_days = visible_at 索引 -
   结构端点索引``，恒 >= 1（见下）。
3. **幂等**：同参数两次运行的 jsonl 逐字节相同。标注行内不含任何时间戳；
   ``generated_at`` / 耗时只写入 ``<out>.meta.json`` 侧车。

lag_days >= 1 是怎么保证的
-------------------------
逐根重放时，某结构在其端点那根 bar 上（lag=0）不算「已确认」——这与
``chanlun_ext_backfill`` 的 ``assert lag >= 1``（产线要求 m>=1）一致。
故首次可见日取「端点之后、且该结构仍存在」的第一根 bar：对每个结构身份
（fractal: type+dt / bi: s_dt+e_dt / pivot: s_dt+e_dt+zg+zd / point:
type+dt）记录 ``first_seen``，先扫描到 L 满足 ``L > 端点索引`` 才落账。
若某结构只在端点当天出现过、次日就消失，则它从未被确认，本模块不输出它
（宁缺毋滥：一条无法在最早期次日复核的结构，画在图上就是幻觉）。

结构身份与「修正」
----------------
笔终点随行情延伸会改写（``chanlun_bi.find_bis`` 里同性分型取更极值），
中枢 end_dt 也会延长。这些修正产生**新的身份**，因此有各自的 first_seen：
旧身份留在它第一次可见的那天，新身份从修正可见的那天起出现。行内只放
「截至 d0 仍存在」的结构，不会出现同一结构两份。

与 ``chanlun_ext_backfill`` 的两点**有意**差异（不是口径漂移）
--------------------------------------------------------------
1. 重放窗口：回填用尾部 ``--bars 500`` 根（复刻产线每日扫描窗口），本模块
   用**全部前置历史**。标注要回答「结构哪天第一次看得见」，用产线窗口会把
   「因为窗口太短而看不见」误记成「那天才看得见」。
2. 无 lag 上限：回填 ``--max-lag 60`` 之外直接丢弃（产线不交易），本模块不设
   上限——结构确实可见就是可见，只是滞后 66 天而已，丢掉等于篡改标注。
   实测 600519 2025 年 6 个买卖点里 5 个与 ``_find_lag``（win=500, max_lag=60）
   确认日完全一致，唯一差异就是那个 lag=66 被回填丢弃的点。

行内结构取舍
------------
``--window``（默认 500，与产线 ``chanlun_scan.LIMIT_BARS`` 同量级）只保留
端点索引 >= d0-window 的结构，避免每行重复回放三年历史把文件撑爆；
``--window 0`` = 全部历史。**窗口只影响「报什么」，不影响 first_seen**：
确认日始终用全历史重放得出。

字段
----
每行（compact JSON，键序固定）::

    {"schema_version":"ann-v1","code":"600519.SH","date":"2025-01-03",
     "visible_at":"2025-01-02","lag_days":1,
     "fractals":[{"type","dt","price","visible_at","lag_days"}],
     "bi":[{"i0_dt","i1_dt","dir","high","low","confirm_dt","lag_days"}],
     "pivots":[{"zg","zd","start_dt","end_dt","n_bi","confirm_dt","lag_days"}],
     "points":[{"type","dt","price","detect_date","lag"}]}

行级 ``visible_at`` / ``lag_days`` = 本行**最新确认**的那个结构事件的确认日
与其滞后（即「本行结构相对上一行有没有变化、变化滞后几天」）。行内一个结构
都没有时二者为 ``null``（真实数据在 120 根热身之后不会出现）。

用法
----
::

    python chanlun_annotate.py --code 600519 --start 2025-01-01 --end 2025-12-31
    python chanlun_annotate.py --code 600519.SH --window 0 --out data/ann_519_full.jsonl
    python chanlun_annotate.py --limit 50 --workers 8

产物只写 ``data/`` 下；批量模式各 worker 先写 ``data/_ann_parts/`` 再按代码序
合并，合并后删除自己的临时分片（不动任何既有文件）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import atomic_io  # noqa: E402  (P2-c 统一原子写)

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DATA = os.path.join(HERE, "data")
PARTS = os.path.join(DATA, "_ann_parts")

SCHEMA_VERSION = "ann-v1"
MIN_BARS = 120      # 与 chanlun_scan.MIN_BARS 同口径：不足 120 根不判结构
WINDOW_DEFAULT = 500  # 产线 chanlun_scan.LIMIT_BARS 同量级；0=全部历史
PRICE_ND = 4        # 价格小数位（复权价原始位数更多，统一截断保证可比）

# --market 取值 → chanlun_scan 市场代码（仓库内市场键是 CN/US/HK，
# 数据目录分别是 data/ashare、data/us、data/hk）
MARKETS = {
    "ashare": "CN", "cn": "CN", "CN": "CN",
    "us": "US", "US": "US",
    "hk": "HK", "HK": "HK",
}


# ── 小工具 ────────────────────────────────────────────────
def _norm_date(s):
    """'2025-01-01' / '20250101' / None → 'YYYY-MM-DD' / None。"""
    if s is None or s == "":
        return None
    t = str(s).strip().replace("-", "")
    if len(t) != 8 or not t.isdigit():
        raise ValueError("日期须为 YYYY-MM-DD 或 YYYYMMDD：%r" % (s,))
    return "%s-%s-%s" % (t[:4], t[4:6], t[6:])


def _px(v):
    """价格 → 固定小数位的 float（None 透传）。"""
    return None if v is None else round(float(v), PRICE_ND)


def _events(st, idx):
    """一次 analyze 结果 → [(结构身份, 端点索引)]。

    身份刻意是「人类可复核的最小键」：同一身份在任意截断下都指向同一段
    结构；结构被行情修正（笔终点延伸 / 中枢延长）即换身份。
    """
    out = []
    for x in st.get("fractals") or []:
        e = idx.get(x["dt"])
        if e is not None:
            out.append((("f", x["type"], x["dt"]), e))
    for b in st.get("bis") or []:
        e = idx.get(b["e_dt"])
        if e is not None:
            out.append((("b", b["s_dt"], b["e_dt"]), e))
    for p in st.get("pivots") or []:
        e = idx.get(p["e_dt"])
        if e is not None:
            out.append((("p", p["s_dt"], p["e_dt"], p["zg"], p["zd"]), e))
    for s in st.get("signals") or []:
        e = idx.get(s["dt"])
        if e is not None:
            out.append((("s", s["type"], s["dt"]), e))
    return out


def _mk_row(code, bars, d0, idx, st, first, window):
    """第 d0 行：只用 st（= analyze(bars[:d0+1])）与 first（历史重放结论）。"""
    lo = d0 - window if window and window > 0 else -1
    fresh = None  # (确认索引, 端点索引, 类型序)
    frs, bis, pvs, pts = [], [], [], []

    def _seen(key, e):
        v = first.get(key)
        if v is None or e < lo:
            return None
        return v

    for x in st.get("fractals") or []:
        e = idx.get(x["dt"])
        if e is None:
            continue
        v = _seen(("f", x["type"], x["dt"]), e)
        if v is None:
            continue
        frs.append({
            "type": x["type"], "dt": x["dt"], "price": _px(x["price"]),
            "visible_at": bars[v]["dt"], "lag_days": v - e,
        })
        if fresh is None or (v, e) > (fresh[0], fresh[1]):
            fresh = (v, e, 0)

    for b in st.get("bis") or []:
        e = idx.get(b["e_dt"])
        if e is None:
            continue
        v = _seen(("b", b["s_dt"], b["e_dt"]), e)
        if v is None:
            continue
        sp, ep = b["s_price"], b["e_price"]
        bis.append({
            "i0_dt": b["s_dt"], "i1_dt": b["e_dt"], "dir": int(b["dir"]),
            "high": _px(max(sp, ep)), "low": _px(min(sp, ep)),
            "confirm_dt": bars[v]["dt"], "lag_days": v - e,
        })
        if fresh is None or (v, e) > (fresh[0], fresh[1]):
            fresh = (v, e, 1)

    for p in st.get("pivots") or []:
        e = idx.get(p["e_dt"])
        if e is None:
            continue
        v = _seen(("p", p["s_dt"], p["e_dt"], p["zg"], p["zd"]), e)
        if v is None:
            continue
        pvs.append({
            "zg": _px(p["zg"]), "zd": _px(p["zd"]),
            "start_dt": p["s_dt"], "end_dt": p["e_dt"],
            "n_bi": int(p["n_bi"]),
            "confirm_dt": bars[v]["dt"], "lag_days": v - e,
        })
        if fresh is None or (v, e) > (fresh[0], fresh[1]):
            fresh = (v, e, 2)

    for s in st.get("signals") or []:
        e = idx.get(s["dt"])
        if e is None:
            continue
        v = _seen(("s", s["type"], s["dt"]), e)
        if v is None:
            continue
        pts.append({
            "type": s["type"], "dt": s["dt"], "price": _px(s.get("price")),
            "detect_date": bars[v]["dt"], "lag": v - e,
        })
        if fresh is None or (v, e) > (fresh[0], fresh[1]):
            fresh = (v, e, 3)

    return {
        "schema_version": SCHEMA_VERSION,
        "code": code,
        "date": bars[d0]["dt"],
        "visible_at": None if fresh is None else bars[fresh[0]]["dt"],
        "lag_days": None if fresh is None else fresh[0] - fresh[1],
        "fractals": frs,
        "bi": bis,
        "pivots": pvs,
        "points": pts,
    }


def annotate_bars(bars, code="", start=None, end=None, window=WINDOW_DEFAULT,
                  stats=None):
    """升序 bars → 区间内每根 bar 一行标注（纯函数，无全局状态）。

    返回 (rows, stats)。``rows`` 是 dict 列表，``stats`` 是本次运行的
    计数（行数 / 各类结构条数 / lag 分布 / 各结构类型点数），供 CLI 汇报。
    """
    import chanlun_signal as csig

    bars = bars or []
    if len(bars) <= MIN_BARS:
        raise ValueError("bars=%d 不足 %d 根，无法判定结构（次新股/数据缺口）"
                         % (len(bars), MIN_BARS + 1))
    idx = {b["dt"]: i for i, b in enumerate(bars)}
    s0, s1 = _norm_date(start), _norm_date(end)

    i_first = 0 if s0 is None else next(
        (i for i, b in enumerate(bars) if b["dt"] >= s0), None)
    i_last = len(bars) - 1 if s1 is None else max(
        (i for i, b in enumerate(bars) if b["dt"] <= s1), default=None)
    acc = stats if stats is not None else {}
    acc.setdefault("bars_total", len(bars))
    acc.setdefault("bars_in_range", 0)
    acc.setdefault("rows", 0)
    acc.setdefault("skipped_warmup", 0)
    acc.setdefault("empty_rows", 0)
    acc.setdefault("n_fractals", 0)
    acc.setdefault("n_bi", 0)
    acc.setdefault("n_pivots", 0)
    acc.setdefault("n_points", 0)
    acc.setdefault("lags", [])
    acc.setdefault("point_types", {})
    if i_first is None or i_last is None or i_last < i_first:
        return [], acc

    acc["bars_in_range"] = i_last - i_first + 1
    d0_min = max(i_first, MIN_BARS)
    acc["skipped_warmup"] += max(0, min(d0_min, i_last + 1) - i_first)
    if d0_min > i_last:
        return [], acc

    first = {}  # 结构身份 → 首次可见索引（全历史重放结论，跨行复用）
    rows = []
    # 从最小可判结构的前缀开始重放：前缀长度 m，最后一根索引 L=m-1。
    # 起点取 3 而非 MIN_BARS，是为了让端点很早的结构也能拿到真实 first_seen
    #（否则它们的 lag 会被热身边界拉长）。
    for m in range(3, i_last + 2):
        st = csig.analyze(bars[:m])
        L = m - 1
        for key, e in _events(st, idx):
            if key not in first and L > e:  # lag_days>=1：端点当天不算确认
                first[key] = L
        if L < d0_min:
            continue
        row = _mk_row(code, bars, L, idx, st, first, window)
        if not (row["fractals"] or row["bi"] or row["pivots"] or row["points"]):
            acc["empty_rows"] += 1
        rows.append(row)
        acc["rows"] += 1
        acc["n_fractals"] += len(row["fractals"])
        acc["n_bi"] += len(row["bi"])
        acc["n_pivots"] += len(row["pivots"])
        acc["n_points"] += len(row["points"])
        if row["lag_days"] is not None:
            acc["lags"].append(row["lag_days"])
        for x in row["fractals"]:
            acc["lags"].append(x["lag_days"])
        for x in row["bi"]:
            acc["lags"].append(x["lag_days"])
        for x in row["pivots"]:
            acc["lags"].append(x["lag_days"])
        for x in row["points"]:
            acc["lags"].append(x["lag"])
            acc["point_types"][x["type"]] = acc["point_types"].get(x["type"], 0) + 1
    return rows, acc


# ── 数据路径 ─────────────────────────────────────────────
def _market_ctx(market):
    from chanlun_scan import MKT

    key = MARKETS.get(str(market).strip(), None)
    if key is None:
        raise SystemExit("未知市场 %r（可选：ashare/CN、us、hk）" % (market,))
    cfg = MKT[key]
    return key, os.path.join(DATA, cfg["dir"]), cfg["restore_code"]


def _code_of_fname(fname, restore_code):
    """文件名 → 规范代码。CN 走 ts_code 形态（600519_SH → 600519.SH），
    与池 / 回填 / alpha 全链一致；US/HK 文件名即代码，原样返回。"""
    stem = fname[:-8] if fname.endswith(".parquet") else fname
    return stem.replace("_", ".") if restore_code else stem


def _resolve_file(mdir, code):
    """'600519' / '600519.SH' / '600519_SH' → parquet 路径。"""
    base = str(code).strip()
    for cand in (base, base.replace(".", "_"), base.upper(), base.upper().replace(".", "_")):
        p = os.path.join(mdir, cand + ".parquet")
        if os.path.isfile(p):
            return p
    stem = base.split(".")[0]
    hits = sorted(f for f in os.listdir(mdir)
                  if f.endswith(".parquet") and f.split("_")[0] == stem)
    if len(hits) == 1:
        return os.path.join(mdir, hits[0])
    if not hits:
        raise SystemExit("找不到代码 %r 的 parquet（目录 %s）" % (code, mdir))
    raise SystemExit("代码 %r 有歧义：%s" % (code, ", ".join(hits[:5])))


def _jsonl_line(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


def _write_rows(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(_jsonl_line(r) + "\n")
    os.replace(tmp, path)


def _write_meta(path, meta):
    with atomic_io.replacing(path) as f:
        json.dump(meta, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def _summarize(acc, elapsed):
    """统计 → 可打印摘要（数字全部来自真实累加，不做估计）。"""
    n = acc.get("rows") or 0
    lags = sorted(acc.get("lags") or [])

    def _avg(k):
        return (acc.get(k) or 0) / n if n else 0.0

    return {
        "rows": n,
        "elapsed_s": round(elapsed, 2),
        "bars_total": acc.get("bars_total"),
        "bars_in_range": acc.get("bars_in_range", 0),
        "skipped_warmup": acc.get("skipped_warmup", 0),
        "empty_rows": acc.get("empty_rows", 0),
        "avg_per_bar": {
            "fractals": round(_avg("n_fractals"), 2),
            "bi": round(_avg("n_bi"), 2),
            "pivots": round(_avg("n_pivots"), 2),
            "points": round(_avg("n_points"), 2),
        },
        "n_struct": {
            "fractals": acc.get("n_fractals", 0),
            "bi": acc.get("n_bi", 0),
            "pivots": acc.get("n_pivots", 0),
            "points": acc.get("n_points", 0),
        },
        "lag_days": {
            "n": len(lags),
            "min": lags[0] if lags else None,
            "median": lags[len(lags) // 2] if lags else None,
            "p90": lags[int(len(lags) * 0.9)] if lags else None,
            "max": lags[-1] if lags else None,
        },
        "point_types": dict(sorted((acc.get("point_types") or {}).items())),
    }


def _raw_rows(path):
    """parquet 原始行数（仅用于把失败原因写准，不影响任何计算）。"""
    try:
        import pandas as pd

        return int(len(pd.read_parquet(path, columns=["trade_date"])))
    except Exception:
        return None


# ── worker ───────────────────────────────────────────────
def _work(job):
    """单票任务：跑标注 → 写分片 → 回传统计。异常不吞，原样上报。"""
    market, path, code, start, end, window, part = job
    t0 = time.time()
    res = {"code": code, "rows": 0, "elapsed_s": 0.0, "err": None, "stat": None,
           "lags": []}
    try:
        import chanlun_scan as cs

        cs.set_market(MARKETS[market])
        bars = cs._bars_from_parquet(path)
        if not bars:
            # _bars_from_parquet 在「清洗后不足 MIN_BARS」时也返回 None，
            # 直接写「读不出」会把次新股误报成数据损坏——把原始行数带出来。
            res["err"] = ("有效 bar 不足：_bars_from_parquet=None（parquet 原始行 %s，"
                          "需清洗后 >%d 根；次新/停牌全滤/字段缺失）"
                          % (_raw_rows(path), MIN_BARS))
            return res
        if len(bars) <= MIN_BARS:
            res["err"] = "有效 bar=%d <= MIN_BARS=%d（次新/缺口）" % (len(bars), MIN_BARS)
            return res
        rows, acc = annotate_bars(bars, code=code, start=start, end=end,
                                  window=window)
        _write_rows(part, rows)
        res["rows"] = len(rows)
        res["stat"] = _summarize(acc, time.time() - t0)
        # 原始 lag 样本回传（批量汇总要按合并后的全样本算分位，不能平均分位）
        res["lags"] = acc.get("lags") or []
        if not rows:
            # 0 行有两种完全不同的原因，汇报时必须分开（都不是失败）
            res["zero_reason"] = ("warmup" if (res["stat"]["skipped_warmup"] or 0) > 0
                                  else "no_bars")
    except Exception as exc:  # 单票失败不阻断全局，但必须能被看到
        res["err"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    res["elapsed_s"] = round(time.time() - t0, 2)
    return res


def _default_out(code, batch):
    if batch:
        return os.path.join(DATA, "chanlun_annotate_batch.jsonl")
    return os.path.join(DATA, "chanlun_annotate_%s.jsonl"
                        % str(code).replace(".", "_"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="缠论结构标注导出（ann-v1）：每根 bar 一行，严格截断 + 确认日口径",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python chanlun_annotate.py --code 600519 --start 2025-01-01 --end 2025-12-31\n"
               "  python chanlun_annotate.py --limit 50 --workers 8 --window 250\n")
    ap.add_argument("--code", default="", help="单票代码（600519 / 600519.SH）")
    ap.add_argument("--market", default="ashare", help="ashare(默认)/us/hk")
    ap.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（含）")
    ap.add_argument("--end", default=None, help="结束日 YYYY-MM-DD（含）")
    ap.add_argument("--out", default="", help="输出 jsonl（默认 data/ 下）")
    ap.add_argument("--window", type=int, default=WINDOW_DEFAULT,
                    help="行内只保留端点距今 <= N 根的结构（0=全部历史）")
    ap.add_argument("--limit", type=int, default=0, help="批量只跑 N 只（等距抽样）")
    ap.add_argument("--pool", default="", help="批量票池（默认市场目录全部 parquet）")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)

    market, mdir, restore = _market_ctx(a.market)
    batch = not a.code
    out = a.out or _default_out(a.code, batch)
    t0 = time.time()

    if not batch:
        path = _resolve_file(mdir, a.code)
        code = _code_of_fname(os.path.basename(path), restore)
        res = _work((market, path, code, a.start, a.end, a.window, out))
        if res["err"]:
            raise SystemExit("失败 %s: %s" % (code, res["err"]))
        stat = res["stat"]
        meta = {"schema_version": SCHEMA_VERSION, "generated_at":
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mode": "single", "code": code, "market": market,
                "start": _norm_date(a.start), "end": _norm_date(a.end),
                "window": a.window, "out": out, "stat": stat}
        _write_meta(out + ".meta.json", meta)
        print("== ann-v1 %s ==" % code)
        print("输入 %s  bar 数 %d  区间内 bar %d  区间 %s~%s  window=%d"
              % (os.path.basename(path), stat["bars_total"], stat["bars_in_range"],
                 _norm_date(a.start) or "首根", _norm_date(a.end) or "末根", a.window))
        print("行数 %d  耗时 %.2fs  热身跳过 %d  空行 %d"
              % (stat["rows"], stat["elapsed_s"], stat["skipped_warmup"],
                 stat["empty_rows"]))
        print("每根 bar 平均条数：分型 %.2f / 笔 %.2f / 中枢 %.2f / 买卖点 %.2f"
              % (stat["avg_per_bar"]["fractals"], stat["avg_per_bar"]["bi"],
                 stat["avg_per_bar"]["pivots"], stat["avg_per_bar"]["points"]))
        print("lag_days(n=%d) min=%s 中位=%s p90=%s max=%s"
              % (stat["lag_days"]["n"], stat["lag_days"]["min"],
                 stat["lag_days"]["median"], stat["lag_days"]["p90"],
                 stat["lag_days"]["max"]))
        print("买卖点类型分布 %s" % (stat["point_types"] or "{}"))
        if stat["rows"] == 0:
            why = ("区间内 %d 根 bar 全部落在热身期（需前置历史 > %d 根）"
                   % (stat["bars_in_range"], MIN_BARS) if stat["skipped_warmup"]
                   else "区间内没有 bar（停牌/退市/未上市）")
            print("注意：%s，输出为空文件" % why)
        print("-> %s\n-> %s.meta.json" % (out, out))
        return 0

    if a.pool:
        if os.path.isdir(a.pool):
            files = sorted(f for f in os.listdir(a.pool) if f.endswith(".parquet"))
            mdir = a.pool
        else:
            with open(a.pool, encoding="utf-8") as f:
                files = [li.strip() for li in f if li.strip()
                         and not li.strip().startswith("#")]
    else:
        files = sorted(f for f in os.listdir(mdir) if f.endswith(".parquet"))
    files = [f for f in files if not f.startswith(".")]
    if a.limit and a.limit < len(files):
        # 等距抽样：连号前缀会让样本偏成单一板块（SH 60xxxx），与回填同做法
        step = max(1, len(files) // a.limit)
        files = files[::step][: a.limit]
    print("ann-v1 批量：%d 只  workers=%d  window=%d  区间 %s~%s"
          % (len(files), a.workers, a.window, _norm_date(a.start) or "首根",
             _norm_date(a.end) or "末根"))

    os.makedirs(PARTS, exist_ok=True)
    jobs = []
    for f in files:
        p = f if os.path.isabs(f) else os.path.join(mdir, f)
        code = _code_of_fname(os.path.basename(p), restore)
        jobs.append((market, p, code, a.start, a.end, a.window,
                     os.path.join(PARTS, code.replace(".", "_") + ".jsonl")))

    ok, bad, total_rows, stats = [], [], 0, []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(_work, j): j for j in jobs}
        done = 0
        for fu in as_completed(futs):
            r = fu.result()
            done += 1
            if r["err"]:
                bad.append((r["code"], r["err"]))
            else:
                ok.append(r)
                total_rows += r["rows"]
                stats.append(r["stat"])
            if done % 10 == 0 or done == len(jobs):
                print("  ..%d/%d 成功=%d 失败=%d 行=%d %.0fs"
                      % (done, len(jobs), len(ok), len(bad), total_rows,
                         time.time() - t0))

    # 按代码序合并（as_completed 顺序不定，必须排序才能幂等）
    ok.sort(key=lambda r: r["code"])
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fo:
        for r in ok:
            part = os.path.join(PARTS, r["code"].replace(".", "_") + ".jsonl")
            with open(part, encoding="utf-8") as fi:
                shutil.copyfileobj(fi, fo)
    os.replace(tmp, out)
    if os.path.isdir(PARTS):
        shutil.rmtree(PARTS, ignore_errors=True)  # 只删本次自建的分片目录

    agg = {"rows": 0, "lags": [], "n_fractals": 0, "n_bi": 0, "n_pivots": 0,
           "n_points": 0, "point_types": {}, "bars_total": 0,
           "skipped_warmup": 0, "empty_rows": 0}
    for r, s in zip(ok, stats):
        agg["rows"] += s["rows"]
        agg["lags"].extend(r.get("lags") or [])
        agg["n_fractals"] += s["n_struct"]["fractals"]
        agg["n_bi"] += s["n_struct"]["bi"]
        agg["n_pivots"] += s["n_struct"]["pivots"]
        agg["n_points"] += s["n_struct"]["points"]
        agg["bars_total"] += s["bars_total"] or 0
        agg["skipped_warmup"] += s["skipped_warmup"]
        agg["empty_rows"] += s["empty_rows"]
        for k, v in s["point_types"].items():
            agg["point_types"][k] = agg["point_types"].get(k, 0) + v
    elapsed = time.time() - t0
    summ = _summarize(agg, elapsed)
    summ["n_ok"] = len(ok)
    summ["n_fail"] = len(bad)
    summ["n_empty"] = sum(1 for r in ok if r["rows"] == 0)
    summ["empty_codes"] = [r["code"] for r in ok if r["rows"] == 0]
    summ["empty_warmup"] = [r["code"] for r in ok
                            if r["rows"] == 0 and r.get("zero_reason") == "warmup"]
    summ["empty_no_bars"] = [r["code"] for r in ok
                             if r["rows"] == 0 and r.get("zero_reason") != "warmup"]
    summ["failures"] = [{"code": c, "err": e} for c, e in bad]
    meta = {"schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": "batch", "market": market,
            "start": _norm_date(a.start), "end": _norm_date(a.end),
            "window": a.window, "workers": a.workers, "out": out,
            "pool_n": len(files), "stat": summ,
            "per_code": [{"code": s["code"], "rows": s["rows"],
                          "elapsed_s": s["elapsed_s"]} for s in ok]}
    _write_meta(out + ".meta.json", meta)

    print("\n== 汇总 ==")
    print("成功 %d / 失败 %d / 请求 %d  行数 %d  总耗时 %.1fs"
          % (len(ok), len(bad), len(files), summ["rows"], elapsed))
    if ok:
        print("每票平均耗时 %.2fs（min %.2f / max %.2f）"
              % (sum(r["elapsed_s"] for r in ok) / len(ok),
                 min(r["elapsed_s"] for r in ok), max(r["elapsed_s"] for r in ok)))
        print("每根 bar 平均条数：分型 %.2f / 笔 %.2f / 中枢 %.2f / 买卖点 %.2f"
              % (summ["avg_per_bar"]["fractals"], summ["avg_per_bar"]["bi"],
                 summ["avg_per_bar"]["pivots"], summ["avg_per_bar"]["points"]))
        print("lag_days(n=%d) min=%s 中位=%s p90=%s max=%s"
              % (summ["lag_days"]["n"], summ["lag_days"]["min"],
                 summ["lag_days"]["median"], summ["lag_days"]["p90"],
                 summ["lag_days"]["max"]))
        print("买卖点类型分布 %s" % (summ["point_types"] or "{}"))
    for c, e in bad[:20]:
        print("  FAIL %s  %s" % (c, e))
    if summ["n_empty"]:
        if summ["empty_warmup"]:
            print("  区间内 bar 全落在热身期（前置历史 < %d 根，非失败）%d 只：%s"
                  % (MIN_BARS, len(summ["empty_warmup"]),
                     ", ".join(summ["empty_warmup"])))
        if summ["empty_no_bars"]:
            print("  区间内无 bar（停牌/退市/未上市，非失败）%d 只：%s"
                  % (len(summ["empty_no_bars"]), ", ".join(summ["empty_no_bars"])))
    print("-> %s\n-> %s.meta.json" % (out, out))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
