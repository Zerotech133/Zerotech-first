# -*- coding: utf-8 -*-
"""金融新闻日报 — 每日 08:30：汇总近 24h 新闻，分版块推送飞书

结构:
  📰 金融新闻日报 YYYY-MM-DD
  🌍 全球财经（华尔街见闻）  N 条
  🇨🇳 市场要闻（新浪财经）   N 条
  ⚡ 7x24 快讯（东方财富）    N 条
  ⭐ 关注命中 N 条（含推送过的）
每版块取最近 8 条标题；推完标记 digested。
用法: python news_digest.py [--dry]
"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "news.db")
CFG_PATH = os.path.join(HERE, "news_config.json")
LOG_PATH = os.path.join(HERE, "logs", "digest.log")
CST = timezone(timedelta(hours=8))

SECTION_ORDER = ["wallstreetcn", "sina", "eastmoney"]
PER_SECTION = 8


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))


def push_message(text):
    tmp = os.path.join(HERE, "outbox", "pending_digest.txt")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(HERE, "send_feishu.py"), tmp],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = (r.stdout or "").strip()
    log("push -> " + out)
    return out.startswith("SEND_OK")


def main():
    dry = "--dry" in sys.argv
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    sections = cfg.get("digest_sections", {})
    db = sqlite3.connect(DB)
    since = (datetime.now(CST) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")

    lines = ["📰 金融新闻日报 · %s" % datetime.now(CST).strftime("%m-%d"),
             "（近 24 小时，按源分版）", ""]
    ids = []
    for src in SECTION_ORDER:
        rows = db.execute(
            "SELECT id, ts, title FROM news WHERE source=? AND created_at>=? "
            "ORDER BY created_at DESC, ts DESC LIMIT ?",
            (src, since, PER_SECTION)).fetchall()
        label = sections.get(src, src)
        if not rows:
            continue
        lines.append("▍%s（%d 条）" % (label, len(rows)))
        for rid, ts, title in rows:
            lines.append("· %s" % title[:64])
            ids.append(rid)
        lines.append("")

    hit_rows = db.execute(
        "SELECT title FROM news WHERE hit=1 AND created_at>=? ORDER BY created_at DESC LIMIT 10",
        (since,)).fetchall()
    if hit_rows:
        lines.append("▍⭐ 关注命中（%d 条）" % len(hit_rows))
        for (t,) in hit_rows:
            lines.append("· %s" % t[:64])
        lines.append("")

    if len(ids) < 3 and not dry:
        log("digest skipped: too few items (%d)" % len(ids))
        db.close()
        print("DIGEST SKIP")
        return

    lines.append("—— AI 工作站 · 信息采集域 · 每日 08:30")
    text = "\n".join(lines)
    sent = dry or push_message(text)
    if sent:
        db.executemany("UPDATE news SET digested=1 WHERE id=?", [(i,) for i in ids])
        db.execute("INSERT INTO runs(kind,ts,ok,new_count,push_count,detail) VALUES(?,?,?,?,?,?)",
                   ("digest", datetime.now(CST).strftime("%Y-%m-%d %H:%M"), 1,
                    0, 1, "sections=%d items=%d" % (len(sections), len(ids))))
        db.commit()
    db.close()
    print("DIGEST %s items=%d" % ("SENT" if sent else "FAIL", len(ids)))


if __name__ == "__main__":
    main()
