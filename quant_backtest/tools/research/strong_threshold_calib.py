# -*- coding: utf-8 -*-
"""strong 250 日超额阈值周度自校准 · 离线研究（W5b / W1-3 挂账项）。

背景（判例）：strong 产线名单 = 250 日超额 ≥+STRONG_EX_TH(10pp) × 板块
成员 ≥MIN_BLOCK_MEMBERS(5) × 日线多头（strong_track.strong_verdict，
与 /bt-linkage 同源）。±10pp 是拍的常数——高波期普涨 +20pp 满地走，
名单厚到 150+ 只推送不可读（2026-10-09~13 实测 63→154 只漂移）；
低波期 +10pp 都算显著，名单薄到没法分层。本脚本只读历史湖离线复算，
回答一个问题：阈值改成「近 60 交易日全市场 250 日超额分布的 P85/P90」
（周度自校准），厚度稳定性与名单质量是否显著优于固定 ±10pp。

口径（与产线逐条对齐，差异显式声明）：
  · 板块池: strong_scan.POOL_SEGS（industry881/industry_l1/old_industry）
  · 成员归组: ts_stock_basic.industry → linkage.resolve_industry_block
    （别名表 + SEG_PRI 精确/包含匹配，与 block_members 同函数同口径）
  · ex250: 个股近 250 根日线涨幅 − 板块近 250 根日线涨幅（pp），即
    linkage.series 归一化 pct[-1] 差值的等价展开；偏差声明——产线个股
    K 线走 load_kline 自身近 250 根 bar，本脚本用湖宽表按交易日历
    shift(249)，停牌股口径略有出入（研究可接受，落盘注明）
  · 趋势三线: close > ma20 > ma60 且自身样本 ≥60 根（trend_bull 等价
    向量化——仿射变换不改变序关系，与 strong_track.trend_bull 同判定）
  · 次新闸: 自身累计 K 线 <250 根时「板块领涨」降级「逆板块强势」
    （strong_scan S2 修复 077e 同规则）
  · 成员快照: ts_stock_basic 当期快照回溯两年（行业归属无历史版本，
    存量近似，判例：W1-2 --full 全量评估同为当期清单口径）

自校准规则（研究假设，产线化方案另立文档）：
  周末点 t 的阈值 th(t) = P85 / P90（两档都算）of 近 60 交易日全体
  池内成员股的 ex250 分布；窗口非空率 <60% 时回退固定 +10pp（数据
  不足不硬校准，判例：freshness_guard 缺数据拦截哲学）。

判定标准（W5b 任务书给定，两条同时满足才建议产线化）：
  A. 厚度稳定性: 自适应版「领涨个股数∈[TARGET_LO,TARGET_HI] 目标带」
     的周命中率 比固定版高 ≥ MIN_GAIN_PP
  B. 名单调优: 自适应名单 T+20 板块相对超额均值 不差于 固定版
     （≥ 固定版均值，同周同口径配对）

纪律：只读历史（data/ashare + data/tdx + ts_stock_basic），不碰
strong_state/strong_latest 等产线状态文件；不推飞书；不注册计划任务。
长跑日志落 /c/Temp/strong_calib.log；产物 reports/strong_threshold_calib_*
（原子写 tmp+replace，判例：strong_latest.json 落盘模式）。

用法:
  "C:/Program Files/Python311/python.exe" strong_threshold_calib.py
  "C:/Program Files/Python311/python.exe" strong_threshold_calib.py --years 1   # 缩窗快跑
"""

import argparse
import datetime as _dt
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from strong_scan import POOL_SEGS  # noqa: E402
from strong_track import MIN_BLOCK_MEMBERS, STRONG_EX_TH  # noqa: E402
import logging

# ---- 研究参数（集中定义，产线化时对应 config 开关，见留痕方案节） ----
TARGET_LO, TARGET_HI = 5, 20  # 名单厚度目标带：领涨个股数 5~20（推送可读性）
MIN_GAIN_PP = 20.0  # 判定 A 门槛：目标带命中率提升 ≥20pp
WIN_DAYS = 60  # 自适应阈值回看窗：近 60 交易日 ex 分布
Q_P85, Q_P90 = 0.85, 0.90  # 自适应两档分位（都算，对照择优）
WIN_MIN_COVER = 0.60  # 窗口非空率 <60% 回退固定阈值（不硬校准）
T_FWD = 20  # 名单调优前视窗：T+20 交易日（下月口径）
REPLAY_YEARS = 2.0  # 复算回溯年数（默认近 2 年）
RET_LB = 250  # 超额回看长度（根）——250 日超额
TREND_MIN_BARS = 60  # trend_bull 最小样本（strong_track 硬编码 60）
NEW_LIST_CAP = 250  # 次新闸：自身 K 线 <250 根降级观察（S2 077e）

ASHARE = os.path.join(HERE, "data", "ashare")
TDX = os.path.join(HERE, "data", "tdx")
REPORTS = os.path.join(HERE, "reports")
LOG_FP = (
    "C:/Temp/strong_calib.log"
    if os.path.isdir("C:/Temp")
    else os.path.join(os.environ.get("TEMP", "/tmp"), "strong_calib.log")  # nosec B108 - 固定日志文件名
)
# 注意：Windows 原生 Python 不认 Git Bash 的 /c/Temp 写法，须用 C:/Temp

_LOG_FH = None


def log(msg):
    """stdout + 日志双写（长跑可续跟，判例：_325b_backfill.log 模式）。"""
    global _LOG_FH
    line = "[%s] %s" % (_dt.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        if _LOG_FH is None:
            _LOG_FH = open(LOG_FP, "a", encoding="utf-8")
        _LOG_FH.write(line + "\n")
        _LOG_FH.flush()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/research/strong_threshold_calib.py", exc)


def atomic_dump(obj, fp):
    """原子写 JSON（tmp + os.replace，判例：产线状态文件落盘模式）。"""
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, fp)


# ---------------------------------------------------------------- 成员归组
def load_pool():
    """板块池 + 成员归组（与 linkage.block_members 同口径，一次成表）。

    返回 (pool, member_of)：
      pool = {block_code: block_name}（POOL_SEGS 三段全池）
      member_of = {block_code: [ts_code,…]}（当期快照，每股唯一归属）
    """
    import linkage

    defs = linkage.sector_defs()
    pool = {bc: d["name"] for bc, d in sorted(defs.items()) if d["seg"] in POOL_SEGS}
    frames = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_stock_basic", "part_*.parquet"))):
        frames.append(pd.read_parquet(p, columns=["ts_code", "name", "industry"]))
    sb = pd.concat(frames, ignore_index=True)
    log("ts_stock_basic %d 行（含退市，镜像产线不过滤 list_status）" % len(sb))
    member_of = {bc: [] for bc in pool}
    n_unresolved = 0
    for _, r in sb.iterrows():
        ind = str(r["industry"] or "") or None
        bc = linkage.resolve_industry_block(ind) if ind else None
        if bc and bc in member_of:
            member_of[bc].append(str(r["ts_code"]))
        else:
            n_unresolved += 1
    log(
        "板块池 %d 个（%s），池内成员 %d 只，industry 未落池 %d 只"
        % (
            len(pool),
            "/".join(POOL_SEGS),
            sum(len(v) for v in member_of.values()),
            n_unresolved,
        )
    )
    return pool, member_of


# ---------------------------------------------------------------- K 线宽表
def load_stock_close(codes):
    """湖 data/ashare → {ts_code: Series(date_str8 → close)}（只读）。"""
    out = {}
    miss = 0
    for c in codes:
        fp = os.path.join(ASHARE, c.replace(".", "_") + ".parquet")
        if not os.path.exists(fp):
            miss += 1
            continue
        df = pd.read_parquet(fp, columns=["trade_date", "close"])
        s = pd.Series(
            df["close"].astype(float).values, index=df["trade_date"].astype(str)
        )
        out[c] = s[s.notna()]
    log("个股湖取数 %d/%d（缺湖文件 %d）" % (len(out), len(codes), miss))
    return out


def load_block_close(pool):
    """tdx_sector_daily → {block_code: Series(date_str8 → close)}。"""
    frames = []
    for p in sorted(glob.glob(os.path.join(TDX, "tdx_sector_daily", "part_*.parquet"))):
        frames.append(pd.read_parquet(p, columns=["code", "trade_date", "close"]))
    df = pd.concat(frames, ignore_index=True)
    df = df[df["code"].isin(pool)]
    out = {}
    for bc, g in df.groupby("code"):
        g = g.sort_values("trade_date")
        out[bc] = pd.Series(
            g["close"].astype(float).values, index=g["trade_date"].astype(str)
        )
    log("板块日线仓取数 %d/%d 个板块" % (len(out), len(pool)))
    return out


# ---------------------------------------------------------------- 主流程
def build_panels(stock_close, block_close, member_of, calendar):
    """一次性向量化算全部研究面板（宽表：行=交易日，列=ts_code/板块）。

    返回 dict of DataFrame：
      ex        ex250 面板（pp）——自适应阈值的分布源 + 判定输入
      bull      日线多头面板（bool；样本<60 根为 False，同 trend_bull）
      nbars     自身累计 K 线根数面板（次新闸输入）
      fwd_rel   T+20 板块相对超额面板（pp）——名单调优判定输入
      blk_n     {block: 成员数}（quorum 门槛输入，当期快照）
    """
    stk = pd.DataFrame({c: s for c, s in stock_close.items()}).reindex(calendar)
    blk = pd.DataFrame({c: s for c, s in block_close.items()}).reindex(calendar)
    # 股票 → 板块归属列（向量广播用）
    blk_of = {}
    for bc, codes in member_of.items():
        for c in codes:
            if c in stk.columns:
                blk_of[c] = bc
    blk_cols = pd.Series({c: blk_of.get(c) for c in stk.columns})
    blk_mat = blk.reindex(columns=blk_cols.values)
    blk_mat.columns = stk.columns
    blk_mat.index = blk.index

    # ex250 = 个股近 250 根涨幅 − 板块近 250 根涨幅（pp）；口径偏差见
    # 文件头（宽表日历 shift vs 产线自身 bar，停牌股略有出入）
    s_ret = stk / stk.shift(RET_LB) - 1.0
    b_ret = blk / blk.shift(RET_LB) - 1.0
    b_ret_al = b_ret.reindex(columns=blk_cols.values)
    b_ret_al.columns = stk.columns
    b_ret_al.index = stk.index
    ex = (s_ret - b_ret_al) * 100.0

    # 日线多头：close > ma20 > ma60，且自身累计根数 ≥60（<60 不可判 False）
    ma20 = stk.rolling(20, min_periods=20).mean()
    ma60 = stk.rolling(60, min_periods=60).mean()
    nbars = stk.notna().cumsum()
    bull = (stk > ma20) & (ma20 > ma60) & (nbars >= TREND_MIN_BARS)

    # T+20 板块相对超额（pp）：个股 20 日涨幅 − 所属板块 20 日涨幅
    s_f = stk.shift(-T_FWD) / stk - 1.0
    b_f = blk.shift(-T_FWD) / blk - 1.0
    b_f_al = b_f.reindex(columns=blk_cols.values)
    b_f_al.columns = stk.columns
    b_f_al.index = stk.index
    fwd_rel = (s_f - b_f_al) * 100.0

    blk_n = {
        bc: len([c for c in codes if c in stk.columns])
        for bc, codes in member_of.items()
    }
    log(
        "面板构建完成：%d 日 × %d 股 / %d 板块"
        % (len(calendar), stk.shape[1], blk.shape[1])
    )
    return {
        "ex": ex,
        "bull": bull,
        "nbars": nbars,
        "fwd_rel": fwd_rel,
        "stk": stk,
        "blk": blk,
        "blk_of": blk_of,
        "blk_n": blk_n,
    }


def weekly_points(calendar, years):
    """周末点 = 日历中每个 ISO 周的最后一个交易日，取近 years 年。"""
    s = pd.Series(pd.to_datetime(calendar, format="%Y%m%d"), index=calendar)
    iso = s.dt.isocalendar()
    key = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    pts = s.groupby(key).max().sort_values()
    lo = pts.max() - pd.Timedelta(days=365.25 * years)
    return [d.strftime("%Y%m%d") for d in pts[pts >= lo]]


def adaptive_thresholds(ex, pts, q):
    """各周末点的自适应阈值（q 分位 of 近 60 交易日池内 ex 分布）。

    回退规则：窗口非空率 <WIN_MIN_COVER 时该周回退固定 +10pp（阈值
    面板记 fallback，判例：数据不足不硬校准）。
    """
    th = {}
    pos = {d: i for i, d in enumerate(ex.index)}
    for d in pts:
        i = pos[d]
        lo = max(0, i - WIN_DAYS + 1)
        win = ex.iloc[lo : i + 1].values
        vals = win[~np.isnan(win)]
        if len(vals) < WIN_DAYS * ex.shape[1] * WIN_MIN_COVER:
            th[d] = float(STRONG_EX_TH)
            continue
        th[d] = float(np.nanquantile(vals, q))
    return th


def verdict_at(ex_row, bull_row, nbars_row, blk_n, blk_of, th):
    """单周末点判定（strong_verdict 判定链向量化）。

    返回 (lead_codes, observe_codes, blocks_hit)：
      达标 = ex≥th 且板块成员 ≥MIN_BLOCK_MEMBERS；多头→「板块领涨」，
      非多头→「逆板块强势」；次新（自身 <250 根）领涨降级观察。
    """
    ok = ex_row.notna() & (ex_row >= th)
    lead, obs, blocks = [], [], set()
    for c in ex_row.index[ok]:
        bc = blk_of.get(c)
        if bc is None or blk_n.get(bc, 0) < MIN_BLOCK_MEMBERS:
            continue
        blocks.add(bc)
        if bull_row.get(c, False):
            if nbars_row.get(c, 0) < NEW_LIST_CAP:
                obs.append(c)  # 次新闸：降级观察（S2 077e）
            else:
                lead.append(c)
        else:
            obs.append(c)
    return lead, obs, blocks


def rel20_at(fwd_rel_row, codes):
    """名单 T+20 板块相对超额（pp，None=前视数据不足）。"""
    return [
        None if pd.isna(fwd_rel_row.get(c)) else round(float(fwd_rel_row.get(c)), 2)
        for c in codes
    ]


def main():
    ap = argparse.ArgumentParser(description="strong 阈值自校准离线研究")
    ap.add_argument(
        "--years",
        type=float,
        default=REPLAY_YEARS,
        help="复算回溯年数（默认 %.0f）" % REPLAY_YEARS,
    )
    a = ap.parse_args()
    t0 = time.time()
    log("=== strong_threshold_calib 启动（回溯 %.1f 年，只读历史）===" % a.years)

    pool, member_of = load_pool()
    pool_codes = sorted({c for cs in member_of.values() for c in cs})
    stock_close = load_stock_close(pool_codes)
    block_close = load_block_close(pool)

    # 交易日历 = 个股湖 ∩ 板块仓（两侧都有数据的日，ex/趋势/前视同轴）
    cal = sorted(set(stock_close[pool_codes[0]].index if pool_codes else []))
    for s in stock_close.values():
        cal = sorted(set(cal) | set(s.index))
    cal = sorted(set(cal) & set(next(iter(block_close.values())).index))
    log("交易日历 %s ~ %s（%d 日，个股湖∩板块仓）" % (cal[0], cal[-1], len(cal)))

    panels = build_panels(stock_close, block_close, member_of, cal)
    pts = weekly_points(cal, a.years)
    log("周末点 %d 个（%s ~ %s）" % (len(pts), pts[0], pts[-1]))

    th = {
        q: adaptive_thresholds(panels["ex"], pts, q)
        for q in (Q_P85, Q_P90, 0.95, 0.97, 0.99)
    }

    # ---- 逐周判定：固定 ±10pp vs 自适应分位网格（同一判定链只换阈值）----
    # P85/P90 是任务书指定两档；P95~P99 为证据附录（回答「更高分位
    # 能否压进目标带」，供留痕对照表）
    variants = {"fixed": {d: float(STRONG_EX_TH) for d in pts}}
    for q, tag in (
        (Q_P85, "p85"),
        (Q_P90, "p90"),
        (0.95, "p95"),
        (0.97, "p97"),
        (0.99, "p99"),
    ):
        variants[tag] = th[q]
    rows = []
    t20_db = {vn: [] for vn in variants}
    pos = {d: i for i, d in enumerate(cal)}
    for d in pts:
        i = pos[d]
        ex_r = panels["ex"].iloc[i]
        bull_r = panels["bull"].iloc[i]
        nb_r = panels["nbars"].iloc[i]
        fr_r = panels["fwd_rel"].iloc[i]
        rec = {"date": d}
        for vn, thmap in variants.items():
            lead, obs, blocks = verdict_at(
                ex_r, bull_r, nb_r, panels["blk_n"], panels["blk_of"], thmap[d]
            )
            rels = rel20_at(fr_r, lead)
            rec["%s_th" % vn] = round(thmap[d], 2)
            rec["%s_lead" % vn] = len(lead)
            rec["%s_obs" % vn] = len(obs)
            rec["%s_blocks" % vn] = len(blocks)
            rec["%s_in_band" % vn] = int(TARGET_LO <= len(lead) <= TARGET_HI)
            rec["%s_in_band_blk" % vn] = int(TARGET_LO <= len(blocks) <= TARGET_HI)
            for c, r in zip(lead, rels):
                if r is not None:
                    t20_db[vn].append(r)
            # 名单留痕只记领涨（推送口径），观察层不进 T+20 评估
        rows.append(rec)
        if (pts.index(d) + 1) % 20 == 0:
            log("进度 %d/%d（%s）" % (pts.index(d) + 1, len(pts), d))

    df = pd.DataFrame(rows)
    # ---- 判定 A：厚度稳定性（目标带命中率，pp 差；显式转 Python 标量，
    # np.bool_/np.float64 不可 JSON 序列化，10-14 实测）----
    hit = {vn: float(df["%s_in_band" % vn].mean()) * 100 for vn in variants}
    hit_blk = {vn: float(df["%s_in_band_blk" % vn].mean()) * 100 for vn in variants}
    gain = {vn: float(hit[vn] - hit["fixed"]) for vn in variants if vn != "fixed"}
    # ---- 判定 B：名单调优（T+20 板块相对超额均值，自适应不差于固定） ----
    t20 = {vn: (float(np.mean(v)) if v else None) for vn, v in t20_db.items()}
    {vn: (float(np.median(v)) if v else None) for vn, v in t20_db.items()}
    ok_a = {vn: bool(gain[vn] >= MIN_GAIN_PP) for vn in gain}
    ok_b = {
        vn: bool(
            t20[vn] is not None and t20["fixed"] is not None and t20[vn] >= t20["fixed"]
        )
        for vn in gain
    }
    best = None
    for vn in ("p85", "p90"):  # 任务书判定档：P85/P90
        if ok_a[vn] and ok_b[vn]:
            best = vn
    # ---- 厚度漂移证据（固定版月度均值，供留痕） ----
    df["month"] = df["date"].str[:6]
    drift = {
        k: float(v)
        for k, v in df.groupby("month")["fixed_lead"].mean().round(1).items()
    }

    grid = {}  # 分位网格对照表（P95~P99 为证据附录，不参与任务书判定）
    for vn in variants:
        grid[vn] = {
            "th_median_pp": round(float(df["%s_th" % vn].median()), 1),
            "lead_median": float(df["%s_lead" % vn].median()),
            "lead_p90": float(df["%s_lead" % vn].quantile(0.9)),
            "blocks_median": float(df["%s_blocks" % vn].median()),
            "hitrate_band_pct": round(hit[vn], 1),
            "hitrate_band_blocks_pct": round(hit_blk[vn], 1),
            "t20_rel_mean_pp": (round(t20[vn], 2) if t20[vn] is not None else None),
        }

    summary = {
        "meta": {
            "run_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "years": a.years,
            "weeks": len(pts),
            "span": [pts[0], pts[-1]],
            "caliber": {
                "th_fixed_pp": STRONG_EX_TH,
                "min_members": MIN_BLOCK_MEMBERS,
                "win_days": WIN_DAYS,
                "q_grid": [Q_P85, Q_P90, 0.95, 0.97, 0.99],
                "target_band": [TARGET_LO, TARGET_HI],
                "t_fwd": T_FWD,
            },
            "disclaimers": [
                "成员快照=ts_stock_basic 当期（行业归属无历史版本）",
                "ex250 用湖宽表日历 shift(250)，停牌股与产线自身 bar 口径略差",
                "对照基准=--full 全量评估语义；历史推送名单为前 20 截断口径",
            ],
        },
        "fixed_drift_monthly_lead_mean": drift,
        "grid": grid,
        "verdict": {
            "A_thickness_gain_ge_%gpp" % MIN_GAIN_PP: ok_a,
            "B_t20_not_worse": ok_b,
            "recommend": ("weekly-adaptive-" + best)
            if best
            else "keep-fixed-threshold",
        },
    }

    os.makedirs(REPORTS, exist_ok=True)
    csv_fp = os.path.join(REPORTS, "strong_threshold_calib_weekly.csv")
    tmp = csv_fp + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, csv_fp)
    js_fp = os.path.join(REPORTS, "strong_threshold_calib_result.json")
    atomic_dump(summary, js_fp)

    log("厚度月均（固定+10pp 领涨数）: %s" % drift)
    log("分位网格对照（阈值中位pp | 领涨中位 | 带命中率%% | T+20均值pp）:")
    for vn, g in grid.items():
        log(
            "  %-5s th=%6.1f lead=%7.1f band=%5.1f%% blk_band=%5.1f%% t20=%s"
            % (
                vn,
                g["th_median_pp"],
                g["lead_median"],
                g["hitrate_band_pct"],
                g["hitrate_band_blocks_pct"],
                g["t20_rel_mean_pp"],
            )
        )
    log(
        "判定: A(≥%gpp)=%s B(不差)=%s → 建议=%s"
        % (MIN_GAIN_PP, ok_a, ok_b, summary["verdict"]["recommend"])
    )
    log("落盘 %s + %s（耗时 %.0fs）" % (csv_fp, js_fp, time.time() - t0))
    if _LOG_FH:
        _LOG_FH.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
