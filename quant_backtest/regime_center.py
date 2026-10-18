# -*- coding: utf-8 -*-
"""策源中心 · 当日大盘状况引擎（regime_center）

定位
  策源中心 = 当日全市场状况的唯一策源地。每交易日产出一份确定性「市场状态
  快照」，供聚合门控、盯盘双池、RiskGuard 与门户展示统一消费。

判据来源（零新发明，逐条对齐 watching-standard-capability-v1.0 §5.1）
  market_regime_gate 允许做多三条：
    close > MA60                       → gate.ma60_above
    MA60[t] > MA60[t-5]                → gate.slope_up（MA60 五日斜率向上）
    MACD DIF > 0                       → gate.dif_positive
  risk_off 否决四条（任一即 risk_off）：
    连续 2 日收盘低于 MA60
    MA60 斜率向下
    DIF < 0 且 DEA < 0
    广度/情绪进入 risk_off（本实现：up_ratio < UP_RATIO_RISK_OFF，见拍板项）

输出契约
  与盯盘规格 market_snapshot 同形（index/breadth/regime/gates）+ 三市成交额，
  落盘 data/regime_center/snapshot_<trade_date>.json（每日一份 = 一条最小颗粒事实）。

纪律（不可协商）
  · 零 LLM、零网络：只读本地 index_daily parquet + datamart 只读层；
    指数刷新由 index_daily.py --pull 单独负责，本引擎不联网；
  · 缺源标灰不假绿：任一数据环缺失 → available=false + gaps 显式列出，
    不补默认值（对齐 watching v1.0 §9 数据质量门：不猜、留痕）；
  · 只读影子：本模块只判定与记录，不拦截任何信号（策源中心无否决执行权）。

用法
  python -X utf8 regime_center.py            # 生成最新交易日快照
  python -X utf8 regime_center.py --date 20261016
  python -X utf8 regime_center.py --show     # 打印最新快照
  python -X utf8 regime_center.py --stdout   # 只打印不落盘（调试/单测）
"""
import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "regime_center")
INDEX_PARQUET = os.path.join(HERE, "data", "index_daily", "sh000001.parquet")

VERSION = "regime_center-1.0.0"
INDEX_CODE = "000001.SH"
INDEX_NAME = "上证指数"

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
MA20, MA60 = 20, 60
SLOPE_LAG = 5               # MA60[t] vs MA60[t-5]
UP_RATIO_RISK_OFF = 0.35    # 广度 risk_off 阈值（拍板项：待老田确认）
MARKETS = [".SH", ".SZ", ".BJ"]


# ── 指标（纯函数，确定性可复现）────────────────────────────────
def ema(series, n):
    return pd.Series(series).ewm(span=n, adjust=False).mean()


def macd(close):
    dif = ema(close, MACD_FAST) - ema(close, MACD_SLOW)
    dea = ema(dif, MACD_SIGNAL)
    return dif, dea


def _f(v, nd=4):
    try:
        return round(float(v), nd)
    except (TypeError, ValueError):
        return None


# ── 指数特征 ─────────────────────────────────────────────────
def index_features(df, trade_date=None):
    """输入 tidy 后的指数日线（含 trade_date/close，按日期升序）。
    返回 {available, trade_date, close, ma20, ma60, ma60_slope5, macd_dif, macd_dea,
          close_below_ma60_streak}；数据不足返回 available=False。"""
    out = {"available": False, "code": INDEX_CODE, "name": INDEX_NAME}
    if df is None or not len(df):
        out["reason"] = "index_empty"
        return out
    d = df.sort_values("trade_date").reset_index(drop=True)
    if trade_date:
        d = d[d["trade_date"].astype(str).str[:8] <= str(trade_date)]
    if len(d) < MA60 + SLOPE_LAG + 1:
        out["reason"] = "index_insufficient_rows:%d" % len(d)
        return out

    close = d["close"].astype(float)
    ma20 = close.rolling(MA20).mean()
    ma60 = close.rolling(MA60).mean()
    dif, dea = macd(close)
    i = len(d) - 1
    below = (close < ma60).astype(bool)
    streak = 0
    for k in range(i, -1, -1):
        if bool(below.iloc[k]):
            streak += 1
        else:
            break
    out.update({
        "available": True,
        "trade_date": str(d["trade_date"].iloc[-1])[:8],
        "close": _f(close.iloc[-1]),
        "prev_close": _f(close.iloc[-2]) if len(d) > 1 else None,
        "ma20": _f(ma20.iloc[-1]),
        "ma60": _f(ma60.iloc[-1]),
        "ma60_slope5": _f(ma60.iloc[-1] - ma60.iloc[-1 - SLOPE_LAG]),
        "macd_dif": _f(dif.iloc[-1]),
        "macd_dea": _f(dea.iloc[-1]),
        "close_below_ma60_streak": int(streak),
    })
    if out["prev_close"]:
        out["chg_pct"] = _f((out["close"] / out["prev_close"] - 1) * 100, 2)
    return out


# ── 判据（§5.1 逐条）──────────────────────────────────────────
def evaluate(idx, breadth=None):
    """返回 {regime, gates, risk_off_reasons, unavailable_reasons}。
    idx/breadth 缺源时不做猜测，登记 unavailable。"""
    gates, off, unavail = {}, [], []
    if not idx.get("available"):
        return {"regime": "unknown", "gates": {},
                "risk_off_reasons": [],
                "unavailable_reasons": ["index:%s" % idx.get("reason", "unknown")]}

    c, m60 = idx.get("close"), idx.get("ma60")
    gates["ma60_above"] = bool(c is not None and m60 is not None and c > m60)
    gates["slope_up"] = bool(idx.get("ma60_slope5") is not None
                             and idx["ma60_slope5"] > 0)
    gates["dif_positive"] = bool(idx.get("macd_dif") is not None
                                 and idx["macd_dif"] > 0)

    if idx.get("close_below_ma60_streak", 0) >= 2:
        off.append("close_below_ma60_2d(streak=%d)" % idx["close_below_ma60_streak"])
    if not gates["slope_up"]:
        off.append("ma60_slope_down")
    if (idx.get("macd_dif") is not None and idx.get("macd_dea") is not None
            and idx["macd_dif"] < 0 and idx["macd_dea"] < 0):
        off.append("dif_and_dea_both_negative")
    if breadth and breadth.get("available"):
        if breadth.get("up_ratio") is not None and breadth["up_ratio"] < UP_RATIO_RISK_OFF:
            off.append("breadth_risk_off(up_ratio=%.3f<%.2f)"
                       % (breadth["up_ratio"], UP_RATIO_RISK_OFF))
    elif breadth is not None:
        unavail.append("breadth:%s" % breadth.get("reason", "unknown"))

    if off:
        regime = "risk_off"
    elif all(gates.values()):
        regime = "risk_on"
    else:
        regime = "neutral"
    return {"regime": regime, "gates": gates,
            "risk_off_reasons": off, "unavailable_reasons": unavail}


# ── 广度与总量（datamart 只读，缺源不猜）──────────────────────
def breadth_volume(trade_date, reader=None):
    """读 ts_daily 全市场涨跌家数与三市成交额。返回 {available, ...}。"""
    out = {"available": False, "trade_date": str(trade_date)}
    try:
        import datamart as dm
        read = reader or dm.read
        df = read("ts_daily", start=str(trade_date), end=str(trade_date),
                  fields=["ts_code", "trade_date", "change", "amount", "vol"])
    except Exception as e:
        out["reason"] = "ts_daily_error:%s" % type(e).__name__
        return out
    if df is None or not len(df):
        out["reason"] = "ts_daily_empty"
        return out
    d = df.copy()
    d["suf"] = d["ts_code"].astype(str).str[-3:]
    d = d[d["trade_date"].astype(str).str[:8] == str(trade_date)]
    if not len(d):
        out["reason"] = "ts_daily_no_row_for_date"
        return out

    up = int((pd.to_numeric(d["change"], errors="coerce") > 0).sum())
    down = int((pd.to_numeric(d["change"], errors="coerce") < 0).sum())
    out.update({
        "available": True,
        "up_count": up,
        "down_count": down,
        "up_ratio": _f(up / max(up + down, 1), 4),
        "amount_yi": {},
    })
    for s in MARKETS:
        g = d[d["suf"] == s]
        out["amount_yi"][s.strip(".")] = _f(
            float(pd.to_numeric(g["amount"], errors="coerce").sum()) / 1e5, 1)
    out["amount_total_yi"] = _f(sum(v or 0 for v in out["amount_yi"].values()), 1)
    return out


# ── 快照 ─────────────────────────────────────────────────────
def build_snapshot(trade_date=None, index_path=INDEX_PARQUET, reader=None):
    gaps = []
    try:
        raw = pd.read_parquet(index_path)
    except Exception as e:
        raw = pd.DataFrame()
        gaps.append("index_unreadable:%s" % type(e).__name__)

    idx = index_features(raw if len(raw) else None, trade_date=trade_date)
    if not idx.get("available"):
        gaps.append("index:%s" % idx.get("reason", "unknown"))
    td = trade_date or idx.get("trade_date")
    if trade_date and idx.get("trade_date") and str(idx["trade_date"]) != str(trade_date):
        gaps.append("index_stale:latest=%s requested=%s"
                    % (idx["trade_date"], trade_date))

    bv = breadth_volume(td, reader=reader) if td else {"available": False}
    if not bv.get("available"):
        gaps.append("breadth:%s" % bv.get("reason", "unknown"))

    verdict = evaluate(idx, bv)
    return {
        "trade_date": str(td) if td else None,
        "regime": verdict["regime"],
        "gates": verdict["gates"],
        "risk_off_reasons": verdict["risk_off_reasons"],
        "index": idx,
        "breadth": bv,
        "data_quality": {
            "available": bool(idx.get("available")) and bool(bv.get("available")),
            "gaps": gaps + verdict.get("unavailable_reasons", []),
        },
        "criteria": {
            "source": "watching-standard-capability-v1.0 §5.1 market_regime_gate",
            "gates": ["close>MA60", "MA60[t]>MA60[t-5]", "MACD_DIF>0"],
            "risk_off": ["close<MA60 连续2日", "MA60斜率向下",
                         "DIF<0且DEA<0", "up_ratio<%.2f" % UP_RATIO_RISK_OFF],
            "macd": "%d/%d/%d" % (MACD_FAST, MACD_SLOW, MACD_SIGNAL),
        },
        "version": VERSION,
    }


def latest_snapshot(out_dir=None):
    import glob
    files = sorted(glob.glob(os.path.join(out_dir or OUT_DIR, "snapshot_*.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def write_snapshot(snap, out_dir=None):
    """原子写；同一交易日重跑幂等覆盖。out_dir 运行时解析（便于测试隔离）。"""
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "snapshot_%s.json" % snap["trade_date"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="策源中心 · 当日大盘状况快照")
    ap.add_argument("--date", help="交易日 YYYYMMDD（默认取指数底座最新日）")
    ap.add_argument("--show", action="store_true", help="打印最新快照")
    ap.add_argument("--stdout", action="store_true", help="只打印不落盘")
    a = ap.parse_args(argv)

    if a.show:
        snap = latest_snapshot()
        print(json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True)
              if snap else "NO_SNAPSHOT (先跑一次 regime_center.py)")
        return 0

    snap = build_snapshot(trade_date=a.date)
    if a.stdout:
        print(json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    path = write_snapshot(snap)
    print("regime=%s trade_date=%s | %s"
          % (snap["regime"], snap["trade_date"], path))
    if snap["data_quality"]["gaps"]:
        sys.stderr.write("[regime_center] WARN gaps=%s\n"
                         % "; ".join(snap["data_quality"]["gaps"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
