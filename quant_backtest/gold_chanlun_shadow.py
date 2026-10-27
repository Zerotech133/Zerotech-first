# -*- coding: utf-8 -*-
"""gold_chanlun_shadow: 沪金（AU.SHF）缠论影子统计——黄金期货×缠论可行性首证。

数据：tushare fut_mapping(AU.SHF) 主力映射 → 分段 fut_daily 拼接（品种级
全史，未复权主力连续，与 cn_fut_backtest 同口径；换月跳空未平滑——影子
统计注记，后续如需可换收益率口径平滑）。
引擎：chanlun_bi.analyze（OHLC 通用；缠论首次应用于期货——纯影子统计，
不改任何信号/推送/入池口径）。
统计：
  · 全部 1/2/3 类买卖点 × d1/3/5/10/20 方向前向收益
    （b: c[i+N]/c[i]-1；s: 取反；基准=信号日收盘=笔终点分型日；窗不足丢弃）
  · 1 类按 sub（tbe 趋势背驰 / pbe 盘整背驰）分层
  · beta 对照 = 全样本任意日同持有期的市场基准收益（随机日进场）
  · d20 按年度分桶（月度稳定性随 TbeStats 日更数据扩展）
产出：stdout 摘要 + data/gold_chanlun_shadow.json（原子写）。
用法：python gold_chanlun_shadow.py [years]   # 默认 20（AU 2008 上市全史）
"""

import json
import os
import sys
import tempfile
from datetime import datetime

import pandas as pd
import tushare as ts

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from chanlun_bi import analyze  # noqa: E402

OUT = os.path.join(HERE, "data", "gold_chanlun_shadow.json")
HOLDS = (1, 3, 5, 10, 20)
CN = {"b1": "1买", "s1": "1卖", "b2": "2买", "s2": "2卖", "b3": "3买", "s3": "3卖"}
SYM = "AU.SHF"


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(text)
    os.replace(tmp, path)


def build_continuous(sym: str, years: int) -> pd.DataFrame:
    """主力映射 → 分段 fut_daily → 拼接连续日线（升序，含 high/low）。"""
    pro = ts.pro_api()
    mp = pro.fut_mapping(ts_code=sym)
    if mp is None or len(mp) == 0:
        return pd.DataFrame()
    mp = mp.sort_values("trade_date").reset_index(drop=True)
    if years:
        cut = (pd.Timestamp.today() - pd.DateOffset(years=years)).strftime("%Y%m%d")
        mp = mp[mp["trade_date"] >= cut]
    seg = mp.copy()
    seg["new_seg"] = seg["mapping_ts_code"] != seg["mapping_ts_code"].shift()
    seg["seg_id"] = seg["new_seg"].cumsum()
    frames = []
    for sid, g in seg.groupby("seg_id"):
        contract = g.iloc[0]["mapping_ts_code"]
        s, e = g["trade_date"].min(), g["trade_date"].max()
        try:
            d = pro.fut_daily(ts_code=contract, start_date=s, end_date=e)
            if len(d):
                frames.append(d[["trade_date", "open", "high", "low", "close", "vol"]])
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = (
        pd.concat(frames)
        .drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close", "high", "low"])


def _grp(rs):
    if not rs:
        return None
    v = sorted(rs)
    n = len(v)
    win = sum(1 for x in v if x > 0) / n * 100
    avg = sum(v) / n * 100
    med = v[n // 2] * 100 if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2 * 100
    return {"n": n, "win": round(win, 1), "avg": round(avg, 2), "med": round(med, 2)}


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    df = build_continuous(SYM, years)
    if len(df) < 60:
        print("bars insufficient: %d" % len(df))
        return 1

    dts = [
        "%s-%s-%s" % (str(v)[:4], str(v)[4:6], str(v)[6:8]) for v in df["trade_date"]
    ]
    bars = [
        {"dt": dts[i], "h": float(r["high"]), "l": float(r["low"])}
        for i, (_, r) in enumerate(df.iterrows())
    ]
    closes = [float(r["close"]) for _, r in df.iterrows()]
    opens = [float(r["open"]) for _, r in df.iterrows()]
    st = analyze(bars, closes)
    idx = {b["dt"]: i for i, b in enumerate(bars)}

    groups, by_year = {}, {}
    for s in st["signals"]:
        i0 = idx.get(s["d"])
        if i0 is None or not closes[i0]:
            continue
        sub = s.get("sub") or ""
        key = (s["t"], sub)
        g = groups.setdefault(key, {h: [] for h in HOLDS})
        sign = 1.0 if s["t"].startswith("b") else -1.0
        for h in HOLDS:
            j = i0 + h
            if j < len(closes) and closes[j]:
                g[h].append(sign * (closes[j] / closes[i0] - 1.0))
        j20 = i0 + 20
        if j20 < len(closes) and closes[i0]:
            yr = s["d"][:4]
            by_year.setdefault((s["t"], sub, yr), []).append(
                sign * (closes[j20] / closes[i0] - 1.0)
            )

    # beta 对照：全样本任意日进场同持有期
    beta = {}
    for h in HOLDS:
        beta[h] = _grp(
            [closes[i + h] / closes[i] - 1.0 for i in range(len(closes) - h)]
        )

    # —— 可成交价确认滞后重放（b3/s3）：信号日后第 L 根以开盘价进场 ——
    # dN = 进场日开盘价 → N 个交易日后收盘；L=1 为 T+1 开盘现实口径，
    # L 越大越保守（缠论笔/分型确认天然滞后 1~3 根）
    REPLAY_T = ("b3", "s3")
    LAGS = (0, 1, 2, 3)
    replay = {}
    for t in REPLAY_T:
        sign = 1.0 if t.startswith("b") else -1.0
        sigs = [s for s in st["signals"] if s["t"] == t]
        for L in LAGS:
            for h in HOLDS:
                rs = []
                for s in sigs:
                    i0 = idx.get(s["d"])
                    if i0 is None:
                        continue
                    e = i0 + L
                    if e >= len(opens) or not opens[e]:
                        continue
                    j = e + h
                    if j < len(closes):
                        rs.append(sign * (closes[j] / opens[e] - 1.0))
                replay["%s_L%d_d%d" % (t, L, h)] = _grp(rs)
    # beta_open：任意日开盘进场（同口径基准，L=1 视角）
    for h in HOLDS:
        replay["beta_open_L1_d%d" % h] = _grp(
            [closes[i + 1 + h] / opens[i + 1] - 1.0 for i in range(len(closes) - 1 - h)]
        )

    out_groups = {}
    lines = [
        "== 沪金 AU.SHF 缠论影子统计（%s ~ %s，%d 根日线，主力连续未复权）"
        % (dts[0], dts[-1], len(bars)),
        "beta 对照（任意日进场）: "
        + " | ".join(
            "d%d avg%+.2f%% win%.0f%%" % (h, beta[h]["avg"], beta[h]["win"])
            for h in HOLDS
        ),
    ]
    for t, sub in sorted(groups):
        g = groups[(t, sub)]
        label = CN.get(t, t) + (("·" + sub) if sub else "")
        segs = []
        row = {}
        for h in HOLDS:
            r = _grp(g[h])
            row["d%d" % h] = r
            if r:
                segs.append(
                    "d%d n=%d win=%.0f%% avg=%+.2f%%" % (h, r["n"], r["win"], r["avg"])
                )
        out_groups["%s_%s" % (t, sub) if sub else t] = row
        lines.append("%s: %s" % (label, " | ".join(segs) if segs else "无（窗不足）"))
    out_by_year = {
        "%s_%s_%s" % (t, sub or "-", y): _grp(v)
        for (t, sub, y), v in sorted(by_year.items())
    }
    lines.append("d20 年度分桶 → json（by_year 键）")
    lines.append("—— 可成交价重放（b3，开盘价进场，d5/d10/d20）——")
    for L in LAGS:
        segs = []
        for h in (5, 10, 20):
            r = replay.get("%s_L%d_d%d" % ("b3", L, h))
            if r:
                segs.append(
                    "d%d n=%d win=%.0f%% avg=%+.2f%%" % (h, r["n"], r["win"], r["avg"])
                )
        lines.append("  L=%d: %s" % (L, " | ".join(segs) or "无"))
    r1 = replay.get("s3_L1_d10")
    if r1:
        lines.append(
            "  s3 L=1 d10: n=%d win=%.0f%% avg=%+.2f%%"
            % (r1["n"], r1["win"], r1["avg"])
        )
    bo = replay.get("beta_open_L1_d20")
    if bo:
        lines.append(
            "  beta_open L=1 d20: avg=%+.2f%% win=%.0f%%" % (bo["avg"], bo["win"])
        )

    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": SYM,
        "bars": len(bars),
        "range": [dts[0], dts[-1]],
        "n_signals": len(st["signals"]),
        "beta": beta,
        "replay": replay,
        "groups": out_groups,
        "by_year": out_by_year,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    _atomic_write(OUT, json.dumps(out, ensure_ascii=False, indent=1))
    print("\n".join(lines))
    print("影子落盘: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
