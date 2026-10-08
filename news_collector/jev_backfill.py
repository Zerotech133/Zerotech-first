# -*- coding: utf-8 -*-
"""JEV 积压批量补判（历史新闻逐条过本地模型，每轮限量防过载）。

news.jev 为空的条目按时间倒序取一批（--batch，默认 100），
按 jev_judge_batch 内部上限 8 条/组分批调用（组间自动 0.8s 防限流），
判完落库并打印剩余积压。单轮 100 条约 2-3 分钟，剩余积压
靠计划任务多次运行逐步消化（或手动重复执行）。

护栏（2026-10-08 K-NEWS-001，A1）：
--deadline 秒数（默认 1800）。每组 8 条处理前检查已耗时，超限即收工。
背景：08:57 实例 batch 500 × 15s timeout × 重试悬挂 2h+，PowerShell 宿主被
任务 ExecutionTimeLimit 杀掉后 python 成孤儿继续跑，IgnoreNew 策略拒绝后续
全部小时轮（0x800710E0 连锁）。deadline 保证 python 自身限时退出。

告警（K-NEWS-001，A3）：
结束后 left > --alert-threshold（默认 100）时经飞书推送告警；
logs/alert_state.json 记录上次告警时间，6 小时内不重发（防小时任务刷屏）。

用法：cd news_collector && python jev_backfill.py [--batch 100] [--deadline 1800]
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news_ai          # noqa: E402

ALERT_STATE = os.path.join(HERE, "logs", "alert_state.json")
ALERT_COOLDOWN_S = 6 * 3600   # 同类告警 6 小时内不重发


def _alert_ok(throttle):
    """节流检查：距上次告警超过 cooldown 才允许再发。"""
    if not throttle:
        return True
    try:
        with open(ALERT_STATE, encoding="utf-8") as f:
            last = json.load(f).get("last_alert_ts", 0)
        return (time.time() - last) >= ALERT_COOLDOWN_S
    except Exception:
        return True  # 状态文件缺失/损坏按可告警处理


def _alert_mark():
    try:
        os.makedirs(os.path.dirname(ALERT_STATE), exist_ok=True)
        with open(ALERT_STATE, "w", encoding="utf-8") as f:
            json.dump({"last_alert_ts": time.time()}, f)
    except Exception:
        pass  # 标记失败只影响节流，不影响主流程


def alert_backlog(left, judged, deadline_hit):
    """积压超阈值告警：飞书推送（失败静默，不影响退出码）。"""
    try:
        msg = ("【JEV 判断积压告警】\n"
               "剩余未判断新闻 %d 条（超过阈值）\n"
               "本轮 judged=%d%s\n"
               "可能原因：云端 systemone 故障或回填实例异常。\n"
               "处置：观察下一小时轮；持续恶化检查 "
               "news_collector\\logs\\ai.log 与 C:\\jev_backfill_task.log"
               % (left, judged,
                  "，deadline 提前收工" if deadline_hit else ""))
        news_ai.push_message(msg)
        _alert_mark()
        return True
    except Exception as e:
        news_ai.log("backfill alert FAIL %s" % str(e)[:100])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--deadline", type=int, default=1800,
                    help="单轮硬超时秒数，超限提前收工（0=不限）")
    ap.add_argument("--alert-threshold", type=int, default=100,
                    help="剩余积压告警阈值（0=不告警）")
    args = ap.parse_args()
    db = sqlite3.connect(os.path.join(HERE, "news.db"))
    rows = db.execute(
        "SELECT id, title, stocks FROM news "
        "WHERE (jev IS NULL OR jev='') ORDER BY ts DESC LIMIT ?",
        (args.batch,)).fetchall()
    if not rows:
        print("BACKFILL done: no pending")
        return
    # Ollama 错峰锁：与 M3 结构化批处理互斥（wait_s≈M3 单条看门狗上限+余量；
    # 正常退出/异常走 atexit 释放，强杀靠锁模块 20 分钟陈旧回收）
    _root = os.path.dirname(HERE)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import atexit
    import ollama_lock
    atexit.register(ollama_lock.release)
    if not ollama_lock.acquire("jev_backfill", wait_s=660):
        print("BACKFILL skip: ollama 锁被 M3 持有超 660s，本轮让位（小时轮自愈）")
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
    deadline_hit = 0
    for i in range(0, len(items), 8):        # jev_judge_batch 单次上限 8
        if args.deadline and (time.time() - t0) >= args.deadline:
            deadline_hit = 1
            break
        judges = news_ai.jev_judge_batch(items[i:i + 8])
        for nid, j in judges.items():
            db.execute("UPDATE news SET jev=? WHERE id=?",
                       (json.dumps(j, ensure_ascii=False), nid))
        db.commit()
        judged += len(judges)
        ollama_lock.heartbeat()
    left = db.execute(
        "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0]
    alerted = 0
    if args.alert_threshold and left > args.alert_threshold \
            and _alert_ok(throttle=True):
        alerted = 1 if alert_backlog(left, judged, deadline_hit) else 0
    print("BACKFILL judged=%d/%d left=%d %.1fs deadline_hit=%d alerted=%d" % (
        judged, len(items), left, time.time() - t0, deadline_hit, alerted))


if __name__ == "__main__":
    main()
