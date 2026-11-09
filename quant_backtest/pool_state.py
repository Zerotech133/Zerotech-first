# -*- coding: utf-8 -*-
"""股票池状态机（Pool State Machine）：每日/盘中判池内个股所处阶段。

背景（2026-11-09 老田纠正口径）：
  此前的选股是「每天从全 A 重扫信号 → 出当日清单」，标的用完即弃，没有
  池子这个持久对象。实际业务是：池子常驻，每天（含盘中）看池内谁临近
  爆发、谁在爆发、谁已衰竭。本文件补这一层。

四个阶段（全部只用 <= asof 当日 的日线，PIT 安全）：

  STAGE_B1 蓄势（临近爆发）——六条全中
    1 close > ma60                     趋势在位
    2 dist_hi120 >= -10%               高位整理（离 120 日高点不远）
    3 amp10/amp60 <= 0.80              波动收缩
    4 vol5/vol50 <= 0.85               缩量
    5 (max(ma5,ma10,ma20)-min)/close <= 5%   均线粘合
    6 amt20 >= --min-amt（亿）         容量门槛（U0：低成交额收益是会计假象）

  STAGE_B2 突破（爆发启动）——当日
    1 close >= 前 120 日最高价
    2 当日量 >= 1.5 × 前 20 日均量
    3 当日涨幅 >= 3%

  STAGE_B3 加速（主升中）
    1 ret20 >= 25%
    2 close >= ma20 × 1.08
    3 非 B2

  STAGE_C1 衰竭（见顶信号）
    a 收盘跌破 ma10 且 ma10 下拐，且 ret20 > 15%
    b ret20 >= 50% 且当日冲高回落 (high-close)/close >= 4%
    c 近 3 日收盘连续新高但量能逐日递减（量价背离）

  优先级：C1 > B2 > B3 > B1 > S0(其他)

用法:
  python pool_state.py --scan                 # 全市场扫一遍，分档+清单
  python pool_state.py --scan --top 50        # 只看蓄势前 50
  python pool_state.py --scan --stage C1      # 只看衰竭
  python pool_state.py --scan --min-amt 1.0   # 抬高容量门槛
  python pool_state.py --scan --save          # 落 reports/pool_state_<asof>.json

口径说明:
  · amount 单位=千元，amt20 换算为亿元（/1e5）
  · 数据源 data/ashare/*.parquet（tushare 日线，前复权/不复权以文件为准）
  · 不足 MIN_BARS 根的直接跳过（次新不可判）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASHARE = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")

MIN_BARS = 250
MIN_PRICE = 2.0         # 低价股剔除（面值退市风险，且 2026-11-09 首跑混进 1.26 元山钢）
ST_MARKS = ("ST", "退")
COLS = ["trade_date", "close", "high", "low", "volume", "amount", "pct_chg"]

# ── 阈值（模块常量，便于调参；改动须记入报告） ──────────────
B1_DIST_HI = -0.15      # 距 120 日高点（硬条件：临近爆发必须靠近前高）
B1_CONTRACT = 0.90      # 波动收缩比（0.80 时全市场仅 3.8% 命中，池子立不起来）
B1_VOLR = 0.90          # 缩量比
B1_SPREAD = 0.05        # 均线粘合度
B1_MIN_HITS = 4         # 六条中命中 >= 4 条即入蓄势池（6/6 全中在 A 股近乎不存在）
B1_MAX_DROP = -0.10     # 近 20 日跌幅超过 10% 视为正在下跌，不许入蓄势池
B2_BREAK_VOL = 1.5      # 突破放量倍数
B2_PCT = 3.0            # 突破当日涨幅 %
B3_RET20 = 0.25         # 加速段 20 日涨幅
B3_MA20_K = 1.08        # 加速段偏离 ma20
B3_DIST_HI = -0.15      # 加速段必须在高位（防深跌反弹冒充主升）
C1_PULLBACK = 0.04      # 冲高回落幅度
C1_DIST_HI = -0.15      # 衰竭必须在高位（下跌中继不算衰竭）


def _ma(a, k):
    return float(a[-k:].mean())


def _safe(x):
    """JSON 友好：NaN/inf → None。"""
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if not np.isfinite(v):
        return None
    return round(v, 6)


def analyse(df):
    """单只票 → 状态字典；不可判返回 None。"""
    df = df.sort_values("trade_date")
    if len(df) < MIN_BARS:
        return None

    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    amt = df["amount"].to_numpy(float)          # 千元
    pct = df["pct_chg"].to_numpy(float)

    last = c[-1]
    if last <= 0:
        return None

    ma5, ma10, ma20, ma60 = (_ma(c, 5), _ma(c, 10), _ma(c, 20), _ma(c, 60))

    # 前 120 日（不含当日）最高价：用于「距高点」与「突破」同一口径
    hi120 = float(h[-121:-1].max())
    dist_hi = last / hi120 - 1.0 if hi120 > 0 else -1.0

    ret20 = last / c[-21] - 1.0
    ret60 = last / c[-61] - 1.0

    # 波动收缩
    rng = (h - low) / np.where(c > 0, c, 1.0)
    amp10 = float(rng[-10:].mean())
    amp60 = float(rng[-60:].mean())
    contract = amp10 / amp60 if amp60 > 1e-9 else 1.0

    # 缩量
    vol5 = float(v[-5:].mean())
    vol50 = float(v[-50:].mean())
    volr = vol5 / vol50 if vol50 > 1e-9 else 1.0

    # 均线粘合
    mset = np.array([ma5, ma10, ma20])
    spread = float((mset.max() - mset.min()) / last)

    amt20 = float(amt[-20:].mean()) / 1e5        # 亿元

    # ── 阶段判定 ─────────────────────────────────────────
    hits_b1 = {
        "above_ma60": last > ma60,
        "near_high": dist_hi >= B1_DIST_HI,
        "contracted": contract <= B1_CONTRACT,
        "shrunk": volr <= B1_VOLR,
        "ma_glued": spread <= B1_SPREAD,
    }

    ma10_prev = _ma(c[:-1], 10)
    # 硬门槛：正在下跌的票不可能是蓄势（首跑把 ret20 -20.7%、dist_hi -28.8%
    # 的崩塌票判成 B1，只因它还站在 ma60 上方）
    not_falling = (ret20 >= B1_MAX_DROP) and (last > ma20 * 0.97)
    b2 = (last >= hi120 * 0.999
          and v[-1] >= B2_BREAK_VOL * float(v[-21:-1].mean())
          and pct[-1] >= B2_PCT)
    # 主升必须是「在高位的主升」——深跌反弹不算（2026-11-09 首跑发现
    # ret20 +44% 但距 120 日高点 -37% 的票被误判为 B3）
    b3 = (ret20 >= B3_RET20 and last >= ma20 * B3_MA20_K
          and dist_hi >= B3_DIST_HI and not b2)

    c1_reasons = []
    if dist_hi < C1_DIST_HI:
        pass  # 已远离高点，属下跌中继而非见顶衰竭，不判 C1
    elif last < ma10 and ma10 < ma10_prev and ret20 > 0.15:
        c1_reasons.append("破ma10拐头")
    if dist_hi >= C1_DIST_HI and ret20 >= 0.50 and (h[-1] - last) / last >= C1_PULLBACK:
        c1_reasons.append("高位冲高回落")
    if len(c) >= 3:
        new_high_3 = c[-1] > c[-2] > c[-3] and c[-1] >= float(c[-60:].max())
        vol_down_3 = v[-1] < v[-2] < v[-3]
        if new_high_3 and vol_down_3 and ret20 > 0.20:
            c1_reasons.append("量价背离3日")

    if c1_reasons:
        stage = "C1"
    elif b2:
        stage = "B2"
    elif b3:
        stage = "B3"
    elif hits_b1["near_high"] and sum(hits_b1.values()) >= B1_MIN_HITS and not_falling:
        stage = "B1"
    else:
        stage = "S0"

    # 蓄势强度分（仅 B1 有意义）：收缩+缩量+粘合 越大越好，距高点越近越好
    score = 0.0
    if stage == "B1":
        score = ((B1_CONTRACT - contract) * 100
                 + (B1_VOLR - volr) * 100
                 + (B1_SPREAD - spread) * 400
                 + (dist_hi - B1_DIST_HI) * 200)

    return {
        "stage": stage,
        "close": _safe(last),
        "ma5": _safe(ma5),
        "ma10": _safe(ma10),
        "ma20": _safe(ma20),
        "ma60": _safe(ma60),
        "hi120": _safe(hi120),
        "amp10": _safe(amp10),
        "amp60": _safe(amp60),
        "vol5": _safe(vol5),
        "vol50": _safe(vol50),
        "dist_hi120": _safe(dist_hi),
        "contract": _safe(contract),
        "volr": _safe(volr),
        "spread": _safe(spread),
        "amt20_yi": _safe(amt20),
        "ret20": _safe(ret20),
        "ret60": _safe(ret60),
        "pct": _safe(pct[-1]),
        "score": _safe(score),
        "n_hits": int(sum(hits_b1.values())),
        "_hb": hits_b1,
        "hits": "".join(k[0].upper() for k, ok in hits_b1.items() if ok),
        "c1": ",".join(c1_reasons),
        "asof": int(df["trade_date"].iloc[-1]),
    }


def iter_files():
    for fn in os.listdir(ASHARE):
        if not fn.endswith(".parquet"):
            continue
        yield fn


def load_names():
    """code2name 映射（data/stock_names.json，fetch_stock_names.py 生成）。"""
    p = os.path.join(HERE, "data", "stock_names.json")
    try:
        with open(p, encoding="utf-8") as f:
            j = json.load(f)
        return {str(k).upper(): str(v) for k, v in (j.get("code2name") or {}).items()}
    except Exception:
        return {}


def load_mv():
    """最新交易日流通市值 {code: 亿元}（ts_daily_basic，circ_mv 单位万元）。"""
    import glob

    try:
        fs = sorted(glob.glob(os.path.join(HERE, "data", "tdx", "ts_daily_basic", "*.parquet")))
        if not fs:
            return {}
        dfs = [pd.read_parquet(f, columns=["ts_code", "trade_date", "circ_mv"]) for f in fs]
        df = pd.concat(dfs, ignore_index=True)
        d = df[df["trade_date"] == df["trade_date"].max()]
        return {str(c).upper(): float(v) / 1e4
                for c, v in zip(d["ts_code"], d["circ_mv"])
                if pd.notna(v)}
    except Exception:
        return {}


def mv_bucket(v):
    if v is None:
        return "未知"
    if v < 20:
        return "微盘<20"
    if v < 50:
        return "小盘20-50"
    if v < 200:
        return "中盘50-200"
    if v < 1000:
        return "大盘200-1k"
    return "超大盘>1k"


def _pad(s, w):
    """按显示宽度左对齐（中文占 2 列）。"""
    s = "" if s is None else str(s)
    n = sum(2 if ord(ch) > 127 else 1 for ch in s)
    return s + " " * max(0, w - n)


def scan(min_amt=0.5, stage_filter=None, top=30, verbose=True):
    t0 = time.time()
    rows, skipped = [], 0
    n_st, n_low, n_amt = 0, 0, 0
    names = load_names()
    mvs = load_mv()
    files = list(iter_files())
    for i, fn in enumerate(files, 1):
        path = os.path.join(ASHARE, fn)
        code = fn[:-8].replace("_", ".")
        nm = names.get(code, "")
        if any(k in nm for k in ST_MARKS):
            n_st += 1
            skipped += 1
            continue
        try:
            df = pd.read_parquet(path, columns=COLS)
        except Exception:
            skipped += 1
            continue
        r = analyse(df)
        if r is None:
            skipped += 1
            continue
        if (r["close"] or 0) < MIN_PRICE:
            n_low += 1
            skipped += 1
            continue
        if (r["amt20_yi"] or 0) < min_amt:
            n_amt += 1
            skipped += 1
            continue
        r["code"] = code
        r["name"] = nm
        r["mv_yi"] = _safe(mvs.get(code))
        r["bucket"] = mv_bucket(r["mv_yi"])
        rows.append(r)
        if verbose and i % 1000 == 0:
            print(f"  ...{i}/{len(files)}", file=sys.stderr)

    buckets = {}
    for r in rows:
        buckets.setdefault(r["stage"], []).append(r)

    elapsed = time.time() - t0
    print(f"[pool_state] 扫描 {len(files)} 文件 / 可判 {len(rows)} / 跳过 {skipped} "
          f"(ST {n_st} / 低价 {n_low} / 额<{min_amt}亿 {n_amt} / 次新或坏文件 {skipped - n_st - n_low - n_amt}) "
          f"/ {elapsed:.1f}s")
    print(f"[pool_state] 门槛 amt20 >= {min_amt} 亿")
    for st in ("B1", "B2", "B3", "C1", "S0"):
        print(f"  {st}: {len(buckets.get(st, []))}")

    dist_n = {}
    for r in rows:
        dist_n[r["n_hits"]] = dist_n.get(r["n_hits"], 0) + 1
    print("[pool_state] 蓄势六条命中分布: "
          + "  ".join(f"{k}/6:{dist_n.get(k, 0)}" for k in (6, 5, 4, 3, 2, 1, 0)))
    for st in ("B1", "B2", "B3", "C1"):
        sub = buckets.get(st, [])
        if not sub:
            continue
        b = {}
        for r in sub:
            b[r["bucket"]] = b.get(r["bucket"], 0) + 1
        print(f"[pool_state] {st} 市值分档: " + "  ".join(
            f"{k}={b.get(k, 0)}" for k in ("微盘<20", "小盘20-50", "中盘50-200", "大盘200-1k", "超大盘>1k", "未知")))
    names = ["above_ma60", "near_high", "contracted", "shrunk", "ma_glued"]
    print("[pool_state] 单条命中率: " + "  ".join(
        f"{k}={sum(1 for r in rows if (r.get('_hb') or {}).get(k))}" for k in names))

    stages = [stage_filter] if stage_filter else ["B1", "B2", "B3", "C1"]
    out = {}
    for st in stages:
        lst = buckets.get(st, [])
        if st == "B1":
            lst.sort(key=lambda r: (-(r["n_hits"] or 0), -(r["score"] or 0)))
        else:
            lst.sort(key=lambda r: -(r["score"] or 0))
        out[st] = lst[:top]
        if not lst:
            continue
        print(f"\n===== {st} 前 {min(top, len(lst))} 只 =====")
        print(f"{_pad('code', 11)}{_pad('名称', 12)}{'close':>8}{'市值亿':>8}"
              f"{'dist_hi':>9}{'contract':>10}{'volr':>7}{'spread':>8}"
              f"{'amt20':>8}{'ret20':>8}{'n':>4}{'score':>8}  hits")
        for r in out[st]:
            print(f"{_pad(r['code'], 11)}{_pad(r.get('name', ''), 12)}{r['close']:>8.2f}"
                  f"{(r['mv_yi'] or 0):>8.1f}"
                  f"{(r['dist_hi120'] or 0)*100:>8.1f}%"
                  f"{r['contract']:>10.2f}{r['volr']:>7.2f}"
                  f"{(r['spread'] or 0)*100:>7.1f}%{r['amt20_yi']:>8.2f}"
                  f"{(r['ret20'] or 0)*100:>7.1f}%{r['n_hits']:>4}"
                  f"{r['score']:>8.2f}  {r['hits']}")

    return out, buckets, {"files": len(files), "judged": len(rows),
                          "skipped": skipped, "elapsed": round(elapsed, 1),
                          "min_amt": min_amt,
                          "ma60_on": sum(1 for r in rows if (r.get("_hb") or {}).get("above_ma60")),
                          "data_asof": str(max((r["asof"] for r in rows), default=""))}


def _fetch_quotes(codes):
    """新浪实时价（与 pool_watch / watch_positions 同一接口）。"""
    root = os.path.dirname(HERE)
    p = os.path.join(root, "news_collector")
    if p not in sys.path:
        sys.path.insert(0, p)
    from quote_snapshot import fetch_quotes

    return fetch_quotes(codes)


def _from_sina(c):
    c = str(c).lower()
    for pre, suf in (("sh", ".SH"), ("sz", ".SZ"), ("bj", ".BJ")):
        if c.startswith(pre):
            return c[2:] + suf
    return str(c).upper()


def intraday(top=None):
    """盘中巡检：读最近一份池文件，只比对关键位，不做重算。

    B1 池：现价 >= hi120          → 突破前高（蓄势转突破）
           现价 <= ma20 * 0.97    → 跌破，出池候选
    B2 池：现价 <= ma20          → 突破失败，退回观察
    B3 池：现价 <= ma10          → 加速段破位（衰竭前兆）
    """
    import glob

    files = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
    if not files:
        print("[intraday] 无池文件，先跑 --scan --save")
        return 1
    src = files[-1]
    with open(src, encoding="utf-8") as f:
        j = json.load(f)

    rows = []
    for st in ("B1", "B2", "B3"):
        for r in j["buckets"].get(st, []):
            rows.append((st, r))
    if not rows:
        print("[intraday] 池为空")
        return 1

    codes = [r["code"] for _, r in rows]
    try:
        qs = _fetch_quotes(codes)
    except Exception as e:
        print(f"[intraday] 取价失败: {e}")
        return 2
    idx = {_from_sina(q.get("code", "")): q for q in qs}

    print(f"[intraday] 池文件 {os.path.basename(src)} (数据基准 {j.get('data_asof')}) "
          f"/ 监控 {len(codes)} 只 / 取到报价 {len(idx)} 只\n")
    print(f"{_pad('code', 11)}{_pad('名称', 12)}{'原态':>5}{'现价':>9}{'涨幅':>8}"
          f"{'距前高':>9}{'vsMA10':>9}{'vsMA20':>9}   提醒")

    alerts, shown = [], 0
    for st, r in rows:
        q = idx.get(r["code"])
        if not q or not q.get("price"):
            continue
        px = float(q["price"])
        pct = q.get("pct")
        hi = r.get("hi120") or 0
        ma10 = r.get("ma10") or 0
        ma20 = r.get("ma20") or 0
        d_hi = px / hi - 1 if hi else 0
        d_10 = px / ma10 - 1 if ma10 else 0
        d_20 = px / ma20 - 1 if ma20 else 0

        note = ""
        if st == "B1" and hi and px >= hi:
            note = "突破前高→B2"
        elif st == "B1" and ma20 and px <= ma20 * 0.97:
            note = "跌破MA20→出池候选"
        elif st == "B2" and ma20 and px <= ma20:
            note = "突破失败→退回B1观察"
        elif st == "B3" and ma10 and px <= ma10:
            note = "破MA10→衰竭前兆"
        if note:
            alerts.append(f"{r['code']} {r.get('name', '')} {note} 现价{px}")

        print(f"{_pad(r['code'], 11)}{_pad(r.get('name', ''), 12)}{st:>5}"
              f"{px:>9.2f}{(pct if pct is not None else 0):>7.2f}%"
              f"{d_hi*100:>8.1f}%{d_10*100:>8.1f}%{d_20*100:>8.1f}%   {note}")
        shown += 1

    print(f"\n[intraday] 共 {shown} 只 / 触发 {len(alerts)} 条")
    for a in alerts:
        print("  ⚑ " + a)
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="全市场扫描（唯一模式，保留开关便于以后加 --update）")
    ap.add_argument("--intraday", action="store_true", help="盘中巡检：读最近池文件 + 实时价比对关键位")
    ap.add_argument("--min-amt", type=float, default=0.5, help="20 日均成交额下限（亿元），默认 0.5")
    ap.add_argument("--stage", default=None, help="只看某阶段 B1/B2/B3/C1")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    if args.intraday:
        return intraday()

    out, buckets, meta = scan(args.min_amt, args.stage, args.top)
    if args.save:
        os.makedirs(REPORTS, exist_ok=True)
        asof = meta.get("data_asof") or datetime.now().strftime("%Y%m%d")
        p = os.path.join(REPORTS, f"pool_state_{asof}.json")
        for lst in out.values():
            for r in lst:
                r.pop("_hb", None)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "data_asof": asof,
                       "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "buckets": out}, f, ensure_ascii=False, indent=1)
        print(f"\n[saved] {p}  (数据基准 {asof})")


if __name__ == "__main__":
    sys.exit(main() or 0)
