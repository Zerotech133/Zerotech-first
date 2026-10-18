#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""雪球情报日报生成器（两线预案 M-RT3 消费端，2026-10-18）

双口径设计（数据源只读，零写回）：
  战术情报口径 = xq_status.created_at 落在当日   → 当日新帖（18:00 生产轮产出）
  管道健康口径 = xq_status.fetched_at 落在当日   → 当日采集入库（含播种/回填）

产出（产物统一根 C:/aios/automation_runs/xq_report/）：
  daily/xq_intel_YYYYMMDD.md / .json   当日日报（人读 + 机读）
  latest.md / latest.json              固定名副本（门户/A0A 消费锚点）
  report_status.log                    ASCII 状态日志

退出码：0 正常（含空窗口日）；2 数据库异常。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

DB_RO = r"file:C:/项目/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db?mode=ro"
OUT_ROOT = r"C:/aios/automation_runs/xq_report"
RE_CODE = re.compile(r"^\d{6}$")   # A股/ETF 场内代码
RE_HK = re.compile(r"^\d{5}$")     # 港股代码（5 位）
SIG_EVENT = ("个股研判", "交易策略", "行情观点")   # 高置信信号关注的事件类
BEARISH_MIN = 3          # 异动信号：bearish 绝对数阈值
BEARISH_SHARE = 0.6      # 异动信号：bearish 占比阈值（样本 >= BEARISH_MIN）
CONF_FLOOR = 0.8         # 高置信信号阈值


def load_rows(con: sqlite3.Connection, day: str):
    """拉当日窗口联表行：tactical=created_at 口径，ingest=fetched_at 口径"""
    q = ("SELECT s.status_id, s.created_at, s.fetched_at, s.title, s.desc, s.category,"
         "       c.event_type, c.sentiment, c.tickers, c.confidence, c.parse_ok,"
         "       c.model, c.prompt_version"
         "  FROM xq_status s LEFT JOIN xq_status_classified c"
         "    ON s.status_id = c.status_id")
    lo, hi = day + " 00:00:00", day + " 23:59:59"
    tac = con.execute(q + " WHERE s.created_at BETWEEN ? AND ?", (lo, hi)).fetchall()
    ing = con.execute(q + " WHERE s.fetched_at BETWEEN ? AND ?", (lo, hi)).fetchall()
    return tac, ing


def split_tickers(raw: str):
    """tickers JSON → (证券代码 list, 主题词 list)。代码=6位(A股/ETF)或5位(港股)纯数字"""
    codes, themes = [], []
    try:
        arr = json.loads(raw) if raw and raw != "[]" else []
    except Exception:
        return codes, themes
    for t in arr:
        t = str(t).strip()
        if not t:
            continue
        is_code = bool(RE_CODE.match(t) or RE_HK.match(t))
        (codes if is_code else themes).append(t)
    return codes, themes


def summarize(rows):
    """对窗口行做全维度汇总（未分类行 event_type=None 自动进 pending 桶）"""
    ev, senti, codes, themes = Counter(), Counter(), Counter(), Counter()
    pending, parse_bad = 0, 0
    sig_rows = []          # (conf, code, senti, event, title)
    ticker_senti = defaultdict(Counter)   # code -> Counter(senti)
    lineages = Counter()
    for sid, _cat, _ft, title, _desc, _tg, et, sn, tk, conf, pok, model, pv in rows:
        if et is None:
            pending += 1
            continue
        if not pok:
            parse_bad += 1
            continue
        ev[et] += 1
        lineages["%s|%s" % (model, pv)] += 1
        if sn:
            senti[sn] += 1
        c, th = split_tickers(tk or "")
        for x in c:
            codes[x] += 1
            if sn:
                ticker_senti[x][sn] += 1
        for x in th:
            themes[x] += 1
        if sn and c and et in SIG_EVENT and (conf or 0) >= CONF_FLOOR:
            sig_rows.append((conf, c[0], sn, et, (title or "")[:42]))
    sig_rows.sort(reverse=True)
    return dict(ev=ev, senti=senti, codes=codes, themes=themes, pending=pending,
                parse_bad=parse_bad, sig=sig_rows[:12],
                ticker_senti=ticker_senti, lineages=lineages)


def anomaly_signals(summ, con, day):
    """异动侦测：当日 bearish 突增/高占比，对比前 6 日同 ticker 基线"""
    base_lo = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
    q = ("SELECT c.tickers FROM xq_status_classified c JOIN xq_status s ON s.status_id = c.status_id"
         " WHERE c.parse_ok=1 AND c.sentiment='bearish' AND c.tickers IS NOT NULL AND c.tickers != '[]'"
         "   AND s.created_at >= ? AND s.created_at < ?")
    base = Counter()
    for (tk,) in con.execute(q, (base_lo + " 00:00:00", day + " 00:00:00")):
        for x in split_tickers(tk)[0]:
            base[x] += 1
    out = []
    for code, cnt in summ["ticker_senti"].items():
        bear = cnt.get("bearish", 0)
        n = sum(cnt.values())
        if bear >= BEARISH_MIN and bear / n >= BEARISH_SHARE:
            out.append((code, n, bear, base.get(code, 0)))
    out.sort(key=lambda r: (-r[2], r[0]))
    return out


def render(day, tac, ing, summ_t, anomalies) -> str:
    n_tac, n_ing = len(tac), len(ing)
    cls_t = n_tac - summ_t["pending"]
    L = []
    L.append("# 雪球情报日报 · %s" % day)
    L.append("")
    L.append("> 生成 %s ｜ 数据源 xueqiu_info.db（只读）｜ 生成器 xq_daily_report.py" %
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    L.append("")
    L.append("## 一、总览")
    L.append("")
    L.append("| 口径 | 数量 | 说明 |")
    L.append("|---|---|---|")
    L.append("| 当日采集入库 | %d | fetched_at 口径（含播种/回填，管道健康）|" % n_ing)
    L.append("| 当日新帖 | %d | created_at 口径（战术情报）|" % n_tac)
    L.append("| 其中已打标 | %d | 覆盖率 %s |" %
             (cls_t, "%.0f%%" % (100.0 * cls_t / n_tac) if n_tac else "-"))
    if n_tac == 0:
        L.append("")
        L.append("⚠️ 当日新帖窗口为空——18:00 生产轮（XueqiuCrawl）首跑后才有战术数据，属预期；"
                 "本报告其余段落按当前入库数据渲染。")
    L.append("")
    L.append("## 二、事件分布（当日新帖 · parse_ok）")
    L.append("")
    if summ_t["ev"]:
        for k, v in summ_t["ev"].most_common():
            L.append("- %s：%d" % (k, v))
    else:
        L.append("（空）")
    L.append("")
    L.append("## 三、情绪分布（当日新帖 · parse_ok）")
    L.append("")
    tot = sum(summ_t["senti"].values())
    if tot:
        for k in ("bullish", "neutral", "bearish"):
            v = summ_t["senti"].get(k, 0)
            L.append("- %s：%d（%.0f%%）" % (k, v, 100.0 * v / tot))
    else:
        L.append("（空）")
    L.append("")
    L.append("## 四、热门标的（当日新帖 · 个股代码）")
    L.append("")
    if summ_t["codes"]:
        for k, v in summ_t["codes"].most_common(10):
            L.append("- %s：%d 帖" % (k, v))
    else:
        L.append("（空）")
    if summ_t["themes"]:
        L.append("")
        L.append("主题词 top（基金/指数词，非个股口径）：")
        L.append("")
        for k, v in summ_t["themes"].most_common(6):
            L.append("- %s：%d" % (k, v))
    L.append("")
    L.append("## 五、异动信号（bearish 突增 · 阈值 n>=%d 且占比>=%.0f%%）" % (BEARISH_MIN, BEARISH_SHARE * 100))
    L.append("")
    if anomalies:
        L.append("| 代码 | 当日样本 | bearish | 前6日基线 |")
        L.append("|---|---|---|---|")
        for code, n, bear, b in anomalies[:10]:
            L.append("| %s | %d | %d | %d |" % (code, n, bear, b))
    else:
        L.append("（无——当日无达标标的）")
    L.append("")
    L.append("## 六、高置信信号（confidence>=%.1f · 个股/策略/行情 · 带代码）" % CONF_FLOOR)
    L.append("")
    if summ_t["sig"]:
        L.append("| conf | 代码 | 情绪 | 事件 | 标题 |")
        L.append("|---|---|---|---|---|")
        for conf, code, sn, et, title in summ_t["sig"]:
            L.append("| %.2f | %s | %s | %s | %s |" % (conf, code, sn, et, title.replace("|", "/")))
    else:
        L.append("（无）")
    L.append("")
    L.append("## 七、管道体检")
    L.append("")
    total_all = con_count("SELECT COUNT(*) FROM xq_status")
    cls_all = con_count("SELECT COUNT(*) FROM xq_status_classified")
    L.append("- 库内总量：%d ｜ 已打标：%d ｜ pending：%d" % (total_all, cls_all, total_all - cls_all))
    L.append("- 当日窗口 parse 失败：%d ｜ 未打标：%d" % (summ_t["parse_bad"], summ_t["pending"]))
    if summ_t["lineages"]:
        for k, v in summ_t["lineages"].most_common(2):
            L.append("- 标注 lineage：`%s` × %d" % (k, v))
    if cls_all < total_all:
        L.append("- ⏳ 存量追平轮进行中（probe-then-run 自动错峰），pending=%d 属预期" % (total_all - cls_all))
    L.append("")
    return "\n".join(L)


def con_count(sql: str) -> int:
    con = sqlite3.connect(DB_RO, uri=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="雪球情报日报生成器（M-RT3）")
    ap.add_argument("--date", default=time.strftime("%Y-%m-%d"), help="报表日（默认今天）")
    a = ap.parse_args()
    day = a.date
    os.makedirs(os.path.join(OUT_ROOT, "daily"), exist_ok=True)

    con = sqlite3.connect(DB_RO, uri=True)
    try:
        tac, ing = load_rows(con, day)
        summ = summarize(tac)
        anomalies = anomaly_signals(summ, con, day)
        md = render(day, tac, ing, summ, anomalies)

        payload = {
            "date": day,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ingested_today": len(ing),
            "posts_today": len(tac),
            "classified_today": len(tac) - summ["pending"],
            "event_type": dict(summ["ev"]),
            "sentiment": dict(summ["senti"]),
            "top_codes": dict(summ["codes"].most_common(10)),
            "top_themes": dict(summ["themes"].most_common(6)),
            "anomaly_signals": [{"code": c, "n": n, "bearish": b, "baseline6d": x}
                                for c, n, b, x in anomalies],
            "high_confidence": [{"confidence": cf, "code": c, "sentiment": s,
                                 "event_type": e, "title": t}
                                for cf, c, s, e, t in summ["sig"]],
        }
        stem = "xq_intel_%s" % day.replace("-", "")
        dp = os.path.join(OUT_ROOT, "daily", stem + ".md")
        jp = os.path.join(OUT_ROOT, "daily", stem + ".json")
        open(dp, "w", encoding="utf-8").write(md)
        json.dump(payload, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for ext, src in ((".md", md), (".json", json.dumps(payload, ensure_ascii=False, indent=1))):
            open(os.path.join(OUT_ROOT, "latest" + ext), "w", encoding="utf-8").write(src)

        with open(os.path.join(OUT_ROOT, "report_status.log"), "a", encoding="ascii",
                  errors="backslashreplace") as f:
            f.write("%s | report done date=%s posts=%d ingested=%d anomalies=%d rc=0\n"
                    % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), day, len(tac), len(ing), len(anomalies)))
        print("[report] date=%s posts_today=%d ingested_today=%d -> %s"
              % (day, len(tac), len(ing), dp))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
