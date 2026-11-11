# -*- coding: utf-8 -*-
"""同花顺板块口径复刻 strong_scan 的「板块领涨」判定（只读验证，不改生产）。

背景（2026-11-11 实测）：
  tdx_sector_daily 上游 rookie.xtcloud 自 2026-10-13 起只写 880001~880003
  三个市场码，2026-10-22 17:00 之后零新增（远端 offset=0 即 20261022）。
  本地库是上游的忠实镜像，不是本地漏跑。

本模块用同花顺板块三件套平移口径：
  ts_ths_index (板块定义) + ts_ths_member (成分) + ts_ths_daily (日线)
判定链与 strong_track.strong_verdict 逐条对齐：
  ex250 = 个股 250 日涨幅 - 板块 250 日涨幅，达标 >= +10pp
  板块成员 >= 5；个股 last > MA20 > MA60 → 「板块领涨」
  超额达标但非多头 → 「逆板块强势」（只进名单不推送）

只读：不写任何 parquet、不碰 _manifest.json、不改 strong_latest.json。
"""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TDX = os.path.join(HERE, "data", "tdx")

STRONG_EX_TH = 10.0    # 250 日超额达标（pp），与 strong_track.STRONG_EX_TH 一致
MIN_BLOCK_MEMBERS = 5  # 板块成员门槛，与 strong_track.MIN_BLOCK_MEMBERS 一致
WINDOW = 250           # 超额回看窗口（交易日）
LOOKBACK_DAYS = 320    # 宽表保留的日历交易日数量（留足 250 根余量）


def _read_parts(table: str, columns=None) -> pd.DataFrame:
    fs = sorted(glob.glob(os.path.join(TDX, table, "part_*.parquet")))
    if not fs:
        raise FileNotFoundError("缺少数据表 %s（%s）" % (table, os.path.join(TDX, table)))
    return pd.concat([pd.read_parquet(f, columns=columns) for f in fs], ignore_index=True)


def load_index(types=("I",)) -> pd.DataFrame:
    """板块定义。type: I=行业 N=指数 TH=主题 S=风格 BB=宽基 R=地域 ST=统计。"""
    idx = _read_parts("ts_ths_index", columns=["ts_code", "name", "count", "type"])
    idx = idx.drop_duplicates("ts_code")
    if types:
        idx = idx[idx["type"].isin(types)]
    return idx.reset_index(drop=True)


def load_members() -> dict[str, list[tuple[str, str]]]:
    """板块 → [(个股代码, 名称)]。"""
    mem = _read_parts("ts_ths_member", columns=["ts_code", "con_code", "con_name"])
    mem = mem.drop_duplicates(["ts_code", "con_code"])
    out: dict[str, list[tuple[str, str]]] = {}
    for tc, grp in mem.groupby("ts_code", sort=False):
        out[str(tc)] = list(zip(grp["con_code"].astype(str), grp["con_name"].astype(str)))
    return out


def _wide_from_long(df: pd.DataFrame, code_col: str) -> pd.DataFrame:
    """长表 → 宽表(index=trade_date, columns=code)，只留最近 LOOKBACK_DAYS 个交易日。"""
    days = sorted(df["trade_date"].astype(str).unique())[-LOOKBACK_DAYS:]
    keep = df[df["trade_date"].astype(str).isin(set(days))]
    w = keep.pivot_table(index="trade_date", columns=code_col, values="close", aggfunc="last")
    w.index = w.index.astype(str)
    return w.sort_index()


def load_block_px(columns=None) -> pd.DataFrame:
    df = _read_parts("ts_ths_daily", columns=["ts_code", "trade_date", "close"])
    return _wide_from_long(df, "ts_code")


def load_stock_px() -> pd.DataFrame:
    df = _read_parts("ts_daily", columns=["ts_code", "trade_date", "close"])
    return _wide_from_long(df, "ts_code")


def trend_bull(closes) -> bool:
    """last > MA20 > MA60；样本 <60 根不可判 → False（与 strong_track 同口径）。"""
    if closes is None or len(closes) < 60:
        return False
    vals = list(closes)
    last = vals[-1]
    ma20 = sum(vals[-20:]) / 20.0
    ma60 = sum(vals[-60:]) / 60.0
    return last > ma20 > ma60


def ret_window(series: pd.Series, n: int = WINDOW) -> float | None:
    """n 个交易日累计涨幅（%）。不足 n+1 个有效点 → None。"""
    s = series.dropna()
    if len(s) < n + 1:
        return None
    base = float(s.iloc[-(n + 1)])
    last = float(s.iloc[-1])
    if base <= 0:
        return None
    return (last / base - 1.0) * 100.0


def scan(types=("I",), cap_members: int = 250, min_members: int = MIN_BLOCK_MEMBERS):

    idx = load_index(types)
    members = load_members()
    bpx = load_block_px()
    spx = load_stock_px()

    basis = bpx.index[-1]
    # 对齐基准日：个股宽表截到板块最新日，保证 ex250 同口径
    if basis in spx.index:
        spx = spx.loc[:basis]
    else:
        sys.stderr.write("!! 个股表无 %s，最近日 %s\n" % (basis, spx.index[-1]))
        spx = spx.loc[:basis]

    hits, skipped = [], []
    n_blocks_eval = 0
    for _, row in idx.iterrows():
        bc, bname = str(row["ts_code"]), str(row["name"])
        mem = members.get(bc) or []
        if len(mem) < min_members:
            skipped.append((bc, bname, len(mem), "成员不足"))
            continue
        if bc not in bpx.columns:
            skipped.append((bc, bname, len(mem), "无板块日线"))
            continue
        b_ret = ret_window(bpx[bc])
        if b_ret is None:
            skipped.append((bc, bname, len(mem), "板块日线不足250根"))
            continue
        n_blocks_eval += 1
        sel = mem[:cap_members]
        for code, cname in sel:
            if code not in spx.columns:
                continue
            s = spx[code].dropna()
            if len(s) < WINDOW + 1:
                continue
            s_ret = ret_window(s)
            if s_ret is None:
                continue
            ex = s_ret - b_ret
            if ex < STRONG_EX_TH:
                continue
            bull = trend_bull(s)
            hits.append(
                {
                    "code": code,
                    "name": cname,
                    "block": bname,
                    "block_code": bc,
                    "ex250": round(ex, 1),
                    "ret250": round(s_ret, 1),
                    "b_ret250": round(b_ret, 1),
                    "tag": "板块领涨" if bull else "逆板块强势",
                    "n_bars": len(s),
                    "last": round(float(s.iloc[-1]), 2),
                    "basis": basis,
                }
            )
    return {
        "basis": basis,
        "n_blocks_total": len(idx),
        "n_blocks_eval": n_blocks_eval,
        "hits": hits,
        "skipped": skipped,
        "block_px": bpx,
        "stock_px": spx,
    }


if __name__ == "__main__":
    r = scan()
    hits = r["hits"]
    print("基准日 %s（板块口径最新日）" % r["basis"])
    print(
        "板块池 %d 个(行业I) / 实际评估 %d 个 / 命中 %d 只"
        % (r["n_blocks_total"], r["n_blocks_eval"], len(hits))
    )
    lead = [h for h in hits if h["tag"] == "板块领涨"]
    print("  板块领涨 %d，逆板块强势 %d" % (len(lead), len(hits) - len(lead)))
    print()
    print("%-11s %-7s %-14s %7s %7s %7s" % ("code", "name", "板块", "ex250", "ret250", "block"))
    for h in sorted(hits, key=lambda x: -x["ex250"])[:30]:
        print(
            "%-11s %-7s %-14s %+7.1f %+7.1f %+7.1f"
            % (h["code"], h["name"][:6], h["block"][:12], h["ex250"], h["ret250"], h["b_ret250"])
        )
    print()
    bad = [s for s in r["skipped"] if s[3] != "成员不足"]
    if bad:
        print("异常板块 %d 个（前 5）：" % len(bad))
        for s in bad[:5]:
            print("   ", s)
