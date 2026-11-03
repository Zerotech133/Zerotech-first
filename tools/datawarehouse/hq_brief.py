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
        alarms.append({"severity": "amber", "code": "today_zero",
                       "summary": "当日增量为 0（可能是时点/断流），勿把存量当今日新增",
                       "evidence": zero_sources})
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

    age_days = None
    try:
        d = dt.date.fromisoformat(str(dw.get("data_date")))
        age_days = (dt.datetime.now(TZ).date() - d).days
    except (TypeError, ValueError):
        pass

    questions = []
    if red_sources or red_pools:
        questions.append({"to": "DATA1", "q": "哪几条采集/表先修复？给出 ETA 与替代数据源"})
    if age_days is not None and age_days >= 1:
        questions.append({"to": "DATA1", "q": "dataset inventory 仍为 %s（距今天 %d 天），何时刷新到今日盘点？" % (dw.get("data_date"), age_days)})
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
                         "display": dw.get("registry_display")},
            "freshness": {"data_date": dw.get("data_date"), "age_days": age_days,
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
