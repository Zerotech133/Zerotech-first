# -*- coding: utf-8 -*-
"""尾盘两段式历史回放验证（K-STOCK-025 验证轮）
模拟 14:40 尾盘扫描的完整逻辑（用历史数据回放）：
  T=2026-09-30：候选池=T-1(0929)单日十字星票 → 截至样本日(0930)复核 → 尾盘信号
               → 后验收益=节后首日(1008)收盘/0930收盘-1（可实现）
  T=2026-10-08：候选池=0930十字星票 → 截至1008复核 → 信号应含 600593/600887
               （今日 14:40 实时复核将用实时 bar 验证同批票）
只读特征湖，venv 运行。
"""

import glob
import json
import sys
import time

import pandas as pd

sys.path.insert(0, r"C:\项目\老田的AI助手\agent_platform\engine")
from quant_double_knife import quant_double_knife  # noqa: E402
import logging

DIR = r"C:\项目\老田的AI助手\quant_backtest\data\ashare"
T1, T2 = "20260929", "20260930"  # T2=回放样本日, T1=其前一交易日
T3 = "20261008"  # 后验日（也是第二回放样本日）
OUT = {}

t0 = time.time()
files = sorted(glob.glob(DIR + "\\*.parquet"))
pool_A, pool_B = [], []  # T1 十字星 → T2 候选；T2 十字星 → T3 候选
closes = {}  # code -> {date: close} 只存三日的收盘价
for i, fp in enumerate(files):
    code = fp.split("\\")[-1].replace(".parquet", "").replace("_", ".")
    try:
        df = pd.read_parquet(
            fp, columns=["trade_date", "open", "high", "low", "close", "amount"]
        )
    except Exception:
        continue
    if len(df) < 100:
        continue
    d = df[df["trade_date"].isin([T1, T2, T3])]
    if d["trade_date"].nunique() < 3:
        continue
    closes[code] = dict(zip(d["trade_date"], d["close"].astype(float)))
    # 单日十字星判定（与引擎 _cross_star 同口径，只需 OHLC）
    r = d.set_index("trade_date")
    for day, pool in ((T1, pool_A), (T2, pool_B)):
        if day not in r.index:
            continue
        o, c = float(r.loc[day, "open"]), float(r.loc[day, "close"])
        h, l = float(r.loc[day, "high"]), float(r.loc[day, "low"])
        body = abs(o - c) / (abs(h - l) + 1e-4)
        if body <= 0.30 and h > max(o, c) and l < min(o, c):
            pool.append(code)
    if (i + 1) % 1500 == 0:
        print("[pool] %d/%d %.0fs" % (i + 1, len(files), time.time() - t0), flush=True)

print(
    "候选池: T1(%s)十字星 %d 只 → T2 复核 | T2(%s)十字星 %d 只 → T3 复核"
    % (T1, len(pool_A), T2, len(pool_B)),
    flush=True,
)


def recheck(codes, until_day):
    """两段式第二段：候选票取截至 until_day 的湖数据跑 strict（模拟盘中
    实时 bar=当日完整 bar 的回放）"""
    sigs = []
    for code in codes:
        try:
            df = pd.read_parquet(
                DIR + "\\%s.parquet" % code.replace(".", "_"),
                columns=["trade_date", "open", "high", "low", "close", "amount"],
            )
        except Exception:
            continue
        df = df[df["trade_date"] <= until_day]
        if len(df) < 100:
            continue
        d = df.rename(columns={"trade_date": "date"}).set_index("date")
        try:
            out = quant_double_knife(d, profile="strict")
        except Exception:
            continue
        if bool(out["double_knife_signal"].iloc[-1]):
            sigs.append(code)
    return sigs


print(
    "\n[回放1] T=%s 尾盘信号（候选池 %d 只复核中...）" % (T2, len(pool_A)), flush=True
)
s1 = recheck(pool_A, T2)
print("  尾盘信号 %d 只: %s" % (len(s1), s1))
post = []
for code in s1:
    try:
        ret = (closes[code][T3] / closes[code][T2] - 1) * 100
        post.append((code, round(ret, 2)))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/qd_replay_verify.py", exc)
if post:
    avg = sum(r for _, r in post) / len(post)
    win = sum(1 for _, r in post if r > 0)
    print(
        "  后验（%s 尾盘买 → %s 收盘卖）: 平均 %+.2f%% | 胜率 %d/%d"
        % (T2, T3, avg, win, len(post))
    )
    for c, r in sorted(post, key=lambda x: -x[1]):
        print("    %s %+.2f%%" % (c, r))
OUT["replay_t2"] = {"signals": s1, "post": post}

print(
    "\n[回放2] T=%s 尾盘信号（候选池 %d 只复核中...）" % (T3, len(pool_B)), flush=True
)
s2 = recheck(pool_B, T3)
print("  尾盘信号 %d 只: %s" % (len(s2), s2))
print("  含已知 10-08 双刀票:", "600593.SH" in s2 and "600887.SH" in s2)
OUT["replay_t3"] = {"signals": s2}

OUT["elapsed"] = round(time.time() - t0, 1)
json.dump(
    OUT,
    open(r"C:\项目\老田的AI助手\portal\qd_replay_verify.json", "w", encoding="utf-8"),
    ensure_ascii=False,
    indent=1,
)
print("\nVERIFY DONE %.0fs → portal/qd_replay_verify.json" % OUT["elapsed"])
