# -*- coding: utf-8 -*-
"""六维席位画像后验（P1 收尾）：哪些维对次日收益有区分力。

口径（与 pick_layer_verdicts / strong_ts_window_research 对齐）：
  事件 = 席位×交易日×股票 的上榜记录（ts_top_inst，net_buy≠0）
  收益 = 当日 close → 次一交易日 close（ts_adj_factor 复权）
  分层 = 六维画像（six_dim_seats.json）按中位数二分（高/低组），
         事件级归属其席位的分组，两组收益差 + Welch t 值。

为什么用"当日→次日"而不是 basis+2（与 picks 框架不同）：
席位上榜是当日已发生的行为，可操作窗口是次日——这里度量的
是"席位行为的次日跟随价值"，不是"信号的可成交收益"。
两个口径都保留：T+1（跟随价值）/ T+5（持续力）。

审计（JUDGMENT_AUDIT.md 待入册）：
  assumptions: 席位画像（近30日）在事件当日已知——用全窗口画像分层
               事件存在轻微前视（画像用了事件之后的数据）；
  falsifier:  滚动窗口重算（只用事件前数据构画像）后分层差异消失；
  cost_if_wrong: 若前视成立，t 值高估维度区分力 → 线上跟随收益不及回测。
  （v0.1 接受该前视做初筛；入选维度再上滚动窗口复核——两段式省算力。）

用法：python six_dim_verdicts.py [--days 30]
输出：reports/six_dim_verdicts.json + 控制台表
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import six_dim_engine  # noqa: E402
import strong_ts_window_research as R  # noqa: E402
import tdx_read as tdxr  # noqa: E402

OUT = os.path.join(HERE, "reports", "six_dim_verdicts.json")
DIMS_BIN = {  # 参与二分检验的维度（odd_time 是类别型，另走分组均值）
    "d1_single_max_yi": "单笔大额峰",
    "d2_cum_max_yi": "累积大额峰",
    "d3_freq_max": "频繁交易峰",
    "d5_net_yi": "资金净和",
    "d6_peak_ratio": "节奏集中度",
}


def welch_t(a, b):
    if len(a) < 5 or len(b) < 5:
        return None
    a, b = np.asarray(a, float), np.asarray(b, float)
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    se = np.sqrt(va + vb)
    if se == 0:
        return None
    return float((a.mean() - b.mean()) / se)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="画像窗口（同 six_dim_engine）")
    a = ap.parse_args()

    # 画像（含席位→六维）
    seats, basis, _ = six_dim_engine.build_seat_profile(a.days, top=300)
    prof = {s["seat"]: s for s in seats}
    med = {}
    for k in DIMS_BIN:
        vals = [s[k] for s in seats]
        med[k] = float(np.median(vals))

    # 事件：窗口内全部上榜记录（net_buy≠0）
    start = (dt.date.today() - dt.timedelta(days=a.days * 3)).strftime("%Y%m%d")
    ti = tdxr.read_range("ts_top_inst", start=start,
                         columns=["ts_code", "trade_date", "exalter", "net_buy"])
    ti = ti.dropna(subset=["exalter"]).copy()
    ti["exalter"] = ti["exalter"].astype(str).str.strip()
    ti["d"] = ti["trade_date"].astype(str).str[:8]
    ti = ti[ti["net_buy"].fillna(0) != 0]
    ti = ti[ti["exalter"].isin(prof)]            # 只看画像内席位

    # 次日收益（复权）
    cal = R.trade_calendar()
    k_all = R.load_table_grouped(
        "ts_daily", ["ts_code", "trade_date", "open", "close"], "ts_code")
    adj_all = R.load_table_grouped(
        "ts_adj_factor", ["ts_code", "trade_date", "adj_factor"], "ts_code")

    events = []   # (ret1, ret5, seat, dim_bucket_tags)
    n_no_ret = 0
    for r in ti.itertuples(index=False):
        code, d0 = r.ts_code, r.d
        k = k_all.get(code)
        if k is None or k.empty:
            continue
        adf = adj_all.get(code)
        adjmap = ({str(t): float(x) for t, x in zip(adf["trade_date"], adf["adj_factor"])}
                  if adf is not None and len(adf) else None)
        d1 = R.after_days(cal, d0, 1)
        d5 = R.after_days(cal, d0, 5)
        if d1 is None:
            n_no_ret += 1
            continue
        ret1 = R.fast_pct(k, adjmap, d0, d1)
        ret5 = R.fast_pct(k, adjmap, d0, d5) if d5 else None
        if not isinstance(ret1, float) or not np.isfinite(ret1):
            n_no_ret += 1
            continue
        p = prof[r.exalter]
        tags = {}
        for kdim in DIMS_BIN:
            tags[kdim] = "高" if p[kdim] > med[kdim] else "低"
        tags["d4_reason_tag"] = p["d4_reason_tag"]
        tags["dir"] = "买" if float(r.net_buy) > 0 else "卖"
        events.append({"ret1": ret1, "ret5": ret5, "seat": r.exalter, "tags": tags})

    print("事件 %d 条（无次日价剔除 %d）画像席位 %d basis=%s"
          % (len(events), n_no_ret, len(prof), basis))

    base1 = np.array([e["ret1"] for e in events])
    print("基准：T+1 均值 %+.2f%% 中位 %+.2f%%"
          % (base1.mean(), np.median(base1)))

    rows = []
    print("\n%-14s %-6s %6s | %-22s | %-22s" %
          ("维度", "组", "n", "T+1 均值%%(差)", "T+5 均值%%(差)"))
    for kdim, cn in DIMS_BIN.items():
        grp = {"高": [], "低": []}
        grp5 = {"高": [], "低": []}
        for e in events:
            g = e["tags"][kdim]
            grp[g].append(e["ret1"])
            if isinstance(e["ret5"], float) and np.isfinite(e["ret5"]):
                grp5[g].append(e["ret5"])
        t1, t5 = welch_t(grp["高"], grp["低"]), welch_t(grp5["高"], grp5["低"])
        m = {g: (np.mean(v) if v else float("nan")) for g, v in grp.items()}
        m5 = {g: (np.mean(v) if v else float("nan")) for g, v in grp5.items()}
        for g in ("高", "低"):
            d1_ = m[g] - base1.mean()
            print("%-14s %-6s %6d | %+7.2f (%+5.2f)      | %+7.2f"
                  % (cn, g, len(grp[g]), m[g], d1_, m5[g]))
        rows.append({"dim": kdim, "cn": cn, "t_t1": t1, "t_t5": t5,
                     "n_hi": len(grp["高"]), "n_lo": len(grp["低"]),
                     "mean_t1_hi": round(float(m["高"]), 2),
                     "mean_t1_lo": round(float(m["低"]), 2),
                     "diff_bp": round(float((m["高"] - m["低"]) * 100), 1)})

    # 方向×净和 交叉（买侧高净 vs 卖侧高净 的跟随价值）
    print("\n方向 × 净和交叉（跟随价值核对）：")
    for dr in ("买", "卖"):
        for g in ("高", "低"):
            sel = [e["ret1"] for e in events
                   if e["tags"]["dir"] == dr and e["tags"]["d5_net_yi"] == g]
            if len(sel) >= 5:
                print("  %s侧·净和%s: n=%d T+1 均值 %+.2f%%"
                      % (dr, g, len(sel), np.mean(sel)))

    doc = {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "basis": basis, "n_events": len(events),
        "baseline_t1": round(float(base1.mean()), 2),
        "audit": "v0.1 存在前视（画像含事件后数据），t 值作初筛；"
                 "入选维度需滚动窗口复核",
        "dims": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("\n-> %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
