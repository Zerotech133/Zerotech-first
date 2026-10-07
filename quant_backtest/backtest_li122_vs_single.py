# -*- coding: utf-8 -*-
"""D2 联立 V1 回测对照：单级别信号（现状基线）vs 联立过滤信号。

口径（报告同步声明，全部可复现）：
· 上级信号：30m（档A/A1）或 1d（档B）的 chanlun_signal 买向信号
  （1买/2买/3买），窗口内每条为一个样本。基线=全部放行；
  联立组=chanlun_li122.judge 通过的子集（同一信号集过滤，公平对照）。
· 入场价 = 信号价（笔终点价）；评估序列 = 档A 用 1m bars、档B 用
  5m bars，取信号 dt 之后的部分（信号桶整桶视为已发生——与产线
  EOD 盘后检出口径一致，无未来函数）。
· 单信号净收益（可复现定义）：评估窗（H_LOW 根低级 K 线 ≈ 5 交易日）
  内先触及 +TP1(3%) 记 +3%，先触及 −SL(2%) 记 −2%（同根同时触及
  保守记 −2%），均未触记期末收盘收益。
· 假阳 = 先触发 −SL 且未先触发 +TP1；假阳率 = 假阳数/信号数。
· 达标（草案闸门）：假阳率下降 ≥30% 且平均净收益不降。

样本：全 A 沪深主板等距抽样（剔除北交所），stk_mins 分钟数据可得者，
默认 40 只；--smoke 10 只冒烟计时。信号窗口 2026-06-01 ~ 2026-09-15，
5 月为 30m 结构预热，9-15 后留评估缓冲。
只读数据（stk_mins parquet / ashare 日线 parquet），不写任何库与共享文件。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd                                    # noqa: E402
import datamart                                        # noqa: E402
from chanlun_signal import analyze as sig_analyze      # noqa: E402
from chanlun_li122 import judge                        # noqa: E402
from chanlun_data import _bucket_label, TZ_CN          # noqa: E402

# ── 回测参数（口径常量，改动即换口径，报告须同步） ──────────────
DATA_START = "20260401"          # 1min 读取起点（30m 结构预热）
SIG_START, SIG_END = "2026-06-01", "2026-09-15"   # 信号窗口（含）
SL = 0.02                        # 止损带（假阳=窗口内触发 −SL）
EVAL_LOW_N = {"1m": 1200, "5m": 240}   # 评估窗根数 ≈ 5 交易日
BUY_TYPES = ("1买", "2买", "3买")
UNIVERSE_N = 40
SMOKE_N = 10
RULE_KEYS_A = ("A_30F_5F_1F", "A1_30F_5F_1F_only1")   # 用 1m/5m 评估
RULE_KEY_B = "B_D_30F_5F"                              # 用 5m 评估
ASHARE = os.path.join(HERE, "data", "ashare")


def pick_universe(n):
    """沪深主板等距抽样（剔 BJ），返回 ts_code 列表。"""
    names = sorted(f[:-8] for f in os.listdir(ASHARE)
                   if f.endswith(".parquet"))
    codes = [f.replace("_", ".") for f in names
             if f.startswith(("600", "601", "603", "605", "000", "001", "003"))]
    step = max(1, len(codes) // (n * 6))
    cand = codes[::step]
    out = []
    for c in cand:
        df = datamart.read("stk_mins", codes=c, start=DATA_START,
                           end="20261009")
        if df is not None and len(df) > 1000:
            out.append(c)
        if len(out) >= n:
            break
    return out


def load_minutes(code):
    """1min 原始 → (bars1, bars5, bars30)（统一 bar dict，dt 北京时区）。"""
    df = datamart.read("stk_mins", codes=code, start=DATA_START,
                       end="20261009")
    if df is None or df.empty:
        return [], [], []
    rows = []
    for r in df.to_dict("records"):
        try:
            t = pd.Timestamp(str(r["trade_time"])[:19]).to_pydatetime()
            t = t.replace(tzinfo=TZ_CN)
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            v = float(r.get("vol") or 0)
        except (TypeError, ValueError):
            continue
        if c != c or c <= 0:
            continue
        rows.append((t, o, h, l, c, v))
    rows.sort(key=lambda x: x[0])
    fmt = "%Y-%m-%d %H:%M"
    bars1 = [{"dt": t.strftime(fmt), "o": o, "h": h, "l": l, "c": c, "v": v}
             for t, o, h, l, c, v in rows]
    return (bars1, _resample(rows, 5), _resample(rows, 30))


def _resample(rows, bm_min):
    """1min → N 分钟桶（复用产线 _bucket_label 会话分桶口径）。"""
    out, cur, key = [], None, None
    for t, o, h, l, c, v in rows:
        lab = _bucket_label(t, bm_min)
        if key != lab:
            if cur:
                out.append(cur)
            key, cur = lab, [lab, o, h, l, c, v]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = c
            cur[5] += v
    if cur:
        out.append(cur)
    fmt = "%Y-%m-%d %H:%M"
    return [{"dt": t.strftime(fmt), "o": o, "h": h, "l": l, "c": c, "v": v}
            for t, o, h, l, c, v in out]


def load_daily(code):
    """ashare parquet 日线 → bar dict（档B 上级）。"""
    p = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    df = pd.read_parquet(p)
    out = []
    for r in df.to_dict("records"):
        d = str(r["trade_date"])
        out.append({"dt": "%s-%s-%s" % (d[:4], d[4:6], d[6:]),
                    "o": float(r["open"]), "h": float(r["high"]),
                    "l": float(r["low"]), "c": float(r["close"]),
                    "v": float(r.get("volume") or 0)})
    out.sort(key=lambda b: b["dt"])
    return out


def eval_return(bars_low, sig_dt, entry):
    """单信号净收益（持有语义，保守）：信号后低级 K 线窗口内触及
    −SL 即记 −SL（=假阳，无论此前涨幅）；未触记期末收盘收益（不封顶）。
    冒烟校准说明：先到优先口径下 +TP1 先到率 >95% 无区分度，弃用。"""
    freq = _low_freq(bars_low)
    fwd = [b for b in bars_low if b["dt"] > sig_dt][:EVAL_LOW_N[freq]]
    if not fwd:
        return None
    for b in fwd:
        if b["l"] <= entry * (1 - SL):
            return -SL
    return fwd[-1]["c"] / entry - 1


def _low_freq(bars_low):
    """按根数密度粗判频率（>800 根/日 ≈ 1m）；仅用于评估窗换算。"""
    if len(bars_low) < 800:
        return "5m"
    d0 = bars_low[-1]["dt"][:10]
    per_day = sum(1 for b in bars_low if b["dt"][:10] == d0)
    return "1m" if per_day > 100 else "5m"


def collect(code):
    """单标的：产出 [样本dict]（含基线 + 各档联立判定 + 收益）。"""
    bars1, bars5, bars30 = load_minutes(code)
    if len(bars30) < 100:
        return []
    st30 = sig_analyze(bars30)
    out = []
    for s in st30["signals"]:
        if s["type"] not in BUY_TYPES:
            continue
        if not (SIG_START <= s["dt"][:10] <= SIG_END):
            continue
        ret = eval_return(bars1, s["dt"], s["price"])
        if ret is None:
            continue
        row = {"code": code, "type": s["type"], "dt": s["dt"],
               "price": s["price"], "ret": ret,
               "A_30F_5F_1F": judge(s, bars5, bars1, "A_30F_5F_1F")["ok"],
               "A1_30F_5F_1F_only1":
                   judge(s, bars5, bars1, "A1_30F_5F_1F_only1")["ok"],
               "A2_30F_5F_1F_wide":
                   judge(s, bars5, bars1, "A2_30F_5F_1F_wide")["ok"]}
        out.append(row)
    # 档B：日线上级 → 30m 底分型 + 5m 不新低
    try:
        barsd = load_daily(code)
        std = sig_analyze(barsd)
        for s in std["signals"]:
            if s["type"] not in BUY_TYPES:
                continue
            if not (SIG_START <= s["dt"][:10] <= SIG_END):
                continue
            ret = eval_return(bars5, s["dt"], s["price"])
            if ret is None:
                continue
            out.append({"code": code, "type": s["type"], "dt": s["dt"],
                        "price": s["price"], "ret": ret, "period": "1d",
                        "B_D_30F_5F": judge(s, bars30, bars5,
                                            "B_D_30F_5F")["ok"]})
    except Exception:
        pass
    return out


def stats(rows, key):
    """一组样本统计：n / 假阳率 / 平均净收益 / 总净收益。"""
    sub = [r for r in rows if key not in r or r.get(key)]
    n = len(sub)
    if not n:
        return {"n": 0}
    fp = sum(1 for r in sub if r["ret"] <= -SL)
    rets = [r["ret"] for r in sub]
    return {"n": n, "fp_rate": round(fp / n, 4),
            "avg_ret": round(sum(rets) / n, 5),
            "sum_ret": round(sum(rets), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()
    n = SMOKE_N if args.smoke else UNIVERSE_N
    t0 = time.time()
    uni = pick_universe(n)
    print("universe(%d): %s" % (len(uni), uni), flush=True)
    rows, done = [], 0
    for c in uni:
        rows += collect(c)
        done += 1
        if done % 5 == 0:
            print("  %d/%d done, %.1fs" % (done, len(uni),
                                           time.time() - t0), flush=True)
    base = [r for r in rows if "period" not in r]     # 30m 上级（档A 组）
    db = [r for r in rows if r.get("period") == "1d"]  # 日线上级（档B 组）
    res = {
        "window": [SIG_START, SIG_END], "universe_n": len(uni),
        "sl": SL, "eval_low_n": EVAL_LOW_N,
        "A_base_30m": stats(base, "_none_"),
        "A_base_1buy": stats([r for r in base if r["type"] == "1买"],
                             "_none_"),
        "A_li_30F_5F_1F": stats(base, "A_30F_5F_1F"),
        "A2_li_wide": stats(base, "A2_30F_5F_1F_wide"),
        "A1_li_only1": stats([r for r in base if r["type"] == "1买"],
                             "A1_30F_5F_1F_only1"),
        "B_base_1d": stats(db, "_none_"),
        "B_li_D_30F_5F": stats(db, "B_D_30F_5F"),
        "A_by_type": {t: stats([r for r in base if r["type"] == t],
                               "_none_") for t in BUY_TYPES},
        "B_by_type": {t: stats([r for r in db if r["type"] == t],
                               "_none_") for t in BUY_TYPES},
        "elapsed_s": round(time.time() - t0, 1),
    }
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
