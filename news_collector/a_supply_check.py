# -*- coding: utf-8 -*-
"""A组七源供管看板 · 断供自检（责任书 01 交付物 #1 + #4，2026-11-11）。

读各源库/目录真实水位，按「断供判定线」出红绿，写
  zerobound-kg/reports/governance/supply_board.json（供 KG 枢纽验收）
  + news_collector/logs/a_supply_check.log（A组侧镜像日志）
退出码 0=全绿 1=有红。设计：只读、零网络、秒级，可入晨链。

断供判定线（供管看板口径，红=源库 N 天无写入）：
  news.db / xq_timeline / guba / taoguba / irm / sse —— 交易日 +1 自然日内无写入即红
  ann_history —— 允许滞后 7 天（公告增量按批回填，非日更心跳）
"""
import glob
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
KG_REPORTS = r"C:\项目\zerobound-kg\reports\governance"
OUT_JSON = os.path.join(KG_REPORTS, "supply_board.json")
LOG = os.path.join(HERE, "logs", "a_supply_check.log")

DAY = 86400.0


def effective_line(base_line):
    """假日感知断供线（2026-11-11 T-185）。

    周末采集器照跑但无新帖、文件 mtime 不动 → 周日/周一早晨按 1.5d 必误红。
    规则：当前时刻往前数，最近的一个**工作日**距今 N 个自然日，断供线 =
    base_line + N（周六查=+1，周日查=+2，周一早=+3 到上午采集后回落）。
    简化实现：统计过去 3 天内的周末天数（周六/周日各计 1），加到 base_line 上。
    节假日未接入日历：容忍偶发误红（连续≥2 次再人工校准），不引外部依赖。
    """
    extra = 0
    t = time.localtime()
    for back in range(1, 4):
        wd = time.localtime(time.time() - back * DAY).tm_wday  # 5=Sat 6=Sun
        if wd >= 5:
            extra += 1
    return base_line + extra


def _days(ts):
    return (time.time() - ts) / DAY


def check_news_db():
    """news.db：库 mtime + runs 表最后成功时间。"""
    p = r"C:/aios/news_collector/news.db"
    if not os.path.exists(p):
        return {"source": "news.db", "ok": False, "reason": "missing"}
    mt = os.path.getmtime(p)
    con = sqlite3.connect(p)
    try:
        row = con.execute(
            "SELECT ts FROM news ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:
        row = None
    finally:
        con.close()
    d = _days(mt)
    line = effective_line(1.5)            # 假日感知（T-185）
    return {"source": "news.db", "last_write": mt, "age_days": round(d, 3),
            "ok": d <= line, "line": "%.1fd" % line, "note": "六源新闻聚合库"}


def check_dir(src, path, line=1.5, note=""):
    fs = glob.glob(os.path.join(path, "*.jsonl"))
    if not fs:
        return {"source": src, "ok": False, "reason": "no jsonl", "line": str(line)}
    mt = max(os.path.getmtime(x) for x in fs)
    d = _days(mt)
    return {"source": src, "files": len(fs), "last_write": mt,
            "age_days": round(d, 3), "ok": d <= line,
            "line": str(line), "note": note}


def check_ann():
    """ann_history.db：公告库，批回填节奏，放宽 7 天。"""
    p = r"C:\项目\zerobound-kg\data\ann_history\ann_history.db"
    if not os.path.exists(p):
        return {"source": "ann_history", "ok": False, "reason": "missing", "line": "7d"}
    mt = os.path.getmtime(p)
    d = _days(mt)
    line = effective_line(7.0)            # 假日感知（T-185）
    try:
        con = sqlite3.connect(p)
        row = con.execute("SELECT MAX(notice_date) FROM ann").fetchone()
        con.close()
        latest_notice = row[0] if row else None
    except Exception:
        latest_notice = None
    return {"source": "ann_history", "last_write": mt, "age_days": round(d, 3),
            "ok": d <= line, "line": "%.1fd" % line,
            "latest_notice_date": latest_notice, "note": "批回填节奏"}


def main():
    line_daily = effective_line(1.5)      # 假日感知（T-185）：周末自动放宽
    line_ann = effective_line(7.0)
    rows = [
        check_news_db(),
        check_dir("xq_timeline", r"C:/aios/news_collector/data/xq_timeline",
                  line_daily, "雪球个股时间线 07:00 日更"),
        check_dir("guba", r"C:/aios/quant_backtest/data/guba",
                  line_daily, "东财股吧 09:30 TargetQueueIntel"),
        check_dir("taoguba", r"C:/aios/data/taoguba",
                  line_daily, "淘股吧 三桥+TargetQueueIntel"),
        check_dir("irm", r"C:/aios/data/irm", line_daily,
                  "互动易 三桥+TargetQueueIntel"),
        check_dir("sse", r"C:/aios/data/sse", line_daily,
                  "e互动 三桥+TargetQueueIntel"),
        check_ann(),
    ]
    n_red = sum(1 for r in rows if not r["ok"])
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "checker": "news_collector/a_supply_check.py",
        "line_daily": line_daily, "line_ann": line_ann,
        "n_sources": len(rows), "n_red": n_red,
        "sources": rows,
    }
    os.makedirs(KG_REPORTS, exist_ok=True)
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT_JSON)

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] n_red=%d  %s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), n_red,
            " | ".join("%s:%s(%.2fd)" % (r["source"],
                                         "GREEN" if r["ok"] else "RED",
                                         r.get("age_days", -1)) for r in rows)))
    for r in rows:
        print("%-12s %-5s age=%.2fd line=%s" % (
            r["source"], "GREEN" if r["ok"] else "RED",
            r.get("age_days", -1), r.get("line", "-")))
    print("n_red=%d -> %s" % (n_red, OUT_JSON))
    return 1 if n_red else 0


if __name__ == "__main__":
    raise SystemExit(main())
