# -*- coding: utf-8 -*-
"""lineage_freshness：血缘新鲜度维度（P1-A，纯函数无 IO 核心）。

问题：断链亮牌只看「最近一次作业状态」，status=ok 但数据太老的表是
沉默断链——管道绿、数据馊，最危险。本模块补新鲜度维度：

    freshness = 最近一次 ok 产出距今跨越的交易日数 vs per-table cadence

    age <= cadence                -> fresh
    cadence < age <= cadence+grace -> watch（观察）
    age > cadence+grace           -> stale（亮牌/auto_draft 入裁决池）
    最近作业非 ok                  -> broken（交断链亮牌，本维度不重复判）
    日期不可解析                   -> unknown（fail-visible 不吞）

三消费方共享本纯函数（2026-10-17 优化清单 P1-A）：
  1. 简报 chain_watch.py 第五段（stale 单列亮牌）；
  2. 门户 /api/kg/lineage view=breaks（all_latest 逐行附 freshness）；
  3. intel_wiring.auto_draft_from_lineage（stale ok 行 → watch 裁决）。

交易日历：调用方供给 holidays 列表（config 层），周末固定非交易日。
per-table cadence 元数据落 agent_platform/data/lineage_cadence.json，
缺省 cadence=1 交易日、grace=1 交易日；配置文件缺失 = 全 fresh
（fail-open，不破坏既有亮牌契约）。

纪律：mode=ro 零写副作用（M3 ro 观测面判例）；本模块不连库不联网。
"""
from __future__ import annotations

import json
from datetime import date, datetime as _datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
CADENCE_FILE = REPO_ROOT / "agent_platform" / "data" / "lineage_cadence.json"

DEFAULT_CADENCE = 1     # 交易日：日频数据隔 1 个交易日就该更新
DEFAULT_GRACE = 1       # 宽限交易日：watch 档宽
_MAX_SCAN_DAYS = 400    # 逐日扫描上限（防时钟漂移死循环）

FRESHNESS_LEVELS = ("fresh", "watch", "stale", "broken", "unknown")


# ---------------------------------------------------------------- 配置
def load_cadence(path: Optional[Path] = None) -> Dict:
    """cadence 元数据加载（缺失/损坏 → 空配置 fail-open）。"""
    p = Path(path) if path else CADENCE_FILE
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tables": {}, "holidays": []}
    if not isinstance(cfg, dict):
        return {"tables": {}, "holidays": []}
    cfg.setdefault("tables", {})
    cfg.setdefault("holidays", [])
    return cfg


def _rule_for(cfg: Dict, urn: str) -> Dict:
    rule = cfg.get("tables", {}).get(urn, {})
    if not isinstance(rule, dict):
        rule = {}
    return {
        "cadence": int(rule.get("cadence_trade_days", DEFAULT_CADENCE)),
        "grace": int(rule.get("grace_trade_days", DEFAULT_GRACE)),
    }


# ---------------------------------------------------------------- 交易日历
def _parse_day(value) -> Optional[date]:
    """started_at 兼容解析：datetime / 'YYYY-MM-DD HH:MM:SS' / ISO / 纯日期。

    注意：psycopg2 直查行里 started_at 是 datetime 对象（服务端 assess
    先于 JSON 序列化执行），datetime 是 date 子类，必须先于 date 判断
    并取 .date()，否则 datetime vs date 比较抛 TypeError（19:46 实证）。
    """
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value or "").strip()
    if not s:
        return None
    day = s.split(" ")[0].split("T")[0]
    try:
        parts = [int(x) for x in day.split("-")]
        return date(parts[0], parts[1], parts[2])
    except (ValueError, IndexError, TypeError):
        return None


def trade_days_between(last_day: date, now_day: date,
                       holidays: Optional[List[str]] = None) -> int:
    """自 last_day 次日起到 now_day 的交易日数（周末+holidays 非交易日）。

    last_day >= now_day（未来戳/当天产出）→ 0。
    """
    if last_day >= now_day:
        return 0
    holiday_set = {h.strip() for h in (holidays or [])}
    n = 0
    cur = last_day + timedelta(days=1)
    while cur <= now_day and n < _MAX_SCAN_DAYS:
        if cur.weekday() < 5 and cur.isoformat() not in holiday_set:
            n += 1
        cur += timedelta(days=1)
    return n


# ---------------------------------------------------------------- 评估
def assess(rows: List[Dict], cfg: Dict = None,
           now: Optional[date] = None,
           today_str: str = "") -> List[Dict]:
    """逐行附 freshness（返回新列表，不改入参——ro 判例）。

    行契约：output_table / status / started_at（缺 urn 跳过不评）。
    today_str 供测试注入假 today；now 优先。
    """
    cfg = cfg or {"tables": {}, "holidays": []}
    now_day = now or _parse_day(today_str) or date.today()
    holidays = cfg.get("holidays", [])
    out = []
    for row in rows:
        item = dict(row)
        urn = str(row.get("output_table", "") or "")
        status = str(row.get("status", "") or "")
        if not urn:
            item["freshness"] = "unknown"
            out.append(item)
            continue
        if status != "ok":
            item["freshness"] = "broken"     # 断链亮牌域，不重复判
            out.append(item)
            continue
        last_day = _parse_day(row.get("started_at") or row.get("ended_at"))
        if last_day is None:
            item["freshness"] = "unknown"    # fail-visible 不吞
            out.append(item)
            continue
        rule = _rule_for(cfg, urn)
        age = trade_days_between(last_day, now_day, holidays)
        if age <= rule["cadence"]:
            item["freshness"] = "fresh"
        elif age <= rule["cadence"] + rule["grace"]:
            item["freshness"] = "watch"
        else:
            item["freshness"] = "stale"
        item["age_trade_days"] = age
        out.append(item)
    return out


def summarize(assessed: List[Dict]) -> Dict:
    """汇总：stale/watch 清单（urn+age）+ fresh 计数，供三消费方直接用。"""
    stale = [{"table": r.get("output_table", ""),
              "age_trade_days": r.get("age_trade_days")}
             for r in assessed if r.get("freshness") == "stale"]
    watch = [{"table": r.get("output_table", ""),
              "age_trade_days": r.get("age_trade_days")}
             for r in assessed if r.get("freshness") == "watch"]
    return {
        "stale": stale,
        "watch": watch,
        "fresh_count": sum(1 for r in assessed
                           if r.get("freshness") == "fresh"),
        "broken_count": sum(1 for r in assessed
                            if r.get("freshness") == "broken"),
        "unknown_count": sum(1 for r in assessed
                             if r.get("freshness") == "unknown"),
        "total": len(assessed),
    }


def assess_breaks_payload(payload: Dict, cfg: Dict = None,
                          now: Optional[date] = None) -> Dict:
    """对 /api/kg/lineage view=breaks 的 data 载荷做新鲜度增强。

    返回 {"assessed": [...], "summary": {...}}；载荷无 all_latest → 空。
    """
    rows = (payload or {}).get("all_latest", [])
    assessed = assess(rows, cfg=cfg, now=now)
    return {"assessed": assessed, "summary": summarize(assessed)}
