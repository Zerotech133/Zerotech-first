# -*- coding: utf-8 -*-
"""JEV 中台健康只读接口 —— `/api/jev/platform`（2026-11-07）。

中台总览页（portal/jev_platform.html）的数据面：一次拉取聚合五层状态。

    GET /api/jev/platform?days=7        # 吞吐窗口天数，1..30，默认 7

聚合内容：
  feed        最新 jev_feed 轮（ts/ok/wm/各源 inserted/fetched）+ 近 N 轮
  night       夜批状态（复用哨兵 C2 的日志尾部解析：last_end/age_h）
  queue       待判队列：总量 + 按源 top（含最老行年龄）
  throughput  近 N 日 xd_ 源入库/已判/prefilter skip（JSON LIKE 粗筛 + 精解复核）
  sentinel    四查快照（复用 tools/jev_sentinel.run_checks，纯只读）
  timeline    近 N 次 runs（kind IN jev_feed/ai/review/digest 摘要行）

只读纪律：news.db 一律 `mode=ro`；不写任何库、不触发判读/回填。
缓存：进程内 TTL 60s（页面刷新友好，巡检工具建议加 ?fresh=1 绕过）。
"""
from __future__ import annotations

import source_names  # 渠道中文名
import datetime as dt
import json
import os
import sqlite3
import sys
import time
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")

MIN_DAYS, MAX_DAYS, DEF_DAYS = 1, 30, 7
QUEUE_TOP_SOURCES = 8
RECENT_RUNS = 12
CACHE_TTL_S = 60

# 复用哨兵四查（同仓库 tools/；哨兵自身只读 news.db + 读夜批日志）
_TOOLS = os.path.join(STATION, "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
try:
    import jev_sentinel  # noqa: E402
except Exception:  # pragma: no cover
    jev_sentinel = None

_CACHE: dict = {"key": None, "at": 0.0, "data": None}


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % NEWS_DB.replace("\\", "/"),
                            uri=True, timeout=10)


def _feed_block(db, days):
    rows = db.execute(
        "SELECT ts, ok, new_count, detail FROM runs WHERE kind='jev_feed' "
        "ORDER BY id DESC LIMIT ?", (RECENT_RUNS,)).fetchall()
    recent, latest = [], None
    for ts, ok, nc, detail in rows:
        try:
            d = json.loads(detail)
        except Exception:
            d = {}
        item = {"ts": ts, "ok": bool(ok), "inserted": nc,
                "wm": d.get("wm", {}),
                "src": {k: {kk: vv for kk, vv in v.items()
                            if kk in ("inserted", "fetched", "judged",
                                      "pref_skipped", "judge_missing",
                                      "cap_hit", "page_limit",
                                      "judge_skipped_night")}
                        for k, v in d.get("src", {}).items()},
                "errors": d.get("errors", [])}
        recent.append(item)
        if ok and latest is None:
            latest = item
    return {"latest_ok": latest, "recent": recent}


def _queue_block(db, now):
    total = db.execute(
        "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0]
    by_src = db.execute(
        "SELECT source, COUNT(*) n, MIN(ts) oldest FROM news "
        "WHERE jev IS NULL OR jev='' GROUP BY source ORDER BY n DESC LIMIT ?",
        (QUEUE_TOP_SOURCES,)).fetchall()
    sources = []
    for s, n, oldest in by_src:
        age_h = None
        try:
            age_h = round(((now - dt.datetime.strptime(oldest[:16], "%Y-%m-%d %H:%M"))
                           ).total_seconds() / 3600.0, 1)
        except Exception:
            pass
        sources.append({"source": s, "source_cn": source_names.cn(s),
                        "pending": n, "oldest": oldest,
                        "oldest_age_h": age_h})
    return {"total": total, "sources": sources}


def _throughput_block(db, days):
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows = db.execute(
        "SELECT substr(created_at,1,10) d, COUNT(*) n, "
        "SUM(CASE WHEN jev IS NOT NULL AND jev!='' THEN 1 ELSE 0 END) judged "
        "FROM news WHERE source LIKE 'xd_%' AND created_at>=? "
        "GROUP BY 1 ORDER BY 1", (since,)).fetchall()
    out = []
    for d, n, judged in rows:
        pref = db.execute(
            "SELECT COUNT(*) FROM news WHERE source LIKE 'xd_%' "
            "AND substr(created_at,1,10)=? AND jev LIKE '%\"src\":\"prefilter\"%'",
            (d,)).fetchone()[0]
        out.append({"date": d, "ingested": n, "judged": judged,
                    "prefilter_skipped": pref})
    return out


def _timeline_block(db, days):
    rows = db.execute(
        "SELECT kind, ts, ok, new_count, substr(detail,1,120) FROM runs "
        "WHERE kind IN ('jev_feed','ai','review','digest') "
        "ORDER BY id DESC LIMIT ?", (RECENT_RUNS,)).fetchall()
    return [{"kind": k, "ts": ts, "ok": bool(ok), "new_count": nc,
             "note": (detail or "").replace("\n", " ")}
            for k, ts, ok, nc, detail in rows]


def compute(days: int, fresh: bool = False) -> dict:
    key = (days,)
    if not fresh and _CACHE["key"] == key and time.time() - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]
    now = dt.datetime.now()
    db = _ro()
    try:
        data = {
            "schema": "jev_platform.v1",
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "window_days": days,
            "feed": _feed_block(db, days),
            "queue": _queue_block(db, now),
            "throughput": _throughput_block(db, days),
            "timeline": _timeline_block(db, days),
        }
    finally:
        db.close()
    # 夜批 + 哨兵四查（哨兵内部自己开关连接/读日志；失败降级 not null 字段）
    night = {"available": False}
    checks = []
    if jev_sentinel is not None:
        try:
            r = jev_sentinel.check_night(now=now)
            night = {"available": True, "last_end": r.get("last_end"),
                     "age_h": r.get("age_h"), "ok": bool(r.get("ok")),
                     "reason": r.get("reason")}
        except Exception as e:  # noqa: BLE001
            night = {"available": False, "reason": str(e)[:120]}
        try:
            checks = jev_sentinel.run_checks(now=now)
        except Exception as e:  # noqa: BLE001
            checks = [{"check": "sentinel", "ok": False, "reason": str(e)[:120]}]
    data["night"] = night
    data["sentinel"] = {"ok": all(c.get("ok") for c in checks) if checks else None,
                        "checks": checks}
    _CACHE.update(key=key, at=time.time(), data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    """portal_api do_GET 分派入口；异常一律 400/500 明确返回。"""
    qs = parse_qs(qs_raw or "")
    try:
        days = int((qs.get("days") or [str(DEF_DAYS)])[-1])
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise ValueError("days 取值 %d..%d" % (MIN_DAYS, MAX_DAYS))
        fresh = (qs.get("fresh") or [""])[-1] in ("1", "true")
        result = compute(days, fresh=fresh)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
