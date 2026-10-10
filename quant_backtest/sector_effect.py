# -*- coding: utf-8 -*-
"""板块效应层（青萍之末策略 · 影子模块）：板块 RPS + 板块共性 + 龙头强度。

方法论（青萍之末2 板块效应维，本站独立实现）:
  · 板块 RPS: 板块指数 N 日涨幅在全部板块指数中的截面百分位（0-100，
    越高越强），N=50 为主；
  · 板块共性: 成分股中"结构相似"（收盘在 20 日线上且 20 日线 5 日前
    斜率为正）比例、短期多头排列（MA5>MA10>MA20）比例；
  · 龙头强度: 板块内 5 日涨幅前 3 名相对板块指数的超额均值，
    前 3 名平均涨幅 ≥ 板块指数 5 日涨幅的 1.5 倍判定为强龙头。

数据口径（tdx_read 只读层）:
  · 板块指数: data/tdx/tdx_sector_daily（880xxx/881xxx 日线），
    板块定义 tdx_sector_def（排除 stat/region 类）；
  · 成分映射: tdx_sector_member（block_name → tdx_sector_def.name →
    指数代码）。注意 member 只覆盖概念/风格类约 41 个板块，
    industry881 行业板块暂无成分映射 → 其共性/龙头字段输出 null；
  · 个股日线: data/ashare/<code>_<MKT>.parquet（close 列，尾部 30 根）。

影子声明: 本模块只读数据、只写 reports/sector_effect.json（原子写），
不推送飞书、不注册计划任务、不改动任何现有策略文件。供后续把
板块维度接入 rps_resonance.py 共振打分前做离线验证。

用法:
  python sector_effect.py --scan            # 全板块扫描 → JSON
  python sector_effect.py --scan --top 5    # 附带打印前 5 摘要
  python sector_effect.py --sector 黄金概念  # 单板块调试
"""
import argparse
import glob
import json
import os
import sys
import tempfile
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TDX = os.path.join(HERE, "data", "tdx")
ASHARE = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")
OUT_JSON = os.path.join(REPORTS, "sector_effect.json")

RPS_N = 50                 # 板块 RPS 主周期
LEADER_TOPN = 3            # 龙头取前 N
STRONG_RATIO = 1.5         # 龙头强度阈值: 前3均涨幅 ≥ 板块涨幅 × 1.5
MIN_MEMBERS = 5            # 成分股不足该数跳过共性/龙头
TAIL_BARS = 30             # 个股只需尾部 30 根（MA20+斜率5 / chg5）
# 排除的板块 seg: 统计类与地区类无板块效应意义
EXCLUDE_SEG = {"stat", "region"}


# ── 数据读取 ───────────────────────────────────────────────
def _read_parts(tbl, columns=None):
    """tdx 分片表全读（与 tdx_read 同源，直接复用其目录布局）。"""
    parts = sorted(glob.glob(os.path.join(TDX, tbl, "part_*.parquet")))
    if not parts:
        return pd.DataFrame()
    try:
        return pd.concat([pd.read_parquet(p, columns=columns)
                          for p in parts], ignore_index=True)
    except Exception:
        return pd.DataFrame()


def load_universe():
    """板块全集 → {code: name}（有日线且非 stat/region 的 880/881 指数）。"""
    df = _read_parts("tdx_sector_def", columns=["code", "name", "seg"])
    daily = _read_parts("tdx_sector_daily", columns=["code"])
    if df.empty or daily.empty:
        return {}
    has_day = set(daily["code"].astype(str))
    df = df[~df["seg"].isin(EXCLUDE_SEG)]
    df = df[df["code"].astype(str).isin(has_day)]
    return dict(zip(df["code"].astype(str), df["name"]))


def load_index_close():
    """全部板块指数日线 → {code: close数组}（按日期升序）。"""
    df = _read_parts("tdx_sector_daily",
                     columns=["code", "trade_date", "close"])
    if df.empty:
        return {}, ""
    df = df.dropna(subset=["close"]).sort_values(["code", "trade_date"])
    out = {c: g["close"].to_numpy(dtype=float)
           for c, g in df.groupby("code")}
    latest = str(df["trade_date"].astype(str).max())
    return out, latest


def load_members():
    """成分映射 → {block_name: [con_code...]}（去重）。"""
    mem = _read_parts("tdx_sector_member", columns=["block_name", "con_code"])
    if mem.empty:
        return {}
    mem = mem.dropna(subset=["con_code"]).drop_duplicates(
        ["block_name", "con_code"])
    return {b: g["con_code"].astype(str).tolist()
            for b, g in mem.groupby("block_name")}


def load_stock_closes(codes):
    """批量读个股尾部收盘 → {6位代码: np.array}（读不到的自动缺）。"""
    out = {}
    for c in codes:
        hits = glob.glob(os.path.join(ASHARE, "%s_*.parquet" % c))
        if not hits:
            continue
        try:
            df = pd.read_parquet(hits[0], columns=["trade_date", "close"])
            df = df.dropna(subset=["close"]).sort_values("trade_date")
            if len(df):
                out[c] = df["close"].to_numpy(dtype=float)[-TAIL_BARS:]
        except Exception:
            continue
    return out


# ── 核心计算（纯函数，单测覆盖） ───────────────────────────
def calc_rps(chg50: pd.Series) -> pd.Series:
    """截面 50 日涨幅 → 百分位 RPS（0-100，越高越强）。

    NaN（历史不足）不参与排名、结果保持 NaN。
    """
    return (chg50.rank(pct=True) * 100).round(1)


def calc_cohesion(closes):
    """板块共性 → (结构相似比例, 多头排列比例)。

    closes: {code: close数组}。结构相似 = 收盘>MA20 且 MA20 相对 5 根前
    上行；多头排列 = MA5>MA10>MA20。有效样本 = 日线 ≥ 21 根的个股；
    有效样本为 0 → (None, None)。
    """
    similar, aligned, valid = 0, 0, 0
    for c in closes.values():
        a = np.asarray(c, dtype=float)
        if len(a) < 21:
            continue                     # 数据不足不计入分母
        valid += 1
        ma5 = a[-5:].mean()
        ma10 = a[-10:].mean()
        ma20 = a[-20:].mean()
        if len(a) >= 26:                 # 斜率需要 5 根前的 MA20
            ma20_prev = a[-25:-5].mean()
            slope_up = ma20 > ma20_prev
        else:
            slope_up = False
        if a[-1] > ma20 and slope_up:
            similar += 1
        if ma5 > ma10 > ma20:
            aligned += 1
    if valid == 0:
        return None, None
    return round(similar / valid, 3), round(aligned / valid, 3)


def calc_leaders(chg5: pd.Series, idx_chg5: float, topn=LEADER_TOPN):
    """龙头强度 → (leaders 列表, 前 N 超额均值, 是否强龙头)。

    chg5: 成分股 5 日涨幅（小数）；idx_chg5: 板块指数 5 日涨幅。
    超额 = 个股涨幅 − 板块涨幅；强龙头 = 前 N 平均涨幅 ≥ 板块涨幅
    × STRONG_RATIO 且板块上涨（板块下跌时超额无意义，判 False）。
    chg5 为空 → ([], None, None)。
    """
    s = chg5.dropna().sort_values(ascending=False)
    if s.empty:
        return [], None, None
    leaders = [{"code": c, "chg5": round(float(v) * 100, 2),
                "excess": round((float(v) - idx_chg5) * 100, 2)
                if idx_chg5 is not None else None}
               for c, v in s.head(topn).items()]
    top = s.head(topn)
    strength = round(float((top - idx_chg5).mean()) * 100, 2) \
        if idx_chg5 is not None else None
    strong = None
    if idx_chg5 is not None:
        strong = bool(idx_chg5 > 0 and top.mean() >= STRONG_RATIO * idx_chg5)
    return leaders, strength, strong


def _chg(close_arr, n):
    """N 日涨幅（小数）；历史不足或基期 ≤0 → None。"""
    a = np.asarray(close_arr, dtype=float)
    if len(a) < n + 1 or a[-(n + 1)] <= 0:
        return None
    return float(a[-1] / a[-(n + 1)] - 1)


# ── 板块级组装 ─────────────────────────────────────────────
def scan_one(name, idx_code, idx_closes, members):
    """单板块 → 指标 dict；任何一步失败返回带 error 的 dict（不抛出）。"""
    row = {"sector": name, "index_code": idx_code, "rps50": None,
           "cohesion": None, "ma_align": None,
           "leaders": [], "leader_strength": None, "leader_strong": None}
    try:
        closes = idx_closes.get(idx_code)
        idx_chg5 = _chg(closes, 5) if closes is not None else None
        codes = members.get(name, [])
        if len(codes) < MIN_MEMBERS:
            row["skip"] = "members<%d" % MIN_MEMBERS
            return row
        stock_closes = load_stock_closes(codes)
        row["n_members"] = len(stock_closes)
        row["cohesion"], row["ma_align"] = calc_cohesion(stock_closes)
        chg = pd.Series({c: _chg(a, 5) for c, a in stock_closes.items()})
        row["leaders"], row["leader_strength"], row["leader_strong"] = \
            calc_leaders(chg, idx_chg5)
    except Exception as ex:                  # 单板块失败不阻断全量
        row["error"] = str(ex)[:80]
    return row


def scan(top=None):
    """全板块扫描 → 写 reports/sector_effect.json（原子写）。

    流程: 板块全集 RPS50 截面 → 逐板块共性/龙头（成分映射命中的才算）
    → 按 rps50 降序（None 沉底）。
    """
    universe = load_universe()
    idx_closes, latest = load_index_close()
    if not universe or not idx_closes:
        print("SCAN no sector data")
        return None
    # 成分映射: block_name → def 名称同名即认（member 无行业 881 成分）
    members = load_members()

    chg50 = pd.Series({c: _chg(a, RPS_N)
                       for c, a in idx_closes.items() if c in universe})
    rps = calc_rps(chg50)

    sectors = []
    for code, name in universe.items():
        row = scan_one(name, code, idx_closes, members)
        v = rps.get(code)
        row["rps50"] = None if pd.isna(v) else float(v)
        sectors.append(row)
    sectors.sort(key=lambda r: (r["rps50"] is None,
                                -(r["rps50"] or 0)))
    snap = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "data_day": latest, "n_sectors": len(sectors),
            "n_with_members": sum(1 for r in sectors
                                  if r.get("n_members")),
            "sectors": sectors}
    os.makedirs(REPORTS, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=REPORTS, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, OUT_JSON)                # 原子替换，防半截文件
    print("SCAN %d sectors (%d with members) -> %s"
          % (len(sectors), snap["n_with_members"], OUT_JSON))

    if top:
        for i, r in enumerate(sectors[:top], 1):
            ld = " ".join("%s%+.1f%%" % (l["code"], l["chg5"])
                          for l in (r.get("leaders") or []))
            print("%2d %-8s %s rps50=%s cohesion=%s align=%s 龙头[%s]"
                  " 强度=%s" % (
                      i, r["sector"], r["index_code"],
                      ("%0.f" % r["rps50"]) if r["rps50"] is not None
                      else "—",
                      ("%d%%" % round(r["cohesion"] * 100))
                      if r["cohesion"] is not None else "—",
                      ("%d%%" % round(r["ma_align"] * 100))
                      if r["ma_align"] is not None else "—",
                      ld or "—", r["leader_strength"]))
    return snap


# ── CLI ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="板块效应层（影子模式）")
    ap.add_argument("--scan", action="store_true", help="全板块扫描")
    ap.add_argument("--sector", help="单板块调试（打印明细）")
    ap.add_argument("--top", type=int, default=0,
                    help="配合 --scan 打印前 N 摘要")
    a = ap.parse_args()
    if a.scan:
        scan(top=a.top)
        return 0
    if a.sector:
        universe = load_universe()
        idx_closes, latest = load_index_close()
        name2code = {v: k for k, v in universe.items()}
        members = load_members()
        code = name2code.get(a.sector)
        if code is None:
            print("板块「%s」不在指数全集（共 %d 个）"
                  % (a.sector, len(universe)))
            return 1
        row = scan_one(a.sector, code, idx_closes, members)
        chg50 = pd.Series({c: _chg(x, RPS_N)
                           for c, x in idx_closes.items() if c in universe})
        v = calc_rps(chg50).get(code)
        row["rps50"] = None if pd.isna(v) else float(v)
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
