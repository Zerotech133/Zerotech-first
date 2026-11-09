# -*- coding: utf-8 -*-
"""JEV 中台漏跑哨兵（O3）——四查 + 飞书告警，rc 语义可挂计划任务。

背景：2026-11-05/11-06 两次夜批漏跑均靠人肉翻日志发现（最终总结 O3）。
preflight 只管「跑之前的环境」，没有「该跑没跑」的主动巡检。本哨兵补位。

四查（单查失败不阻断其余）：
  C1 feed    最近一次 kind='jev_feed' ok=1 的 ts 距今 >26h → 违规
             （每日 21:00 一轮，26h 容忍 LastRun 延迟触发）
  C2 night   C:/Temp/jev_night.log 尾部 64KB 无 ' END ' 或 END 距今 >26h
             → 违规（夜批不写 runs，以日志为准；日志缺失亦违规）
  C3 水位    最新成功轮 detail.wm 各源 date < 今天-3d → 老化告警
             （v2 dict 水位读 .date；阈值 3 天宽容补数窗口）
  C4 队列    news.jev IS NULL 总数 > 1500 → 积压告警（当前基线 ~370，
             cap 修复后入队抬升 1.2–6.8×，阈值按 3× 基线取整）

用法：
  python tools/jev_sentinel.py            # 巡检；违规打印 + 飞书（env 可关）
  python tools/jev_sentinel.py --json     # 机器可读输出（portal 消费）
退码：0=全绿 2=有违规（计划任务可接 alert_fail_wrap）。
告警开关：JEV_SENTINEL_ALERT=0 关推送（排障用）；推送失败静默不影响 rc。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

STATION = Path(__file__).resolve().parent.parent
NEWS_DB = STATION / "news_collector" / "news.db"
NIGHT_LOG = Path(os.environ.get("JEV_NIGHT_LOG", r"C:\Temp\jev_night.log"))

FEED_STALE_H = 26        # C1：每日一轮 → 24h + 2h 容忍
NIGHT_STALE_H = 26       # C2：同上
WM_STALE_DAYS = 3        # C3：水位老化阈值（天）
QUEUE_THRESHOLD = 1500   # C4：待判积压阈值
TAIL_BYTES = 65536       # C2：日志尾读窗口
ALERT_ENV = "JEV_SENTINEL_ALERT"


def _hours_since(ts_text: str, now=None) -> float | None:
    """'YYYY-MM-DD HH:MM' → 距今小时数；解析失败返回 None。"""
    try:
        t = dt.datetime.strptime(str(ts_text)[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return None
    return ((now or dt.datetime.now()) - t).total_seconds() / 3600.0


def check_feed(db, now=None) -> dict:
    """C1：最近成功 feed 轮年龄。"""
    row = db.execute(
        "SELECT ts, detail FROM runs WHERE kind='jev_feed' AND ok=1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"check": "feed", "ok": False, "reason": "runs 无任何成功 jev_feed 记录"}
    h = _hours_since(row[0], now)
    if h is None:
        return {"check": "feed", "ok": False, "reason": "ts 解析失败", "ts": row[0]}
    ok = h <= FEED_STALE_H
    return {"check": "feed", "ok": ok, "ts": row[0], "age_h": round(h, 1),
            "threshold_h": FEED_STALE_H,
            "reason": None if ok else "距上次成功 feed %.1fh（>%.0fh）" % (h, FEED_STALE_H)}


def check_night(now=None) -> dict:
    """C2：夜批日志尾部有 END 且距今未老化。"""
    if not NIGHT_LOG.exists():
        return {"check": "night", "ok": False,
                "reason": "夜批日志不存在: %s" % NIGHT_LOG}
    try:
        with open(NIGHT_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            text = f.read().decode("utf-8", "replace")
    except OSError as e:
        return {"check": "night", "ok": False, "reason": "日志读失败: %s" % e}
    stamps = re.findall(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] END", text)
    if not stamps:
        return {"check": "night", "ok": False,
                "reason": "日志尾部 64KB 无 END 行（夜批未完成或日志轮转）"}
    h = _hours_since(stamps[-1], now)
    if h is None:
        return {"check": "night", "ok": False, "reason": "END 时间戳解析失败"}
    ok = h <= NIGHT_STALE_H
    return {"check": "night", "ok": ok, "last_end": stamps[-1], "age_h": round(h, 1),
            "threshold_h": NIGHT_STALE_H,
            "reason": None if ok else "夜批 END 距今 %.1fh（>%.0fh）" % (h, NIGHT_STALE_H)}


def _wm_date(v) -> str | None:
    """水位值（v1 str / v2 dict）→ 'YYYYMMDD'。"""
    if isinstance(v, dict):
        v = v.get("date")
    v = str(v or "")
    return v if len(v) == 8 and v.isdigit() else None


def check_watermark(db, now=None) -> dict:
    """C3：最新成功轮水位未老化。"""
    row = db.execute(
        "SELECT detail FROM runs WHERE kind='jev_feed' AND ok=1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return {"check": "watermark", "ok": False, "reason": "无成功 feed 记录"}
    try:
        wm = json.loads(row[0]).get("wm", {})
    except Exception:
        return {"check": "watermark", "ok": False, "reason": "detail 解析失败"}
    now = now or dt.datetime.now()
    stale = {}
    for kind, v in wm.items():
        d = _wm_date(v)
        if not d:
            stale[kind] = "非法水位 %r" % (v,)
            continue
        age_d = (now - dt.datetime.strptime(d, "%Y%m%d")).days
        if age_d > WM_STALE_DAYS:
            stale[kind] = "%s 距今 %dd（>%dd）" % (d, age_d, WM_STALE_DAYS)
    ok = not stale
    return {"check": "watermark", "ok": ok, "wm": {k: _wm_date(v) for k, v in wm.items()},
            "reason": None if ok else "; ".join("%s:%s" % kv for kv in stale.items())}


def check_queue(db) -> dict:
    """C4：待判队列总量。"""
    n = db.execute(
        "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0]
    ok = n <= QUEUE_THRESHOLD
    return {"check": "queue", "ok": ok, "pending": n, "threshold": QUEUE_THRESHOLD,
            "reason": None if ok else "待判队列 %d 条（>%d）" % (n, QUEUE_THRESHOLD)}


def run_checks(now=None) -> list[dict]:
    """跑四查（单查异常降级为该查违规，不阻断其余）。"""
    results = []
    db = None
    try:
        db = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        results.append({"check": "db", "ok": False, "reason": "news.db 只读连失败: %s" % e})
    if db is not None:
        for fn in (check_feed, check_watermark, check_queue):
            try:
                results.append(fn(db, now=now) if fn is not check_queue else fn(db))
            except Exception as e:  # noqa: BLE001
                results.append({"check": getattr(fn, "__name__", "?"), "ok": False,
                                "reason": "检查异常: %s" % e})
        try:
            db.close()
        except Exception:
            pass
    try:
        results.append(check_night(now=now))
    except Exception as e:  # noqa: BLE001
        results.append({"check": "night", "ok": False, "reason": "检查异常: %s" % e})
    return results


def _push(message: str) -> None:
    """飞书推送（失败静默，绝不影响 rc）。"""
    if str(os.environ.get(ALERT_ENV, "1")).strip().lower() in ("0", "false", "no", "off"):
        return
    try:
        sys.path.insert(0, str(STATION / "news_collector"))
        import news_ai  # noqa: E402
        news_ai.push_message(message)
    except Exception:  # noqa: BLE001
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JEV 中台漏跑哨兵（O3）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    results = run_checks()
    bad = [r for r in results if not r.get("ok")]
    out = {
        "schema": "jev_sentinel.v1",
        "checked_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ok": not bad,
        "checks": results,
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print("=== jev_sentinel %s ===" % ("全绿" if not bad else "违规 %d 项" % len(bad)))
        for r in results:
            mark = "OK " if r.get("ok") else "BAD"
            line = "  [%s] %-10s" % (mark, r.get("check"))
            extra = r.get("reason") or " ".join(
                "%s=%s" % (k, v) for k, v in r.items()
                if k in ("ts", "age_h", "last_end", "pending", "wm"))
            print(line + str(extra))
    if bad:
        msg = "【JEV 中台哨兵】%d 项违规：\n%s" % (
            len(bad),
            "\n".join("- %s: %s" % (r.get("check"), r.get("reason")) for r in bad),
        )
        print(msg)
        print("SENTINEL VIOLATION n=%d" % len(bad), file=sys.stderr)
        _push(msg)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
