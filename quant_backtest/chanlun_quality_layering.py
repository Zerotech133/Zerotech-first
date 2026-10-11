# -*- coding: utf-8 -*-
"""chanlun 买点质量分层 · 离线回测调研（2026-10-11，T1 优化计划续篇）。

背景：strong 时间窗共振 D4 已关闭（交集 1.6x<3x 且收益劣）。本调研
转向 chanlun 腿自身：买点池 3526 事件按信号特征分层，找显著占优的
高质量层，供 T1/清单加权或过滤参考。离线研究，不改生产口径。

样本：chanlun_pool.jsonl 全部 CN 买点（1买/2买/3买），D=detect_date。
窗口收益：与 strong_ts_window_research / build_pick_verdicts 同口径——
D+2 首次可操作日开盘买入 × T+5/T+10 收盘卖出，ts_adj_factor 复权。

分层键（字段语义见 chanlun_composite.FACTOR_GETTERS）：
  · type    1买/2买/3买
  · trend   信号时个股趋势 up/down/range
  · reg     大盘 regime down/flat/up（None=不可判）
  · bei     盘整背驰（3类适用）；tbe 趋势背驰双中枢（1类适用）
  · vv      量能缩量
  · lag     检测滞后（dt→detect_date 自然日）：≤10 / 11-20 / >20 / None(scan)
  · source  backfill（回补检测）/ scan（当日扫描）
  · 组合    type×trend；type 内背驰×量能（1买: tbe×vv，3买: bei×vv）

用法：python chanlun_quality_layering.py
"""
import collections
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "portal")))

import strong_ts_window_research as R  # noqa: E402  复用湖加载/日历/收益

POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
BUY_TYPES = ("1买", "2买", "3买")
WINDOWS = (5, 10)


def load_events_full():
    """买点事件 + 全部特征字段。"""
    evts = []
    with open(POOL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("market") != "CN" or r.get("type") not in BUY_TYPES:
                continue
            code = str(r.get("code") or "").strip()
            dd = str(r.get("detect_date") or "")[:10]
            if not code or len(dd) != 10:
                continue
            lag = r.get("lag")
            evts.append({
                "code": code, "type": r.get("type"), "D": dd,
                "trend": str(r.get("trend")), "reg": str(r.get("reg")),
                "bei": r.get("bei"), "tbe": r.get("tbe"),
                "vv": r.get("vv"), "lag": lag,
                "lagb": ("scan(None)" if lag is None else
                         "≤10" if lag <= 10 else
                         "11-20" if lag <= 20 else ">20"),
                "source": str(r.get("source") or ""),
            })
    return evts


def lag_bucket(lag):
    return ("scan(None)" if lag is None else "≤10" if lag <= 10
            else "11-20" if lag <= 20 else ">20")


def main():
    evts = load_events_full()
    print("[1/3] 买点事件 %d" % len(evts), flush=True)
    cal = R.trade_calendar()
    stocks = R.load_table_grouped("ts_daily",
                                  ["ts_code", "trade_date", "open", "close"],
                                  "ts_code")
    adj_all = R.load_table_grouped("ts_adj_factor",
                                   ["ts_code", "trade_date", "adj_factor"],
                                   "ts_code")

    rows, n_skip = [], 0
    for e in evts:
        code = e["code"]
        stock = stocks.get(code)
        if stock is None:
            n_skip += 1
            continue
        adf = adj_all.get(code)
        adjmap = ({str(t): float(a) for t, a in zip(
            adf["trade_date"], adf["adj_factor"])} if adf is not None
            and len(adf) else None)
        D = R.d8(e["D"])
        exec_day = R.after_days(cal, D, 2)  # 生产口径：D+2 首次可操作
        if exec_day is None:
            continue
        wins = {}
        for w in WINDOWS:
            d1 = R.after_days(cal, exec_day, w)
            wins[w] = (R.fast_pct(stock, adjmap, exec_day, d1)
                       if d1 else None)
        rows.append(dict(e, wins=wins))
    print("[2/3] 收益计算完成 %d 行（湖缺票跳过 %d）" % (len(rows), n_skip),
          flush=True)

    def stat(rs, w):
        vals = [r["wins"][w] for r in rs]
        vals = [v for v in vals if isinstance(v, float) and np.isfinite(v)]
        if not vals:
            return None
        arr = np.array(vals)
        return (len(arr), round(float(arr.mean()), 2),
                round(float(np.median(arr)), 2),
                round(float((arr > 0).mean() * 100), 1))

    base = {w: stat(rows, w) for w in WINDOWS}

    def layer(keyfn, title):
        groups = collections.defaultdict(list)
        for r in rows:
            groups[str(keyfn(r))].append(r)
        print("\n--- %s ---" % title)
        print("%-18s %6s | %19s | %19s" %
              ("层", "n", "T+5 均值/中位/胜率%", "T+10 均值/中位/胜率%"))
        for k in sorted(groups, key=lambda x: -len(groups[x])):
            g = groups[k]
            line = "%-18s %6d" % (k, len(g))
            for w in WINDOWS:
                s = stat(g, w)
                if s is None:
                    line += " |            --"
                    continue
                dm = s[2] - base[w][2]
                line += " | %6.2f %6.2f %5.1f(%+5.1f)" % (
                    s[1], s[2], s[3], dm)
            print(line)

    print("[3/3] 分层汇总（基准全池：T+5 中位 %.2f / T+10 中位 %.2f；"
          "括号=中位差 vs 全池）" % (base[5][2], base[10][2]), flush=True)
    layer(lambda r: r["type"], "type 买点类别")
    layer(lambda r: r["trend"], "trend 信号时个股趋势")
    layer(lambda r: r["reg"], "reg 大盘 regime")
    layer(lambda r: r["bei"], "bei 盘整背驰(3类)")
    layer(lambda r: r["tbe"], "tbe 趋势背驰(1类)")
    layer(lambda r: r["vv"], "vv 量能缩量")
    layer(lambda r: r["lagb"], "lag 检测滞后桶")
    layer(lambda r: r["source"], "source 检出来源")
    layer(lambda r: "%s×%s" % (r["type"], r["trend"]), "type×trend")
    layer(lambda r: "1买 tbe=%s vv=%s" % (r["tbe"], r["vv"])
          if r["type"] == "1买" else "—", "1买内 tbe×vv（—=非1买）")
    layer(lambda r: "3买 bei=%s vv=%s" % (r["bei"], r["vv"])
          if r["type"] == "3买" else "—", "3买内 bei×vv（—=非3买）")
    layer(lambda r: "2买 lag=%s" % r["lagb"]
          if r["type"] == "2买" else "—", "2买内 lag 分桶（—=非2买）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
