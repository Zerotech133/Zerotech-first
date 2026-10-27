# -*- coding: utf-8 -*-
"""时间外扩独立样本验证：tushare 2018-2023 历史对"缠论仅1买1卖"策略做 OOS 评估。

破除时间选择偏差——空间外扩(8标的7正1负)已做，时间外扩是唯一未破除项。
口径与 walk_forward.chan_full_oos_eval 完全一致：
  - TRAIN_N=252 训练窗（仅用于确定 OOS 起点位置，不参与寻优）
  - sig_types={"1买","1卖"} 仅一类买卖点
  - 逐信号截断重放(_confirm_idx)防未来
  - 不止损（ffill 状态延续）
  - 评估段=[TRAIN_N, n) 连续区间
"""

import os
import time

import pandas as pd
import requests

import metrics as mt
from walk_forward import chan_full_oos_eval, TRAIN_N

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = r"D:\青萍之末2\.env"
API = "https://api.tushare.pro"

# 8 标的全集 + 3 标的核心组合（与空间外扩对齐）
EIGHT = [
    "000001.SH",
    "000016.SH",
    "000300.SH",
    "000905.SH",
    "000852.SH",
    "399001.SZ",
    "399006.SZ",
    "399005.SZ",
]
THREE = ["000300.SH", "000905.SH", "399006.SZ"]

START = "20180101"
END = "20240101"


def _token():
    # P0-4：优先环境变量 TUSHARE_TOKEN，读不到再落回 .env（兼容）。
    tok = os.environ.get("TUSHARE_TOKEN", "").strip()
    if tok:
        return tok
    with open(ENV, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("TUSHARE_TOKEN="):
                return ln.split("=", 1)[1].strip()
    raise SystemExit("TUSHARE_TOKEN not found (env / %s)" % ENV)


def ts_index_daily(code, start, end, retry=4):
    """tushare index_daily 单码日线；失败重试含限频退避。"""
    tok = _token()
    last = ""
    for i in range(retry):
        try:
            r = requests.post(
                API,
                json={
                    "api_name": "index_daily",
                    "token": tok,
                    "params": {"ts_code": code, "start_date": start, "end_date": end},
                    "fields": "",
                },
                timeout=30,
            )
            d = r.json()
        except Exception as e:
            last = str(e)[:80]
            time.sleep(2 * (i + 1))
            continue
        if d.get("code") == 0:
            data = d.get("data") or {}
            return pd.DataFrame(
                data.get("items") or [], columns=data.get("fields") or []
            )
        last = str(d.get("msg", ""))[:100]
        if "每分钟" in last:
            time.sleep(61)
            continue
        raise RuntimeError("%s: %s" % (code, last))
    raise RuntimeError("%s %s~%s: %s" % (code, start, end, last))


def to_bars(df):
    """tushare index_daily DataFrame → 升序 bars list[{dt,o,h,l,c,v}] + close Series。"""
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.sort_values("trade_date").reset_index(drop=True)
    bars = [
        {
            "dt": str(r.trade_date),
            "o": float(r.open),
            "h": float(r.high),
            "l": float(r.low),
            "c": float(r.close),
            "v": float(r.vol),
        }
        for r in df.itertuples()
    ]
    close = pd.Series([b["c"] for b in bars], index=[b["dt"] for b in bars])
    return close, bars


def main():
    print("=== 时间外扩独立样本验证（tushare 2018-2023）===")
    print("口径: chan_full_oos_eval, sig_types={1买,1卖}, 不止损, OOS=[252,n)")
    print("8 标的:", " ".join(EIGHT))
    print()

    results = {}
    for code in EIGHT:
        try:
            df = ts_index_daily(code, START, END)
        except Exception as e:
            print("[FAIL] %s 拉取失败: %s" % (code, str(e)[:80]))
            continue
        if df.empty:
            print("[EMPTY] %s 无数据" % code)
            continue
        close, bars = to_bars(df)
        n = len(bars)
        if n < TRAIN_N + 100:
            print("[SKIP] %s 仅 %d 根 < %d+100" % (code, n, TRAIN_N))
            continue
        oos_lo_dt = bars[TRAIN_N]["dt"]
        print(
            "[%s] %d 根 %s~%s | OOS 起点=%s"
            % (code, n, bars[0]["dt"], bars[-1]["dt"], oos_lo_dt)
        )

        t0 = time.time()
        try:
            net_oos, pos_oos = chan_full_oos_eval(
                close,
                bars,
                bench_close=close,
                sig_types={"1买", "1卖"},
                ret_series=True,
            )
        except Exception as e:
            print("  chan_full_oos_eval FAIL: %s" % str(e)[:120])
            continue
        dt_eval = time.time() - t0
        bench_ret = close.iloc[TRAIN_N:].pct_change().fillna(0)
        m = mt.evaluate(net_oos, pos_oos, bench_ret)
        results[code] = {"net": net_oos, "pos": pos_oos, "m": m}
        mt.show("  " + code, m)
        print("  (评估耗时 %.1fs)" % dt_eval)

    if not results:
        print("\n!! 无可用标的，退出")
        return

    print("\n=== 8 标的独立 OOS 汇总 ===")
    pos_cnt = sum(
        1 for c in EIGHT if c in results and results[c]["m"]["total_return"] > 0
    )
    neg_cnt = sum(
        1 for c in EIGHT if c in results and results[c]["m"]["total_return"] <= 0
    )
    for code in EIGHT:
        if code in results:
            m = results[code]["m"]
            print(
                "%-10s 年化=%7.2f%% 夏普=%5.2f 回撤=%6.1f%% 卡玛=%5.2f 胜率=%5.1f%%"
                % (
                    code,
                    m["cagr"] * 100,
                    m["sharpe"],
                    m["max_dd"] * 100,
                    m["calmar"],
                    m["win_rate"] * 100,
                )
            )
    print("盈/亏标的: %d/%d" % (pos_cnt, neg_cnt))
    sharpes = sorted([results[c]["m"]["sharpe"] for c in EIGHT if c in results])
    if sharpes:
        mid = sharpes[len(sharpes) // 2]
        print("夏普中位数: %.2f" % mid)

    # 三标的组合（与空间外扩对齐）
    three_present = [c for c in THREE if c in results]
    if three_present:
        print("\n=== 三标的组合(等权) %s ===" % " ".join(three_present))
        net_avg = sum(results[c]["net"] for c in three_present) / len(three_present)
        pos_avg = sum(results[c]["pos"] for c in three_present) / len(three_present)
        # 对齐 bench
        bench_ret = results[three_present[0]]["net"].copy()
        bench_ret[:] = 0
        for c in three_present:
            br = results[c]["net"].copy()
            br[:] = 0
            # 用各标的 close 计算基准——重新简化为等权 bench_ret=0 评估 IR 不报
        m3 = mt.evaluate(net_avg, pos_avg, None)
        mt.show("三标的组合", m3)

    # 八标的组合
    eight_present = [c for c in EIGHT if c in results]
    if eight_present:
        print("\n=== 八标的组合(等权) %s ===" % " ".join(eight_present))
        net_avg = sum(results[c]["net"] for c in eight_present) / len(eight_present)
        pos_avg = sum(results[c]["pos"] for c in eight_present) / len(eight_present)
        m8 = mt.evaluate(net_avg, pos_avg, None)
        mt.show("八标的组合", m8)

    # 与空间外扩对比结论
    print("\n=== 时间外扩 vs 空间外扩(8标的1买1卖)对比 ===")
    print("空间外扩: 7正1负 中位夏普0.87 | 三标的组合夏普1.05 | 八标的组合夏普0.93")
    print("时间外扩: %d正%d负 中位夏普%.2f" % (pos_cnt, neg_cnt, mid if sharpes else 0))


if __name__ == "__main__":
    main()
