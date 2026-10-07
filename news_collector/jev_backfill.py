# -*- coding: utf-8 -*-
"""JEV 积压批量补判（历史新闻逐条过本地模型，每轮限量防过载）。

news.jev 为空的条目按时间倒序取一批（--batch，默认 100），
按 jev_judge_batch 内部上限 8 条/组分批调用（组间自动 0.8s 防限流），
判完落库并打印剩余积压。单轮 100 条约 2-3 分钟，剩余积压
靠计划任务多次运行逐步消化（或手动重复执行）。

用法：cd news_collector && python jev_backfill.py [--batch 100]
"""
import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news_ai          # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=100)
    args = ap.parse_args()
    db = sqlite3.connect(os.path.join(HERE, "news.db"))
    rows = db.execute(
        "SELECT id, title, stocks FROM news "
        "WHERE (jev IS NULL OR jev='') ORDER BY ts DESC LIMIT ?",
        (args.batch,)).fetchall()
    if not rows:
        print("BACKFILL done: no pending")
        return
    items = []
    for nid, title, stocks in rows:
        try:
            st = json.loads(stocks) if stocks else []
        except Exception:
            st = []
        items.append({"id": nid, "title": title, "stocks": st})
    t0 = time.time()
    judged = 0
    for i in range(0, len(items), 8):        # jev_judge_batch 单次上限 8
        judges = news_ai.jev_judge_batch(items[i:i + 8])
        for nid, j in judges.items():
            db.execute("UPDATE news SET jev=? WHERE id=?",
                       (json.dumps(j, ensure_ascii=False), nid))
        db.commit()
        judged += len(judges)
    left = db.execute(
        "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0]
    print("BACKFILL judged=%d/%d left=%d %.1fs" % (
        judged, len(items), left, time.time() - t0))


if __name__ == "__main__":
    main()
