# -*- coding: utf-8 -*-
"""缠论 106 课板块强弱指标 v0 · 9 类均线攻克度（影子，2026-11-10 老田批准）。

106 课原文口径：全市场个股按「本次行情迄今未攻克的最小周期均线」分 9 类
——完全在所有均线下 = 最差第 1 类；站上 5 日线但在 13 日线下 = 第 2 类；
……全部攻克（站上 233 日线）= 第 9 类。**板块强度 = 板块内个股类别数
平均值**。比单纯 RPS / 5 日涨幅更具结构含义：它量的是「板块里有多少
比例的票站在多头结构上」，不是「涨得多快」。

本脚本与现有 sector_state 的 5 日涨幅（r5）/RPS 口径**并行影子**、
不互相推翻（本仓惯例）：产出 reports/sector106_shadow_<asof>.json，
附两口径排名对比，样本面板择优后再考虑替换。

均线参数（106 课）：5/13/21/34/55/89/144/233 斐波那契系。

类别判定（asof 当日）：类别 = 已攻克均线数 + 1；close >= MA(k) 视为
攻克 k。数据不足 233 根的票只按已能算的均线判（如实降级，n_lt 变量）。
成员缺失/停牌/ST 不计均值，板块记 n_covered / n_members。

CLI:
  python sector_beichi106.py --save          # 算+落盘+对比
  python sector_beichi106.py --asof 20261109 # 指定数据日
用法在 pool_daily 第 8 步 soft 接入（失败不打断结构流水线）。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASHARE = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")
NAMES_FP = os.path.join(HERE, "data", "stock_names.json")

MAS = (5, 13, 21, 34, 55, 89, 144, 233)  # 106 课斐波那契系
MIN_MEMBERS = 8  # 与 sector_state 同门槛

sys.path.insert(0, HERE)


def _log(msg):
    print(msg, flush=True)


def load_names():
    try:
        with open(NAMES_FP, encoding="utf-8") as f:
            j = json.load(f)
        return {str(k).upper(): str(v) for k, v in (j.get("code2name") or {}).items()}
    except Exception:
        return {}


def stock_cls_all(asof=None):
    """全市场个股 106 课类别 → {code: (cls, close, n_bars)}。

    逐文件读 close（只 2 列）；asof=None 取每股自身最新一日（结构层
    与日线新鲜度门控解耦——板块指数落后一天时个股仍按自身最新算，
    落盘时统一记 asof=max）。
    """
    names = load_names()
    out = {}
    fs = sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
    t0 = time.time()
    n_probe = 0
    for idx, fp in enumerate(fs):
        code = os.path.basename(fp)[:-8].replace("_", ".")
        if code.endswith(".BJ") or names.get(code, "").upper().find("ST") >= 0:
            continue
        if code.endswith(".SH") and code[:3] in ("000", "880", "999"):
            continue  # 指数
        if code.startswith("399"):
            continue
        try:
            df = pd.read_parquet(fp, columns=["trade_date", "close"])
        except Exception:
            continue
        if asof is not None:
            df = df[df["trade_date"].astype(str) <= str(asof)]
        if df.empty:
            continue
        df = df.sort_values("trade_date")
        close = float(df["close"].iloc[-1])
        n_bars = len(df)
        tail = df["close"].tail(233).to_numpy(float)
        # 类别 = 已攻克均线数 + 1（短→长依序检查）
        cls = 1
        for k in MAS:
            if len(tail) >= k:
                ma = float(np.mean(tail[-k:]))
                if close >= ma:
                    cls += 1
            else:
                break  # 数据不足，更长的均线都算不了
        out[code] = (cls, close, n_bars)
        n_probe += 1
        if n_probe % 1000 == 0:
            _log("[scan] %d/%d 只（%.0fs）" % (n_probe, len(fs), time.time() - t0))
    _log("[scan] 完成 %d 只（%.1fs）" % (n_probe, time.time() - t0))
    return out


def build(asof=None, level="L1N", save=False):
    """板块 106 强度 + 与现有 r5 口径的影子对比。"""
    import sector_state as ss

    cls_map = stock_cls_all(asof)
    if not cls_map:
        raise SystemExit("[fail] 个股扫描为空")
    segs = ss.segs_of(level)
    sectors = ss.load_sectors()
    members = ss.load_members()

    rows = []
    for code, sd in sectors.items():
        if sd["seg"] not in segs or sd["type"] not in ("I", "N"):
            continue
        mem = members.get(code) or []
        if len(mem) < MIN_MEMBERS:
            continue
        nm = str(sd["name"])
        if any(w in nm for w in ss.DROP_WORDS):
            continue
        vals = [cls_map[m][0] for m in mem if m in cls_map]
        if len(vals) < MIN_MEMBERS:
            continue
        rows.append({
            "code": code,
            "name": nm,
            "seg": sd["seg"],
            "n_members": len(mem),
            "n_covered": len(vals),
            "cls106": round(sum(vals) / len(vals), 3),      # 106 课强度
            "pct_full": round(sum(1 for v in vals if v == 9) / len(vals), 4),
            "pct_bottom": round(sum(1 for v in vals if v == 1) / len(vals), 4),
        })
    rows.sort(key=lambda r: -r["cls106"])
    for i, r in enumerate(rows, 1):
        r["rank106"] = i

    # 影子对比：与 sector_state_<asof>.json 的 r5 排名
    cmp_rows, cmp_note = [], None
    ss_fp = None
    if asof:
        cand = os.path.join(REPORTS, "sector_state_%s.json" % asof)
        ss_fp = cand if os.path.exists(cand) else None
    if ss_fp is None:
        fs = sorted(glob.glob(os.path.join(REPORTS, "sector_state_*.json")))
        ss_fp = fs[-1] if fs else None
    if ss_fp:
        try:
            with open(ss_fp, encoding="utf-8") as f:
                ssd = json.load(f)
            r5map = {}
            for r in ssd.get("sectors") or []:
                if r.get("r5") is not None:
                    r5map[r["code"]] = (r.get("r5"), r.get("name"))
            order = sorted(r5map, key=lambda c: -(r5map[c][0] or -9))
            rank5 = {c: i + 1 for i, c in enumerate(order)}
            for r in rows:
                if r["code"] in rank5:
                    r5, nm5 = r5map[r["code"]]
                    r["r5"] = round(r5, 4) if r5 is not None else None
                    r["rank5"] = rank5[r["code"]]
                    r["rank_diff"] = r["rank5"] - r["rank106"]  # 正=106 更靠前
                    cmp_rows.append(r)
            cmp_note = "对比基准 %s（%d 板块）" % (os.path.basename(ss_fp), len(cmp_rows))
        except Exception as e:
            cmp_note = "对比失败: %s" % str(e)[:80]
    else:
        cmp_note = "无 sector_state 落盘可比"

    # 个股 asof：有 sector_state 落盘就取它的 asof（个股层与板块层同日可比）
    out = {
        "asof": (asof or (json.load(open(ss_fp, encoding="utf-8"))["asof"] if ss_fp else None)),
        "level": level,
        "mas": list(MAS),
        "n_sectors": len(rows),
        "n_stocks": len(cls_map),
        "note": "106课 9 类均线攻克度均值，影子口径，与 r5/RPS 并行不互斥",
        "cmp_note": cmp_note,
        "sectors": rows,
    }
    if save:
        fp = os.path.join(REPORTS, "sector106_shadow_%s.json" % (out["asof"] or "latest"))
        json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _log("[save] %s" % fp)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="106 课板块强弱 v0（影子）")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--asof", default=None, help="个股数据日 YYYYMMDD（默认各自最新）")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    out = build(asof=a.asof, save=a.save)
    _log("板块数 %d · 个股 %d · %s" % (out["n_sectors"], out["n_stocks"], out["cmp_note"]))
    _log("── 106 课口径头部（cls106 高=多头结构占比高）──")
    for r in out["sectors"][: a.top]:
        _log("  #%-3d %s %s cls106=%.2f 全攻克%.0f%% 最差%.0f%% r5=%s(rank%s)"
             % (r["rank106"], r["code"], r["name"], r["cls106"],
                r["pct_full"] * 100, r["pct_bottom"] * 100,
                r.get("r5"), r.get("rank5")))
    _log("── 尾部 ──")
    for r in out["sectors"][-5:]:
        _log("  #%-3d %s %s cls106=%.2f" % (r["rank106"], r["code"], r["name"], r["cls106"]))
    # 两口径分歧最大的板块（|rank_diff| 大 = 结构与动量背离，值得人看）
    diver = [r for r in out["sectors"] if r.get("rank_diff") is not None]
    diver.sort(key=lambda r: -abs(r["rank_diff"]))
    if diver:
        _log("── 两口径分歧 top5（正=106 更靠前：涨得慢但结构强）──")
        for r in diver[:5]:
            _log("  %s %s rank106=%s rank5=%s diff=%+d"
                 % (r["code"], r["name"], r["rank106"], r["rank5"], r["rank_diff"]))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
