# -*- coding: utf-8 -*-
"""K35 三线联动 · 五信号历史命中率回放（2026-10-14 linkage-dev，只读）。

目的：影子 collector 上线前，用历史数据复现五信号判定，给出挂账与否的
数字依据（实施计划判据：影子样本命中率 ≥55% 再议挂账组合）。

口径（与产线 linkage_signals.judge_series v1 逐条对齐，1d 周期）：
  · 窗口收益 = 250 根日线窗口、公共首根=0% 归一化的末值——回放中要求
    个股/板块/大盘 250 窗口全有数据（rolling count==250 掩码），此时
    归一化基 = 窗口首根，与产线 ffill 对齐后的公共首根一致；
  · ① 抗跌：窗口收益(大盘<0 或 板块<0) 且 超额(个股-板块) ≥ +5pp
  · ② 滞涨：板块窗口收益>0 且 超额 ≤ -5pp
  · ③ 共振底：三线收盘同破各自近 60 根前低（不含最新一根）
  · ④ 孤军深入：个股破近 120 根前高而板块/大盘未全破
  · ⑤ 三共振：三线同破各自近 120 根前高（⑤/④ 互斥，产线 elif 同款）
  · 关键位 close 口径；产线归一化基在判定窗口内恒定 → 窗口内 pct 比较
    等价于原始 close 比较（同乘正常数不变号），故回放直接用原始 close
    rolling 极值，与产线 pct 序列极值判定严格等价。

命中定义（两种口径，T+k = 信号日收盘 → T+k 收盘）：
  · 口径A 绝对：个股前向收益 > 0
  · 口径B 跑赢板块：个股前向收益 - 所属板块前向收益 > 0

样本去重：信号常连续多日触发，按「连续触发段首日」计 1 个样本
（episode 去重），避免同一段行情重复计数虚增样本。

数据源（全部本地只读）：ts_daily / ts_index_daily(000001.SH) /
tdx_sector_daily / ts_stock_basic（行业→板块归属 linkage 同源）。

三档结论（对照 55% 判据，按口径A）：
  · 够格挂账：n≥30 且 命中率≥55%
  · 建议废弃：n≥30 且 命中率<45%
  · 继续攒样本：其余（含 n<30）

用法:
  python linkage_hitrate_backtest.py                        # 全量回放
  python linkage_hitrate_backtest.py --start 2024-01-01     # 截窗口
  python linkage_hitrate_backtest.py --out result.json
"""

import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WIN = 250  # 归一化窗口（产线 1d DEFAULT_LAST）
GAP = 5.0  # 超额阈值 pp（linkage_signals.GAP）
LO_WIN, HI_WIN = 60, 120
HORIZONS = (5, 10, 20)
INDEX_CODE = "000001.SH"
SIGNALS = {"1": "抗跌", "2": "滞涨", "3": "共振底", "4": "孤军深入", "5": "三共振"}


def load_universe():
    """{stock_code: block_code}，industry→板块归属 linkage 同源。"""
    import linkage as LK
    import tdx_read as tdxr

    df = tdxr.read_all("ts_stock_basic", columns=["ts_code", "industry"])
    out = {}
    for _, r in df.iterrows():
        ind = str(r["industry"] or "") or None
        bc = LK.resolve_industry_block(ind)
        if bc:
            out[str(r["ts_code"])] = bc
    return out


def load_frames():
    """(stock_df, block_df, idx_sr, uni)：全部对齐到并集日历，板块/大盘 ffill。"""
    import tdx_read as tdxr

    uni = load_universe()
    codes = sorted(uni)
    blocks = sorted(set(uni.values()))

    sd = tdxr.read_all("ts_daily", columns=["ts_code", "trade_date", "close"])
    sd = sd[sd["ts_code"].isin(set(codes))]
    sd["trade_date"] = sd["trade_date"].astype(str)
    stock = sd.pivot(index="trade_date", columns="ts_code", values="close").sort_index()

    bd = tdxr.read_all("tdx_sector_daily", columns=["code", "trade_date", "close"])
    bd = bd[bd["code"].isin(set(blocks))]
    bd["trade_date"] = bd["trade_date"].astype(str)
    block = bd.pivot(index="trade_date", columns="code", values="close").sort_index()

    idf = tdxr.read_all("ts_index_daily", columns=["ts_code", "trade_date", "close"])
    idf = idf[idf["ts_code"] == INDEX_CODE].copy()
    idf["trade_date"] = idf["trade_date"].astype(str)
    idx = idf.set_index("trade_date")["close"].sort_index()

    dates = sorted(set(stock.index) | set(block.index) | set(idx.index))
    stock = stock.reindex(dates)
    block = block.reindex(dates).ffill()
    idx = idx.reindex(dates).ffill()
    return stock, block, idx, uni


def _brk(df, win, mode):
    """收盘破位布尔阵：ref=近 win 根极值（shift(1) 排除最新一根）。"""
    ref = df.rolling(win).min() if mode == "lo" else df.rolling(win).max()
    ref = ref.shift(1)
    if mode == "lo":
        return df.notna() & ref.notna() & (df < ref)
    return df.notna() & ref.notna() & (df > ref)


def _bool(df):
    """统一布尔化（部分列可能混入 NaN → object，~ 运算会炸）。"""
    return df.fillna(False).astype(bool)


def judge_matrix(stock, block, idx, uni):
    """全矩阵判定 → {sig_tag: bool_df}（与产线 judge_series 逐条对齐）。"""
    cols = list(stock.columns)
    blk_codes = sorted(set(uni.values()))
    bfull = block.reindex(columns=blk_codes)

    # 窗口收益（归一化末值）：250 窗口全有数据时基=窗口首根
    valid_s = stock.rolling(WIN).count() == WIN
    valid_b = bfull.rolling(WIN).count() == WIN
    r_s = (stock / stock.shift(WIN - 1) - 1) * 100
    r_b_all = (bfull / bfull.shift(WIN - 1) - 1) * 100
    r_b = pd.DataFrame({c: r_b_all[uni[c]] for c in cols})
    r_i = (idx / idx.shift(WIN - 1) - 1) * 100
    r_i_df = pd.DataFrame({c: r_i for c in cols})
    vb_df = pd.DataFrame({c: valid_b[uni[c]] for c in cols})
    valid = valid_s & vb_df & pd.DataFrame({c: r_i.notna() for c in cols})

    ex = r_s - r_b
    sig = {}
    sig["2"] = valid & (r_b > 0) & (ex <= -GAP)
    sig["1"] = valid & ((r_i_df < 0) | (r_b < 0)) & (ex >= GAP)

    # 破位（原始 close 口径，基不变性）
    lo_s_all, hi_s_all = _brk(stock, LO_WIN, "lo"), _brk(stock, HI_WIN, "hi")
    lo_b_all, hi_b_all = _brk(bfull, LO_WIN, "lo"), _brk(bfull, HI_WIN, "hi")
    lo_i = _brk(idx.to_frame("c"), LO_WIN, "lo")["c"]
    hi_i = _brk(idx.to_frame("c"), HI_WIN, "hi")["c"]
    sig["3"] = _bool(
        valid
        & lo_s_all
        & pd.DataFrame({c: lo_b_all[uni[c]] for c in cols})
        & pd.DataFrame({c: lo_i for c in cols})
    )
    hi5 = _bool(
        valid
        & hi_s_all
        & pd.DataFrame({c: hi_b_all[uni[c]] for c in cols})
        & pd.DataFrame({c: hi_i for c in cols})
    )
    sig["5"] = hi5
    sig["4"] = _bool(valid & hi_s_all & ~hi5)
    return sig


def episode_starts(bdf):
    """连续触发段首日：fire & ~fire.shift(1)。"""
    return bdf & ~bdf.shift(1).fillna(False)


def run(horizons=HORIZONS, start=None, end=None):
    stock, block, idx, uni = load_frames()
    sig = judge_matrix(stock, block, idx, uni)
    if start:
        sig = {t: df.loc[df.index >= start] for t, df in sig.items()}
    if end:
        sig = {t: df.loc[df.index <= end] for t, df in sig.items()}

    # 前向收益（列=股票）与超额，k 只算一次
    fwd_all = {k: stock.shift(-k) / stock - 1 for k in horizons}
    ex_all = {
        k: fwd_all[k]
        - pd.DataFrame(
            {c: (block[uni[c]].shift(-k) / block[uni[c]] - 1) for c in stock.columns}
        )
        for k in horizons
    }

    result = {}
    n_ep = {}
    for tag, df in sig.items():
        starts = episode_starts(df)
        n_ep[tag] = int(starts.values.sum())
        outs = {}
        for k in horizons:
            fwd, ex = fwd_all[k], ex_all[k]
            rows = {}
            for code in starts.columns:
                col = starts[code]
                if not col.any():
                    continue
                dts = col.index[col]
                for dt in dts:
                    fv = fwd.at[dt, code]
                    ev = ex.at[dt, code]
                    if pd.isna(fv):
                        continue  # 前向窗口不足，弃样
                    for cal, v in (("abs", fv), ("rel", ev)):
                        if pd.isna(v):
                            continue
                        r = rows.setdefault(cal, [0, 0, 0.0, 0.0, 0.0])
                        r[0] += 1
                        r[1] += 1 if v > 0 else 0
                        r[2] += v
                        r[3] += max(v, 0.0)
                        r[4] += max(-v, 0.0)
            kk = {}
            for cal, (n, win, s, gp, gl) in sorted(rows.items()):
                kk[cal] = {
                    "n": n,
                    "hits": win,
                    "hitrate": round(win / n, 4) if n else None,
                    "avg_ret": round(s / n, 5) if n else None,
                    "pf": round(gp / gl, 3) if gl > 0 else None,
                }
            outs[k] = kk
        result[tag] = outs

    verdicts = {}
    for tag, outs in result.items():
        v = {}
        for k, kk in outs.items():
            h = kk.get("abs", {}).get("hitrate")
            n = kk.get("abs", {}).get("n", 0)
            if n >= 30 and h is not None and h >= 0.55:
                v[k] = "够格挂账"
            elif n >= 30 and h is not None and h < 0.45:
                v[k] = "建议废弃"
            else:
                v[k] = "继续攒样本"
        verdicts[tag] = v
    return result, verdicts, n_ep


def main():
    ap = argparse.ArgumentParser(description="五信号历史命中率回放（只读）")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    result, verdicts, n_ep = run(start=a.start, end=a.end)
    print(
        "== 五信号历史命中率回放（窗口%d根｜GAP%.0fpp｜lo%d/hi%d根｜"
        "episode去重）==" % (WIN, GAP, LO_WIN, HI_WIN)
    )
    print("口径A=绝对收益>0；口径B=跑赢板块；T+k 自信号日收盘起算")
    for tag, name in SIGNALS.items():
        print("-- 信号%s %s（episode数 %d）--" % (tag, name, n_ep[tag]))
        for k, kk in result[tag].items():
            A, B = kk.get("abs", {}), kk.get("rel", {})

            def _pct(x):
                return "-" if x is None else "%.1f%%" % (x * 100)

            def _r(x):
                return "-" if x is None else "%.2f%%" % (x * 100)

            print(
                "  T+%2d  A: n=%-6d 命中=%-6s 均收=%-7s PF=%-6s | "
                "B: n=%-6d 命中=%-6s 超额均=%-7s PF=%-6s → %s"
                % (
                    k,
                    A.get("n", 0),
                    _pct(A.get("hitrate")),
                    _r(A.get("avg_ret")),
                    A.get("pf", "-"),
                    B.get("n", 0),
                    _pct(B.get("hitrate")),
                    _r(B.get("avg_ret")),
                    B.get("pf", "-"),
                    verdicts[tag][k],
                )
            )
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(
                {"result": result, "verdicts": verdicts, "episode_counts": n_ep},
                f,
                ensure_ascii=False,
                indent=1,
            )
        print("明细 → %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
