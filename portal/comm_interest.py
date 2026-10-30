# -*- coding: utf-8 -*-
"""阚泽部 · customer_interest 聚合与商机信号（K4.1/K4.2，WO-FS-148-6）。

一句话：把客户的每一次提问变成可经营的行为统计——但**只存统计，不存原文**。

数据口径（v2 方案 §3.2/§3.3）
--------------------------
- 表 `customer_interest`（sqlite，按天 upsert）：咨询次数、被闸次数、
  三域分布、高频标的/主题计数。**无任何原文字段**（隐私红线）。
- 信号四档（7 日滚动窗口）：
    hot       ★★★ 咨询≥5 且被闸≥2 且未订阅（想要没权限 = 最强商机）
    prospect  ★★  咨询≥3 且未订阅
    expiring  ★★  已订阅且到期≤7天且有活跃（流失预警反用）
    churning  ★    曾活跃（7日内≥3）但近 3 日零咨询（已订阅者）
- 空态不造数：无数据即返回空列表，页面显示空态。

设计说明
--------
- 独立库文件（默认 `portal/work/comm_interest.sqlite3`，`COMM_INTEREST_DB`
  可覆盖），不写订阅账本库——账本是交易事实，画像是行为统计，两者
  生命周期与回滚口径不同，混库会让「删画像」变成动账本。
- 写入时机：smart_intake 判定后同步 upsert（不依赖定时任务）。
- 只读查询：stats_7d / signals，供 sub_admin 商务面与个性化引导卡消费。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

CONSULT_INTENTS = ("stock_consult", "watch_setup", "subscription_qa", "billing")
QUANT_INTENTS = ("stock_consult", "watch_setup")
BUSINESS_INTENTS = ("subscription_qa", "billing")
KNOWLEDGE_INTENTS = ("feed",)

HOT_CONSULT_MIN = 5
HOT_BLOCKED_MIN = 2
PROSPECT_CONSULT_MIN = 3
CHURN_ACTIVE_MIN = 3
CHURN_IDLE_DAYS = 3
EXPIRING_DAYS = 7

_DDL = """
CREATE TABLE IF NOT EXISTS customer_interest (
  open_id        TEXT NOT NULL,
  day            TEXT NOT NULL,
  domain_quant   INTEGER NOT NULL DEFAULT 0,
  domain_business INTEGER NOT NULL DEFAULT 0,
  domain_knowledge INTEGER NOT NULL DEFAULT 0,
  domain_comm    INTEGER NOT NULL DEFAULT 0,
  consult_count  INTEGER NOT NULL DEFAULT 0,
  blocked_count  INTEGER NOT NULL DEFAULT 0,
  top_codes      TEXT NOT NULL DEFAULT '{}',
  top_topics     TEXT NOT NULL DEFAULT '{}',
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (open_id, day)
);
CREATE INDEX IF NOT EXISTS idx_ci_day ON customer_interest(day);
"""


def _db_path():
    p = os.environ.get("COMM_INTEREST_DB")
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "work" / "comm_interest.sqlite3"


def _conn():
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


def init_schema():
    """幂等建表。→ (ok, error)。"""
    try:
        with _conn() as c:
            c.executescript(_DDL)
        return True, None
    except sqlite3.Error as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


def _merge_json_counts(raw, key, inc=1):
    try:
        obj = json.loads(raw or "{}")
        if not isinstance(obj, dict):
            obj = {}
    except ValueError:
        obj = {}
    obj[key] = int(obj.get(key, 0)) + inc
    return json.dumps(obj, ensure_ascii=False)


def record(open_id, intent, code=None, blocked=False, day=None):
    """按天 upsert 一次行为。→ (ok, error)。

    未知意图不报错：domain 记 comm，不计咨询——宁可少记也不猜。
    """
    d = day or date.today().isoformat()
    delta = {
        "domain_quant": 1 if intent in QUANT_INTENTS else 0,
        "domain_business": 1 if intent in BUSINESS_INTENTS else 0,
        "domain_knowledge": 1 if intent in KNOWLEDGE_INTENTS else 0,
        "domain_comm": 1
        if intent not in (QUANT_INTENTS + BUSINESS_INTENTS + KNOWLEDGE_INTENTS)
        else 0,
        "consult_count": 1 if intent in CONSULT_INTENTS else 0,
        "blocked_count": 1 if blocked else 0,
    }
    try:
        init_schema()
        with _conn() as c:
            row = c.execute(
                "SELECT top_codes FROM customer_interest WHERE open_id=? AND day=?",
                (open_id, d),
            ).fetchone()
            codes = (
                _merge_json_counts(row["top_codes"] if row else "{}", code)
                if code
                else (row["top_codes"] if row else "{}")
            )
            import time

            c.execute(
                """
                INSERT INTO customer_interest
                  (open_id, day, domain_quant, domain_business,
                   domain_knowledge, domain_comm, consult_count,
                   blocked_count, top_codes, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(open_id, day) DO UPDATE SET
                  domain_quant   = domain_quant   + excluded.domain_quant,
                  domain_business= domain_business+ excluded.domain_business,
                  domain_knowledge=domain_knowledge+excluded.domain_knowledge,
                  domain_comm    = domain_comm    + excluded.domain_comm,
                  consult_count  = consult_count  + excluded.consult_count,
                  blocked_count  = blocked_count  + excluded.blocked_count,
                  top_codes      = ?,
                  updated_at     = excluded.updated_at
            """,
                (
                    open_id,
                    d,
                    delta["domain_quant"],
                    delta["domain_business"],
                    delta["domain_knowledge"],
                    delta["domain_comm"],
                    delta["consult_count"],
                    delta["blocked_count"],
                    codes,
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    codes,
                ),
            )
        return True, None
    except sqlite3.Error as ex:
        return False, "%s: %s" % (type(ex).__name__, ex)


def _rows_between(open_id=None, days=7, offset_days=0):
    end = date.today() - timedelta(days=offset_days)
    start = end - timedelta(days=days - 1)
    q = "SELECT * FROM customer_interest WHERE day BETWEEN ? AND ?" + (  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
        " AND open_id=?" if open_id else ""
    )
    args = [start.isoformat(), end.isoformat()] + ([open_id] if open_id else [])
    try:
        init_schema()
        with _conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]
    except sqlite3.Error:
        return []


def stats_7d(open_id):
    """→ {consult_count, blocked_count, domain_*, top_codes, active_days}。"""
    rows = _rows_between(open_id, days=7)
    agg = {
        "consult_count": 0,
        "blocked_count": 0,
        "domain_quant": 0,
        "domain_business": 0,
        "domain_knowledge": 0,
        "domain_comm": 0,
    }
    codes = {}
    for r in rows:
        for k in agg:
            agg[k] += int(r.get(k) or 0)
        try:
            for k, v in (json.loads(r.get("top_codes") or "{}")).items():
                codes[k] = codes.get(k, 0) + int(v)
        except ValueError:
            continue
    agg["top_codes"] = dict(sorted(codes.items(), key=lambda x: -x[1])[:5])
    agg["active_days"] = len(rows)
    return agg


def _default_subscribed_lookup(open_ids):
    """默认订阅判定：→ {oid: {"subscribed": bool, "end": "YYYY-MM-DD"|None}}。

    账本不可用时 subscribed=False 且 end=None——按未订阅处理但**不误标热客**
    （热客还要求被闸≥2，账本挂了连闸都不会发生）。
    """
    out = {}
    try:
        import subscription_store as ss

        for oid in open_ids:
            try:
                s = ss.get_subscriber_summary(oid) or {}
                subs = s.get("subscriptions") or []
                end = (subs[-1].get("end") or "")[:10] if subs else None
                out[oid] = {"subscribed": bool(subs), "end": end}
            except Exception:  # noqa: BLE001
                out[oid] = {"subscribed": False, "end": None}
    except Exception:  # noqa: BLE001
        for oid in open_ids:
            out[oid] = {"subscribed": False, "end": None}
    return out


def _sub_view(oid, sub_map):
    """兼容两种入参：{oid: bool}（测试/简单调用）与 {oid: {subscribed,end}}。"""
    v = sub_map.get(oid) if isinstance(sub_map, dict) else None
    if isinstance(v, dict):
        return bool(v.get("subscribed")), v.get("end")
    return bool(v), None


def _days_to(end_str):
    """到期还有几天（无法解析返回 None）。"""
    if not end_str:
        return None
    try:
        y, m, d = [int(x) for x in str(end_str)[:10].split("-")]
        return (date(y, m, d) - date.today()).days
    except (ValueError, TypeError):
        return None


def signals(subscribed=None, limit=50):
    """7 日窗口商机信号（按强度排序）。空态返回 []，不造数。"""
    rows = _rows_between(None, days=7)
    if not rows:
        return []
    per = {}
    for r in rows:
        oid = r["open_id"]
        cur = per.setdefault(
            oid, {"consult": 0, "blocked": 0, "days": set(), "codes": {}}
        )
        cur["consult"] += int(r.get("consult_count") or 0)
        cur["blocked"] += int(r.get("blocked_count") or 0)
        cur["days"].add(r["day"])
        try:
            for k, v in (json.loads(r.get("top_codes") or "{}")).items():
                cur["codes"][k] = cur["codes"].get(k, 0) + int(v)
        except ValueError:
            continue

    sub_map = (
        subscribed if subscribed is not None else _default_subscribed_lookup(list(per))
    )
    recent = set(r["open_id"] for r in _rows_between(None, days=CHURN_IDLE_DAYS))

    out = []
    for oid, m in per.items():
        is_sub, end = _sub_view(oid, sub_map)
        level = stars = None
        hint = ""
        d2e = _days_to(end) if is_sub else None
        if (
            not is_sub
            and m["consult"] >= HOT_CONSULT_MIN
            and m["blocked"] >= HOT_BLOCKED_MIN
        ):
            level, stars = "hot", 3
            hint = "立即人工跟进 / 发限时体验券"
        elif not is_sub and m["consult"] >= PROSPECT_CONSULT_MIN:
            level, stars = "prospect", 2
            hint = "引导卡持续触达，观察升级为热客户"
        elif (
            is_sub
            and d2e is not None
            and 0 <= d2e <= EXPIRING_DAYS
            and m["consult"] >= PROSPECT_CONSULT_MIN
        ):
            level, stars = "expiring", 2
            hint = "订阅 %d 天后到期且仍活跃——续费提醒优先触达" % d2e
        elif is_sub and oid not in recent and m["consult"] >= CHURN_ACTIVE_MIN:
            level, stars = "churning", 1
            hint = "已订阅但近 3 日无互动，回访确认需求"
        if not level:
            continue
        top = sorted(m["codes"].items(), key=lambda x: -x[1])[:3]
        out.append(
            {
                "open_id": oid,
                "open_id_masked": _mask(oid),
                "level": level,
                "stars": stars,
                "hint": hint,
                "consult_7d": m["consult"],
                "blocked_7d": m["blocked"],
                "active_days": len(m["days"]),
                "top_codes": [c for c, _ in top],
            }
        )
    out.sort(key=lambda x: (-x["stars"], -x["blocked_7d"], -x["consult_7d"]))
    return out[:limit]


def _mask(open_id):
    s = str(open_id or "")
    if len(s) <= 8:
        return s[:2] + "**"
    return s[:4] + "**" + s[-2:]


def guide_text(open_id, top_n=2):
    """个性化引导卡文案（引用真实统计，无数据返回 None 不硬编）。"""
    st = stats_7d(open_id)
    if not st["consult_count"]:
        return None
    parts = []
    if st["top_codes"]:
        codes = "、".join(list(st["top_codes"])[:top_n])
        parts.append(codes)
    text = (
        "您本周已咨询 %d 次%s——订阅「盯盘档」后，这类问题由盯盘官"
        "实时解答，还能设自动提醒。"
        % (
            st["consult_count"],
            ("（主要是 " + "、".join(parts) + "）") if parts else "",
        )
    )
    return text
