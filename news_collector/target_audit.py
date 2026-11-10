# -*- coding: utf-8 -*-
"""标的驱动采集 v1：库内覆盖对账（纯只读）。

队列（老田 2026-11-09 批）：
  ① 缠论 actionable 65（target_queue_chanlun.json: watch/observe 且 new/running）
  ② todays_picks n_hit>=2（多策略共振，排除①已含）
  ③ K-STOCK 模拟仓现持仓（paper.db positions，常驻）

对账三档（老田口径「能实时更新的不再采」）：
  fresh  高频表 max<=7 天 / 事件表 max<=90 天
  stale  有数据但超出新鲜窗
  absent 该标的该维度库内无数据

产出：target_audit_result.json（矩阵）+ reports/target_audit_v1_*.md（缺口热力图）
"""
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TDX = r"C:/aios/quant_backtest/data/tdx"
REPORTS = r"C:/项目/老田的AI助手/reports"

TODAY = date(2026, 11, 9)
FRESH_HI = (TODAY - timedelta(days=7)).strftime("%Y%m%d")     # 高频表新鲜线
FRESH_EVT = (TODAY - timedelta(days=90)).strftime("%Y%m%d")   # 事件表新鲜线

# 维度表清单： (表名, 中文名, 日期列, 类型)  type: hi=高频 evt=事件
# 2026-11-09 语义修正：evt 表 absent=「无事件」非缺口（没回购的公司在
# ts_repurchase 本来就无行）——处置单把「管道缺口」与「无事件」分开，
# 回填只保证事件发生即入库（六表 2026-11-09 已追平远端）。
DIMS = [
    ("ts_daily",              "行情日线",     "trade_date", "hi"),
    ("ts_daily_basic",        "每日指标",     "trade_date", "hi"),
    ("ts_moneyflow",          "资金流",       "trade_date", "hi"),
    ("ts_stk_factor",         "技术因子",     "trade_date", "hi"),
    ("ts_top_list",           "龙虎榜",       "trade_date", "evt"),
    ("ts_top_inst",           "龙虎榜机构",   "trade_date", "evt"),
    ("ts_stk_holdernumber",   "股东户数",     "ann_date",   "evt"),
    ("ts_stk_holdertrade",    "股东增减持",   "ann_date",   "evt"),
    ("ts_top10_floatholders", "十大流通股东", "ann_date",   "evt"),
    ("ts_share_float",        "限售解禁",     "float_date", "evt"),
    ("ts_repurchase",         "回购",         "ann_date",   "evt"),
    ("ts_pledge_stat",        "股权质押",     "end_date",   "evt"),
    ("ts_margin_detail",      "融资融券",     "trade_date", "hi"),
    ("ts_forecast",           "业绩预告",     "ann_date",   "evt"),
    ("ts_dividend",           "分红",         "end_date",   "evt"),
    ("ts_anns_d",             "公告文本",     "ann_date",   "evt"),
    ("ts_stk_surv",           "机构调研",     "rep_date",   "evt"),
    ("ts_limit_list_ths",     "涨跌停(THS)",  "trade_date", "evt"),
    ("ts_kpl_concept_cons",   "开盘啦题材",   "trade_date", "evt"),
    ("ts_disclosure_date",    "财报日历",     "end_date",   "evt"),
]
NEWS_SRC_GROUPS = {          # news.db 舆情维度: (展示名, source 集合)
    "新闻舆情": None,        # None = 全源（title 匹配）
    "公告流": {"announcement", "xd_notice"},
    "研报流": {"report", "ts_broker_recommend"},
}


def build_queue():
    q = json.load(open(os.path.join(HERE, "target_queue_chanlun.json"),
                       encoding="utf-8"))
    seen, queue = set(), []
    for it in q["actionable"]:
        seen.add(it["code"])
        queue.append({**it, "origin": "chanlun_actionable"})
    # ② todays_picks n_hit>=2
    tp = json.load(open(r"C:/项目/老田的AI助手/portal/data/todays_picks.json",
                        encoding="utf-8"))
    for s in tp.get("stocks") or []:
        if (s.get("n_hit") or 0) >= 2 and s["code"] not in seen:
            seen.add(s["code"])
            queue.append({"code": s["code"], "name": s.get("name", ""),
                          "grade": "multi_hit", "type": "n_hit=%s" % s["n_hit"],
                          "stage": "-", "origin": "picks_nhit2"})
    # ③ 模拟仓现持仓
    db = sqlite3.connect(r"file:C:/项目/老田的AI助手/k_stock/data/paper.db?mode=ro",
                         uri=True)
    rows = db.execute("SELECT stock, strategy FROM positions WHERE qty > 0").fetchall()
    db.close()
    for code, strat in rows:
        if code not in seen:
            seen.add(code)
            queue.append({"code": code, "name": "", "grade": "holding",
                          "type": strat, "stage": "-", "origin": "kstock_position"})
        else:
            for it in queue:
                if it["code"] == code:
                    it["origin"] += "+holding"
    return queue


def audit_tdx_dim(table, dcol, kind, codes):
    """全表读 ts_code+日期两列，返回 {code: max_date_str}。表缺列/缺文件返回 None。"""
    parts = sorted(glob.glob(os.path.join(TDX, table, "part_*.parquet")))
    if not parts:
        return None
    best = {}
    for p in parts:
        try:
            df = pd.read_parquet(p, columns=["ts_code", dcol])
        except Exception:
            try:
                df = pd.read_parquet(p)          # 兜底：全读
                df = df[["ts_code", dcol]]
            except Exception:
                return None
        df = df[df["ts_code"].isin(codes)]
        if df.empty:
            continue
        s = df[dcol].astype(str).str[:8]
        for code, d8 in zip(df["ts_code"], s):
            if d8.isdigit() and d8 > best.get(code, ""):
                best[code] = d8
    return best


def audit_news_dim(sources, names):
    """news.db 按 title LIKE 名称反查，返回 {name: (n30, last_ts)}。"""
    db = sqlite3.connect(r"file:C:/项目/老田的AI助手/news_collector/news.db?mode=ro",
                         uri=True)
    out = {}
    cut = (TODAY - timedelta(days=30)).strftime("%Y-%m-%d")
    for nm in names:
        if not nm:
            continue
        if sources is None:
            row = db.execute(
                "SELECT COUNT(*), MAX(ts) FROM news WHERE title LIKE ? AND ts >= ?",
                ("%" + nm + "%", cut)).fetchone()
        else:
            ph = ",".join("?" * len(sources))
            row = db.execute(
                "SELECT COUNT(*), MAX(ts) FROM news WHERE title LIKE ? AND ts >= ? "
                "AND source IN (%s)" % ph,
                ("%" + nm + "%", cut, *sources)).fetchone()
        out[nm] = (row[0], (row[1] or "")[:10])
    db.close()
    return out


def main():
    t0 = time.time()
    queue = build_queue()
    codes = [it["code"] for it in queue]    # TDX ts_code 带点（301047.SZ），勿去点
    code2name = {it["code"]: it["code"] for it in queue}
    names = [it["name"] for it in queue if it["name"]]
    print("队列: %d 只（缠论 actionable + picks n_hit>=2 + 持仓）" % len(queue))

    result = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "queue": queue, "dims": {}, "news_dims": {}}
    for table, cn, dcol, kind in DIMS:
        best = audit_tdx_dim(table, dcol, kind, set(codes))
        if best is None:
            result["dims"][cn] = {"status": "SKIPPED", "note": "表缺列/缺文件"}
            print("[SKIP] %s (%s)" % (cn, table))
            continue
        fresh_line = FRESH_HI if kind == "hi" else FRESH_EVT
        stat = {"fresh": 0, "stale": 0, "absent": 0, "per_code": {}}
        for c in codes:
            mx = best.get(c)
            if not mx:
                stat["absent"] += 1
                tag = "no_event" if kind == "evt" else "absent"
                stat["per_code"][code2name[c]] = tag
            elif mx >= fresh_line:
                stat["fresh"] += 1
                stat["per_code"][code2name[c]] = "fresh:" + mx
            else:
                stat["stale"] += 1
                stat["per_code"][code2name[c]] = "stale:" + mx
        result["dims"][cn] = {"status": "OK", "kind": kind, **stat}
        print("[OK] %-14s fresh=%d stale=%d %s=%d (%.1fs)"
              % (cn, stat["fresh"], stat["stale"],
                 "no_event" if kind == "evt" else "absent", stat["absent"],
                 time.time() - t0))

    # news.db 舆情维度（按名称）
    for gname, srcs in NEWS_SRC_GROUPS.items():
        res = audit_news_dim(srcs, names)
        n_hit = sum(1 for v in res.values() if v[0] > 0)
        result["news_dims"][gname] = res
        print("[OK] %-10s 30天内命中标的 %d/%d (%.1fs)"
              % (gname, n_hit, len(names), time.time() - t0))

    with open(os.path.join(HERE, "target_audit_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("\n落盘 target_audit_result.json，总耗时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    sys.exit(main())
