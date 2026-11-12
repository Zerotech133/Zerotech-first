# -*- coding: utf-8 -*-
"""数据日报聚合出口 mixin（K-ORG-183 M1，2026-10-18）。

GET /api/daily/data   零参数
    → 多维数据日报机读投影，五节各独立 fail-visible（一节坏不拖垮整卡）：
      dist   当日 JEV 判定分布：bias 计数 / 事件类型分层 tier(替代已弃用 conf 三桶, K-ORG-157 口径升级)/
             scope / event_type / bias_probs 均值(K-ORG-176 新字段首次出口) /
             近 7 日逐日趋势（news.db mode=ro 直连，沿 api_dataops 先例）
      bt     情绪×次日实证投影（news_collector/data/sentiment_bt_result.json）
      impact 冲击力分层投影（news_collector/data/jev_impact.json）
      drift  判定漂移哨兵（PG v2_alert title LIKE 'JEV判定漂移%'；
             沿 feishu_control 懒加载 jev_common 先例，DB 不可达降级 error 键）
             S1-04 F3（2026-11-02）：失败另返回结构化 error_detail
             {kind, errno, stage, type, message, dsn(脱敏)}；口令绝不出现，
             error 字符串保留（data_daily.html 兼容），exists=false 语义不变
      health 管道体检：四产物 mtime / 内嵌 generated_at / 时效小时数
      sent   五源情绪分布卡（news_collector/data/sentiment_cards.json，
             sentiment_card 产出；活跃标的前 20，2026-11-09）
      market 市场级情绪指数（news_collector/data/market_sentiment.json，
             market_sentiment 产出；指数/状态/广度/分源，2026-11-09）
      collect 采集新鲜度 SLA（news_collector/data/collect_sla.json，
             collect_sla.py 产出；每源最后成功/滞后小时/覆盖缺口/连续不达标，
             2026-11-13 新增）。只投影读数与判据，不含任何研判结论——
             判定 GREEN/AMBER/RED 由生成器按阈值给出，阈值为经验值、非标准。

只读纪律（沿 api_capability 判例）：mode=ro / fail-visible（产物缺失返回
exists=false 合法观测态而非 500，坏 JSON 带 error 不吞）；参数白名单空集，
带任何参数走 400（api_perf 判例）。
路径锚：REPO 默认 C:/aios（宪法注册表 ASCII 别名 junction），env AIOS_ROOT
可覆盖（仅测试用）；门户不 import quant_backtest（依赖链隔离判例）——
JSON 文件是唯一契约，生成器换实现不影响出口。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ.get("AIOS_ROOT", "C:/aios"))
NEWS_DB = REPO / "news_collector" / "news.db"
BT_JSON = REPO / "news_collector" / "data" / "sentiment_bt_result.json"
IMPACT_JSON = REPO / "news_collector" / "data" / "jev_impact.json"
SENT_JSON = REPO / "news_collector" / "data" / "sentiment_cards.json"
MARKET_JSON = REPO / "news_collector" / "data" / "market_sentiment.json"
# A8（2026-11-10）：XQ_LATEST 收编进 REPO 锚（原写死 C:/aios 全路径绕过了
# env AIOS_ROOT 覆盖——G2 判例的锚不一致病灶，genkit/paths.py 已立案）
XQ_LATEST = REPO / "automation_runs" / "xq_report" / "latest.json"
# 采集新鲜度 SLA（2026-11-13）：生成器 news_collector/collect_sla.py，只读快照
COLLECT_JSON = REPO / "news_collector" / "data" / "collect_sla.json"

CST = timezone(timedelta(hours=8))

# conf 已弃用（AUC=0.414 反相关，见 JEV_capability_ledger）；当日分布改用事件类型分层
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))
from jev_signal import event_type_tier  # noqa: E402

DRIFT_TITLE_LIKE = "JEV判定漂移%"

# 懒加载 jev_common（feishu_control 判例）：DB 断连不拖垮整卡
_jev = None


def _db():
    global _jev
    if _jev is None:
        jev_dir = str(REPO / "jev-eval")
        if jev_dir not in __import__("sys").path:
            __import__("sys").path.insert(0, jev_dir)
        import jev_common  # noqa: WPS433
        _jev = jev_common
    return _jev


def _read_json(p: Path):
    """产物 JSON 只读 → (dict|None, error|None)。缺失是合法空态。"""
    if not p.exists():
        return None, None
    try:
        return json.loads(p.read_text(encoding="utf-8")), None
    except Exception as e:  # noqa: BLE001
        return None, "bad json: %r" % e


def _mask_secrets(text) -> str:
    """S1-04 F3：异常文本脱敏——口令/DSN 密码段一律 ***（永不外泄）。"""
    s = str(text or "")
    s = re.sub(r"(?i)((?:password|passwd|pwd)\s*[=:]\s*)\S+", r"\1***", s)
    s = re.sub(r"(?i)(://[^:/@\s]+:)[^@\s]+@", r"\1***@", s)
    s = re.sub(r"(?i)((?:ZHICE_PG_PASSWORD|PGPASSWORD)\s*=\s*)\S+", r"\1***", s)
    return s[:200].replace("\n", " ")


def _dsn_provenance() -> "dict | None":
    """S1-04 F3/F4：取 jev_common 的脱敏 DSN 来源（失败返回 None，绝不抛）。

    白名单投影：只给诊断需要字段；口令相关只保留来源(wp_source)/是否设置，
    绝不包含口令值/长度以外的凭据内容。
    """
    try:
        prov = _db().dsn_provenance()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(prov, dict):
        return None
    return {
        "host": prov.get("host"),
        "port": prov.get("port"),
        "dbname": prov.get("dbname"),
        "user": prov.get("user"),
        "pw_source": prov.get("password_source", prov.get("pw_source")),
        "password_set": bool(prov.get("password_set")),
        "driver": prov.get("driver"),
    }


def _drift_error_detail(exc, dsn) -> dict:
    """S1-04 F3：drift 失败结构化诊断（kind/errno/stage/dsn），口令绝不出现。

    kind：fallback_unavailable=docker 降级通道不可用（裸 OSError/超时/psql 失败）
          driver_missing=psycopg2 或 jev_common 不可导入
          connect_failed=直连失败（带 errno 或 OperationalError）
          query_failed=其它（SQL/权限/数据异常）
    """
    cls = type(exc).__name__
    stage = getattr(exc, "stage", None)
    errno_v = getattr(exc, "errno", None)
    if cls == "DBFallbackError" or stage in ("spawn", "timeout", "psql"):
        kind = "fallback_unavailable"
    elif isinstance(exc, ImportError):
        kind = "driver_missing"
    elif errno_v is not None or cls in ("OperationalError", "InterfaceError"):
        kind = "connect_failed"
    else:
        kind = "query_failed"
    return {
        "kind": kind,
        "errno": errno_v if isinstance(errno_v, int) else None,
        "stage": stage or None,
        "type": cls,
        "message": _mask_secrets(exc),
        "dsn": dsn,
    }


def dist_section() -> dict:
    """当日 JEV 判定分布 + 近 7 日趋势（news.db RO，fail-visible）。"""
    out = {"exists": False, "db": str(NEWS_DB)}
    if not NEWS_DB.exists():
        out["error"] = "news.db 不存在（首次采集前为合法空态）"
        return out
    today = datetime.now(CST).strftime("%Y-%m-%d")
    week_ago = (datetime.now(CST) - timedelta(days=6)).strftime("%Y-%m-%d")
    try:
        con = sqlite3.connect("file:%s?mode=ro" % NEWS_DB, uri=True)
        rows = con.execute(
            "SELECT created_at, jev FROM news "
            "WHERE created_at >= ? AND jev IS NOT NULL AND jev != ''",
            (week_ago + " 00:00",)).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        out["error"] = "query failed: %r" % e
        return out
    bias = {"bull": 0, "bear": 0, "neutral": 0}
    tier = {"strong": 0, "weak": 0, "unknown": 0}
    scope = {}
    event = {}
    trend = {}   # day -> Counter(bias)
    bp = {"bull": 0.0, "bear": 0.0, "neutral": 0.0}
    bp_n = 0
    bad = 0
    for created_at, raw in rows:
        try:
            j = json.loads(raw)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        day = str(created_at or "")[:10]
        b = j.get("bias")
        if b in bias:
            trend.setdefault(day, {"bull": 0, "bear": 0, "neutral": 0})
            trend[day][b] += 1
            if day == today:
                bias[b] += 1
                sc = (j.get("scope") or "").strip()
                if sc:
                    scope[sc] = scope.get(sc, 0) + 1
                ev = (j.get("event_type") or "").strip()
                if ev:
                    event[ev] = event.get(ev, 0) + 1
                    tier[event_type_tier(ev)] += 1
                probs = j.get("bias_probs")
                if isinstance(probs, dict):
                    vals = {k: probs.get(k) for k in bp}
                    if all(isinstance(v, (int, float)) for v in vals.values()):
                        bp_n += 1
                        for k, v in vals.items():
                            bp[k] += float(v)
    trend7 = []
    for i in range(6, -1, -1):
        d = (datetime.now(CST) - timedelta(days=i)).strftime("%Y-%m-%d")
        c = trend.get(d, {"bull": 0, "bear": 0, "neutral": 0})
        trend7.append({"day": d, **c, "total": sum(c.values())})
    out.update({
        "exists": True, "day": today,
        "total": sum(bias.values()), "bias": bias, "tier_buckets": tier,
        "scope": scope, "event_type": event, "bad_jev_rows": bad,
        "bias_probs_avg": ({k: round(v / bp_n, 4) for k, v in bp.items()}
                           if bp_n else None),
        "bias_probs_n": bp_n,
        "trend7": trend7,
    })
    return out


def bt_section() -> dict:
    """情绪×次日实证投影（sentiment_bt_result.json，StockCorrBacktest 产出）。"""
    data, err = _read_json(BT_JSON)
    out = {"exists": data is not None, "source": BT_JSON.name}
    if err:
        out["error"] = err
        return out
    if data is None:
        out["hint"] = "由 sentiment_backtest.py 每日计划任务产出"
        return out
    for k in ("generated_at", "news_day_range", "n", "n_dedup_total",
              "n_dropped_no_price", "groups", "corr_score_pct1",
              "n_high_conf", "corr_score_pct1_high_conf", "conf_tiers",
              "conf_monotone", "scope_dist", "scope_groups"):
        if k in data:
            out[k] = data[k]
    return out


def impact_section() -> dict:
    """冲击力分层投影（jev_impact.json，E3 CAR 回测产出）。"""
    data, err = _read_json(IMPACT_JSON)
    out = {"exists": data is not None, "source": IMPACT_JSON.name}
    if err:
        out["error"] = err
        return out
    if data is None:
        out["hint"] = "由 jev_impact.py 随回测产出（K-ORG-173）"
        return out
    for k in ("generated_at", "n", "impact_dist", "groups", "applied", "hint"):
        if k in data:
            out[k] = data[k]
    return out


def drift_section() -> dict:
    """判定漂移哨兵（PG v2_alert，jev_drift 每日 07:10 幂等告警）。

    S1-04 F3（2026-11-02）：失败时 `exists=False` 语义不变（fail-visible 合法观测态），
    `error` 保留脱敏字符串供 data_daily.html 原样展示；另加结构化 `error_detail`
    {kind, errno, stage, type, message, dsn(脱敏)} 供页面/健康卡/排障消费。
    """
    out = {"exists": False}
    dsn = None
    try:
        db = _db()
        dsn = _dsn_provenance()
        rows = db.db_rows(
            "SELECT alert_id, title, coalesce(message,''), status, created_at "
            "FROM v2_alert WHERE title LIKE %s ORDER BY id DESC LIMIT 20",
            (DRIFT_TITLE_LIKE,))
        open_n = db.db_rows(
            "SELECT count(*) FROM v2_alert WHERE status='OPEN' "
            "AND title LIKE %s", (DRIFT_TITLE_LIKE,))[0][0]
        out.update({
            "exists": True, "open_count": int(open_n),
            "alerts": [{"alert_id": r[0], "title": r[1], "message": r[2],
                        "status": r[3], "created_at": str(r[4] or "")}
                       for r in rows],
        })
    except Exception as e:  # noqa: BLE001
        detail = _drift_error_detail(e, dsn)
        # 兼容：error 仍是字符串（已脱敏），保持 data_daily.html 渲染不破
        out["error"] = "v2_alert 不可达: %s" % detail["message"]
        out["error_detail"] = detail
    return out


def health_section() -> dict:
    """管道体检：四产物 mtime + 内嵌 generated_at + 时效。"""
    items = []
    for name, p, key in (
            ("sentiment_bt_result", BT_JSON, "generated_at"),
            ("jev_impact", IMPACT_JSON, "generated_at"),
            ("xq_intel_latest", XQ_LATEST, "generated_at"),
            ("news.db", NEWS_DB, None)):
        item = {"name": name, "path": str(p), "exists": p.exists()}
        if p.exists():
            item["mtime"] = datetime.fromtimestamp(
                p.stat().st_mtime, CST).strftime("%Y-%m-%d %H:%M:%S")
            item["age_hours"] = round(
                (time.time() - p.stat().st_mtime) / 3600.0, 1)
            if key:
                data, _ = _read_json(p)
                if isinstance(data, dict) and data.get(key):
                    item["generated_at"] = data[key]
        items.append(item)
    return {"items": items, "checked_at": datetime.now(CST).isoformat()}


def sent_section() -> dict:
    """五源情绪分布卡投影（news_collector/data/sentiment_cards.json）。

    生成器 news_collector/sentiment_card.py（K263 口径扩展）：
    股吧/淘股吧/互动易=引擎打分，雪球=LLM 原生标注，新闻流=声量。
    只暴露 volume>0 的活跃标的前 20（全量 500 票太大，前端无益）。
    """
    data, err = _read_json(SENT_JSON)
    if err:
        return {"exists": True, "error": err}
    if data is None:
        return {"exists": False, "error": None}
    cards = data.get("cards") or []
    active = [c for c in cards if c.get("volume", 0) > 0]
    return {
        "exists": True,
        "generated_at": data.get("generated_at"),
        "days": data.get("days"),
        "n_codes": data.get("n_codes"),
        "n_active": len(active),
        "top": active[:20],
    }


def market_section() -> dict:
    """市场级情绪指数投影（news_collector/data/market_sentiment.json）。

    生成器 news_collector/market_sentiment.py（P5，2026-11-09）：
    六源情绪卡 → 指数 0-100 + 5 档状态机 + 广度 + 分源极性。
    history 序列（market_sentiment_history.jsonl）留在生成器侧，
    出口只投影当日读数（R1 双序列校准是离线分析，不走门户）。
    """
    data, err = _read_json(MARKET_JSON)
    if err:
        return {"exists": True, "error": err}
    if data is None:
        return {"exists": False, "error": None}
    return {
        "exists": True,
        "generated_at": data.get("generated_at"),
        "days": data.get("days"),
        "index": data.get("index"),
        "state": data.get("state"),
        "breadth_pct": data.get("breadth_pct"),
        "n_codes": data.get("n_codes"),
        "n_samples": data.get("n_samples"),
        "sources": data.get("sources") or {},
        "news_volume": data.get("news_volume"),
        "ver": data.get("ver"),
    }


def collect_section() -> dict:
    """采集新鲜度 SLA 投影（news_collector/data/collect_sla.json）。

    生成器 collect_sla.py（2026-11-13）：每源最后成功时间 / 滞后小时 /
    覆盖缺口 / 连续不达标次数。只投影读数与判据，**不含研判结论**。

    字段取舍：`missing` 全量代码清单可长达数十条、且随队列变动，出口只给
    计数与前三只样本，需要全码请直接读 JSON（机读契约）。
    """
    data, err = _read_json(COLLECT_JSON)
    if err:
        return {"exists": True, "error": err}
    if data is None:
        return {"exists": False, "error": None,
                "hint": "由 news_collector/collect_sla.py 产出"}
    rows = []
    for r in data.get("rows") or []:
        missing = r.get("missing") or []
        rows.append({
            "source": r.get("source"),
            "label": r.get("label"),
            "state": r.get("state"),
            "last_ok_ts": r.get("last_ok_ts"),
            "stale_hours": r.get("stale_hours"),
            "covered": r.get("covered"),
            "expected": r.get("expected"),
            "gap": r.get("gap"),
            "gap_open": r.get("gap_open"),
            "gap_known": r.get("gap_known"),
            "gap_pending_fix": r.get("gap_pending_fix"),
            "consecutive_bad": r.get("consecutive_bad"),
            "empty_files": r.get("empty_files"),
            "missing_n": r.get("missing_n"),
            "missing_sample": missing[:3],
            "last_run_ts": r.get("last_run_ts"),
            "note": r.get("note"),
        })
    return {
        "exists": True,
        "generated_at": data.get("generated_at"),
        "summary": data.get("summary") or {},
        "thresholds": data.get("thresholds") or {},
        "thresholds_source": data.get("thresholds_source"),
        "weekend_slack_hours": data.get("weekend_slack_hours"),
        "rows": rows,
        "note": data.get("note"),
    }


class Handler:
    def _daily_data(self, qs):
        # 零参数出口：参数白名单空集，带任何参数走 400（api_perf 判例）
        if self._norm_qs(qs):
            self._json({"error": "本出口不接受任何参数"}, 400)
            return
        self._json({
            "endpoint": "/api/daily/data",
            "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
            "dist": dist_section(),
            "bt": bt_section(),
            "impact": impact_section(),
            "drift": drift_section(),
            "health": health_section(),
            "sent": sent_section(),
            "market": market_section(),
            "collect": collect_section(),
        })
