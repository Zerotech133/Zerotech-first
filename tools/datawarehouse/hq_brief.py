#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""张松数据仓 → 指挥部数据简报（只读 MVP）。

把三类东西合成一个 HQ 可消费对象：
1) 数据健康：资产登记/开放、catalog/registry、采集新鲜度、契约；
2) 挖掘信号：因子衰减、选股库可执行性、学习语料转化；
3) 决策问题：只给 A0/指挥部“该问什么/该补什么”，不裁决业务。

不新增端口；可被 A0 跑板/会议/日报以 JSON 消费。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL_STATIC = ROOT / "portal" / "static"
DEFAULT_DW = "http://127.0.0.1:8091/api/dw/stats"
DEFAULT_OPS = "http://127.0.0.1:8091/api/dataops/overview"
SCHEMA = "hq/data_brief/v1"
TZ = dt.timezone(dt.timedelta(hours=8))

# 采集窗口：各采集器的当日增量在 18:00 之后才可比。窗口前查当日增量必为 0，
# 这是时点问题不是断流（R12 D3 修正：原实现把二者混为一谈，7 席据此误判）。
COLLECT_WINDOW_HOUR = 18


def _now_dt(now=None) -> dt.datetime:
    """把 now 参数（None / ISO 字符串 / datetime）归一成 +08 的 datetime。"""
    if now is None:
        return dt.datetime.now(TZ)
    if isinstance(now, str):
        try:
            now = dt.datetime.fromisoformat(now)
        except ValueError:
            return dt.datetime.now(TZ)
    if not isinstance(now, dt.datetime):
        return dt.datetime.now(TZ)
    return now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)


def _collect_window_open(now=None) -> bool:
    return _now_dt(now).hour >= COLLECT_WINDOW_HOUR


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _now_iso(now=None) -> str:
    if now is None:
        now = dt.datetime.now(TZ)
    if isinstance(now, str):
        return now
    return now.astimezone(TZ).replace(microsecond=0).isoformat()


def build_brief(dw: dict, ops: dict, factor: dict, selection: dict, learning: dict,
                now=None) -> dict:
    """纯函数：输入已取到的各源 JSON，输出 hq/data_brief/v1。"""
    sv2 = dw.get("stats_v2") or {}
    file_inv = sv2.get("file_inventory") or {}
    pg = sv2.get("pg") or {}
    all_scope = sv2.get("all") or {}
    collectors = ops.get("collectors") or []
    pools = ops.get("pools") or []

    red_sources = []
    zero_sources = []
    for c in collectors:
        name = c.get("name") or c.get("key")
        if c.get("level") == "red":
            red_sources.append({"name": name, "lag_hours": c.get("lag_hours"),
                                "last_output_at": c.get("last_output_at")})
        if c.get("today_incr") == 0 and c.get("output_metrics"):
            zero_sources.append({"name": name, "last_output_at": c.get("last_output_at")})
    red_pools = []
    for p in pools:
        for t in p.get("tables") or []:
            if t.get("level") == "red":
                red_pools.append({"db": p.get("db"), "table": t.get("name"),
                                  "lag_hours": t.get("lag_hours"),
                                  "last_output_at": t.get("last_output_at")})

    alarms = []
    if red_sources or red_pools:
        alarms.append({"severity": "red", "code": "collector_lag",
                       "summary": "采集/表级延时报红，需 DATA1 先修数",
                       "evidence": {"collectors": red_sources, "tables": red_pools}})
    if zero_sources:
        if _collect_window_open(now):
            alarms.append({"severity": "amber", "code": "today_zero",
                           "summary": "采集窗口（%d:00）已过，当日增量仍为 0（可能是断流），勿把存量当今日新增" % COLLECT_WINDOW_HOUR,
                           "evidence": zero_sources})
        else:
            alarms.append({"severity": "info", "code": "today_pending",
                           "summary": "采集窗口（%d:00）未到，当日增量为 0 属时点常态，不作为断流" % COLLECT_WINDOW_HOUR,
                           "evidence": zero_sources})
    # 口径（R12 D2 修正）：catalog 与 registry 的可比单位是「物理表」= registry_display。
    # registry_physical 是资产全量（含外部/非物理表资产），拿它与 catalog 表数相比属
    # **不同单位相减**——原实现据此报红，导致 9 席每天读到一个永不消失的假红。
    cat_tables = dw.get("catalog_tables")
    reg_physical = dw.get("registry_physical")
    reg_display = dw.get("registry_display")
    comparable = isinstance(cat_tables, int) and isinstance(reg_display, int)
    catalog_registry_drift = None
    catalog_registry_mismatch = False
    if comparable and cat_tables != reg_display:
        catalog_registry_mismatch = True
        alarms.append({"severity": "red", "code": "catalog_registry_mismatch",
                       "summary": "catalog %s 表 != registry display %s 表；同为物理表口径却不一致，先修 catalog/registry 再验收" % (
                           cat_tables, reg_display),
                       "evidence": {"catalog_tables": cat_tables,
                                    "registry_display": reg_display,
                                    "registry_physical": reg_physical,
                                    "catalog_version": dw.get("catalog_version")}})
    elif comparable and isinstance(reg_physical, int):
        catalog_registry_drift = {
            "catalog_tables": cat_tables,
            "registry_display": reg_display,
            "registry_physical": reg_physical,
            "physical_minus_display": reg_physical - reg_display,
            "note": ("physical 为资产全量（含外部/非物理表资产），display 与 catalog 同为物理表口径；"
                     "差值属正常，逐项明细见 P3-1 对账表"),
        }
        alarms.append({"severity": "info", "code": "catalog_registry_drift",
                       "summary": "catalog/display 同为 %s 表已对齐；physical 资产全量 %s（含 %s 项非物理表资产）" % (
                           cat_tables, reg_physical, reg_physical - reg_display),
                       "evidence": catalog_registry_drift})
    mismatch = (dw.get("contract_artifacts") or {}).get("mismatch", 0)
    if mismatch:
        alarms.append({"severity": "amber", "code": "contract_mismatch",
                       "summary": "数据契约产物 %d 个 sha256 不一致" % mismatch,
                       "evidence": {"mismatch": mismatch}})

    # 挖掘信号
    signals = []
    f = factor or {}
    if f:
        factors = f.get("factors") or []
        decay = f.get("decay_alerts") or []
        pass_ls = [x.get("factor") for x in factors if "PASS" in str(x.get("verdict", ""))]
        fail = [x.get("factor") for x in factors if "FAIL" in str(x.get("verdict", ""))]
        signals.append({
            "id": "factor.decay", "kind": "risk",
            "summary": "因子日更：%d 个因子，%d 个四关 PASS(LS)，%d 个 FAIL，%d 个衰减告警" % (
                len(factors), len(pass_ls), len(fail), len(decay)),
            "evidence": {"as_of": f.get("latest_date"), "owner": f.get("owner"),
                         "pass_ls": pass_ls, "fail": fail,
                         "decay": [x.get("factor") for x in decay]},
            "decision_hint": "PASS 不等于可交易；FAIL/衰减因子不得进策略或需重新验证",
        })
    s = selection or {}
    if s:
        strategies = s.get("strategies") or []
        approved = [x.get("id") for x in strategies if x.get("approved")]
        picks = s.get("picks") or []
        signals.append({
            "id": "selection.executability", "kind": "decision",
            "summary": "选股库 %d 策略，%d 获批，picks=%d；当前无自动可执行 alpha" % (
                len(strategies), len(approved), len(picks)),
            "evidence": {"date": s.get("date"),
                         "statuses": {x.get("id"): x.get("status") for x in strategies},
                         "approved": approved},
            "decision_hint": "没有获批策略时，指挥部不应把研究候选当交易信号",
        })
    l = learning or {}
    if l:
        ov = l.get("overall") or {}
        signals.append({
            "id": "learning.yield", "kind": "coverage",
            "summary": "学习语料：%s 文件，%s 单元，候选池 %s，卡片 %s" % (
                ov.get("files"), ov.get("units"), ov.get("candidate_pool"), ov.get("cards_total")),
            "evidence": {"units_with_domain": ov.get("units_with_domain"),
                         "unusable_reasons": ov.get("unusable_reasons")},
            "decision_hint": "语料产能不等于可用知识；先看领域覆盖与卡片通过率",
        })

    # 时点语义（R12 D3）：data_date 是**盘点文件 mtime**，不是数据最新交易日。
    # 两者必须分开报，否则「盘点没刷新」会被读成「数据断流」。
    age_days = dw.get("inventory_age_days")
    if not isinstance(age_days, int):
        try:
            d = dt.date.fromisoformat(str(dw.get("data_date")))
            age_days = (_now_dt(now).date() - d).days
        except (TypeError, ValueError):
            age_days = None

    questions = []
    if red_sources or red_pools:
        questions.append({"to": "DATA1", "q": "哪几条采集/表先修复？给出 ETA 与替代数据源"})
    if age_days is not None and age_days >= 1:
        questions.append({"to": "DATA1", "q": "数据集盘点（dataset_inventory）仍是 %s 那一版（距今 %d 天）：它只反映盘点时刻，不代表数据断流；何时重跑盘点？" % (dw.get("data_date"), age_days)})
    if catalog_registry_mismatch:
        questions.append({"to": "DATA1", "q": "catalog %s 表与 registry display %s 表不一致（同为物理表口径）：哪边是真源？何时对齐？" % (cat_tables, reg_display)})
    if s and not (s.get("picks") or []):
        questions.append({"to": "A0/ACAD1", "q": "当前无可执行 picks，今天指挥部按“研究/观察”还是“空仓”处理？"})
    if f and (f.get("decay_alerts") or []):
        questions.append({"to": "A0/FACTOR1", "q": "衰减因子是否暂停相关策略？需给 A/B 口径"})

    return {
        "schema": SCHEMA,
        "as_of": _now_iso(now),
        "owner": {"agent_id": "DATA1", "name": "张松",
                  "position": "数据资源主官（兰台令）"},
        "data_health": {
            "assets": {"all": all_scope.get("total"), "registered": all_scope.get("registered"),
                       "exposed": all_scope.get("exposed"),
                       "file": {"total": file_inv.get("total"),
                                "registered": file_inv.get("registered"),
                                "exposed": file_inv.get("exposed")},
                       "pg": {"total": pg.get("total"), "registered": pg.get("registered"),
                              "available": pg.get("available")}},
            "catalog": {"version": dw.get("catalog_version"),
                        "tables": dw.get("catalog_tables"),
                        "updated": dw.get("catalog_updated")},
            "registry": {"physical": dw.get("registry_physical"),
                         "display": dw.get("registry_display"),
                         "drift": catalog_registry_drift},
            "freshness": {"data_date": dw.get("data_date"),
                          "data_date_source": dw.get("data_date_source") or "unknown",
                          "age_days": age_days,
                          "inventory_age_days": dw.get("inventory_age_days"),
                          "latest_trade_date": dw.get("latest_trade_date"),
                          "latest_trade_date_source": dw.get("latest_trade_date_source"),
                          "collect_window_open": _collect_window_open(now),
                          "state": dw.get("state")},
            "collectors": {"red": red_sources, "today_zero": zero_sources},
            "contracts": dw.get("contract_artifacts") or {},
        },
        "alarms": alarms,
        "mining_signals": signals,
        "questions": questions,
        "boundaries": [
            "DATA1 只提供数据资源/证据，不裁决业务口径",
            "挖掘信号不是交易建议；无获批策略时不得当信号执行",
            "不得把 today=0 的存量当当日新增",
        ],
        "sources": [DEFAULT_DW, DEFAULT_OPS,
                    "portal/static/factor_daily.json",
                    "portal/static/selection_daily.json",
                    "portal/static/learning_capability.json"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="张松数据仓→指挥部数据简报（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--dw", default=DEFAULT_DW)
    ap.add_argument("--ops", default=DEFAULT_OPS)
    a = ap.parse_args(argv)
    try:
        dw = _get(a.dw)
        ops = _get(a.ops)
    except Exception as e:  # noqa: BLE001
        print("data_brief_error: %s" % str(e)[:300], file=sys.stderr)
        return 2
    factor = _load_json(PORTAL_STATIC / "factor_daily.json")
    selection = _load_json(PORTAL_STATIC / "selection_daily.json")
    learning = _load_json(PORTAL_STATIC / "learning_capability.json")
    brief = build_brief(dw, ops, factor, selection, learning)
    text = json.dumps(brief, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
