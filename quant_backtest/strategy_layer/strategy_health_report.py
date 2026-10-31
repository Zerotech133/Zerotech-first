# -*- coding: utf-8 -*-
"""Strategy Health 周报 / 上游 Bias Ticket（WO-HEALTH-WEEKLY-20261031，Phase 2-D）。

只读周报：吃「多日后验行」（可注入 list / JSON 文件，字段契约同
strategy_layer/strategy_health.py）＋ 可选策略卡 / registry 视图，按自然日
窗口（默认最近 7 天）聚合出每个策略的周度 HealthCard 与上游偏差票
（BiasTicket draft），回答「本周哪些策略有偏差、该通知哪个上游 owner 怎么调」。

本模块**只读**（硬约束，同 strategy_health 判例）：
  · 不写 PG / 不写生产数据（落盘仅限调用方显式给的 --out / out_path）；
  · 不改 strategy_registry.json / strategy_cards.json 的权重或启用态；
  · 不自动调权、不自动改 registry、不下单、不新增计划任务、不重启服务；
  · 缺失字段一律显式 insufficient / null，不造数（不填 0 充数）。

输入（参数）：
  · rows / rows_file：多日 posterior 行（JSON 行列表或 {"rows": [...]}）；
  · days（默认 7）：窗口 = [asof-(days-1) 自然日, asof]；交易日以窗口内
    实际出现的 trade_date 计数（本模块不自带交易日历，不猜）；
  · asof：窗口锚点日（YYYYMMDD / YYYY-MM-DD），缺省 = 窗口内最大 trade_date；
  · start_date/end_date：显式覆盖窗口（给了就不用 days 推算 start）；
  · cards / cards_file / registry / registry_path：策略卡与 registry 视图；
    都不给时自动读 strategy_layer.strategy_cards（load_cards + registry_view），
    不可用则显式 unavailable，不臆造卡片。
  · equity_curves / costs / thresholds：透传 strategy_health 的口径注入。

输出 WeeklyHealthReport（dict，JSON 友好）：
  {
    "schema": "strategy_health_weekly/v1",
    "status": "OK" | "insufficient_data",
    "readonly": true, "side_effects": [],
    "generated_at": "...", "asof": "YYYYMMDD" | null,
    "period": {"days_requested", "start", "end", "trading_days",
               "rows", "rows_out_of_window", "rows_missing_trade_date"},
    "inputs": {...}, "thresholds": {...}, "caliber": {...},
    "summary": {...}, "health_levels": {"green": n, ...},
    "strategies": [ {...周度 HealthCard + weekly.daily...}, ... ],
    "bias_tickets": [ {...BiasTicket draft...}, ... ],
    "notes": [...]
  }

逐策略周度条目 = strategy_health.HealthCard 全字段 +：
  · weekly：{rows, trading_days, first_date, last_date, daily[]}；
  · bias_ticket_ids：本周该策略的偏差票 id 列表。

BiasTicket（每周报版）在 strategy_health 最小 schema 上追加上游字段：
  {schema, ticket_id, strategy_id, bias_type, period, asof,
   owner_upstream, owner_source, evidence, evidence_refs,
   suggested_action, priority, status="draft", not_auto_apply=True}
  · owner_upstream：来自 Strategy Card 的 owner（缺失显式 null，不猜）；
  · evidence_refs：结构化证据引用（health_card / metric / regime_breakdown /
    strategy_card），便于下游机器回溯；
  · not_auto_apply 恒 true：只建议人工复核，绝不自动改权重/registry/交易。

用法：
  python strategy_health_report.py --rows-file rows.json --days 7 --out weekly.json
  python strategy_health_report.py --rows-file rows.json --days 7 --format md
  python strategy_health_report.py --rows-file rows.json --days 7 --format md --out weekly.md
  python strategy_health_report.py --rows-file rows.json --asof 20260130 \
      --start-date 20260124 --registry strategy_registry.json --min-samples 20
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta

try:  # 只读复用单次健康检查口径（HealthCard/BiasTicket 第一版）
    from strategy_layer import strategy_health as _health
except ImportError:  # 直接脚本运行时的路径兜底（同 strategy_health 判例）
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from strategy_layer import strategy_health as _health

SCHEMA = "strategy_health_weekly/v1"
TICKET_SCHEMA = "bias_ticket/v1"
DEFAULT_DAYS = 7
STATUS_OK = "OK"
STATUS_INSUFFICIENT = "insufficient_data"
RETURN_WINDOWS = ("t1", "t5")

# 上游 owner 来源：策略卡 owner（A0 归一工位名）；卡片缺失就不猜
OWNER_SOURCE_CARDS = "strategy_cards"

_NOTE_READONLY = "本模块只读：不自动改权重/registry、不下单、不写生产数据、不重启服务"


# ── 基础小工具 ────────────────────────────────────────────────────────
def _write_text_atomic(text: str, path: str) -> str:
    """文本原子写（K-STOCK-393 判例：tmp + os.replace，失败不留半文件）。"""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError as exc:  # 清理失败不掩盖主异常，只留告警
            logging.getLogger(__name__).warning(
                "silent-except %s: %s",
                "quant_backtest/strategy_layer/strategy_health_report.py",
                exc,
            )
        raise
    return path


def _write_json_atomic(doc: dict, path: str) -> str:
    return _write_text_atomic(json.dumps(doc, ensure_ascii=False, indent=2), path)


def _parse_date(value):
    """YYYYMMDD / YYYY-MM-DD → date；非法/缺失 → None（显式缺失，不猜）。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_str(value) -> str:
    return value.strftime("%Y%m%d")


def _norm_days(days) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("days 须为正整数: %r" % (days,)) from exc
    if value < 1:
        raise ValueError("days 须 >= 1: %r" % (days,))
    return value


def _window_stats(values) -> dict:
    """单窗口统计：{count, win_rate(%), avg_ret(%)}；空 → 显式 null。"""
    if not values:
        return {"count": 0, "win_rate": None, "avg_ret": None}
    count = len(values)
    wins = sum(1 for value in values if value > 0)
    win_rate = round(100.0 * wins / count, 1)
    avg_ret = round(sum(values) / count, 2)
    return {
        "count": count,
        "win_rate": 0.0 if win_rate == 0 else win_rate,
        "avg_ret": 0.0 if avg_ret == 0 else avg_ret,
    }


# ── 策略卡：注入 / JSON / 自动读 strategy_cards（不可用显式降级） ──────
def _import_strategy_cards():
    """延迟 import strategy_cards（Phase 0-A 交付；不可用由调用方降级）。"""
    from strategy_layer import strategy_cards

    return strategy_cards


def _registry_view_summary(reg_view) -> dict | None:
    """strategy_cards.registry_view() 的可序列化摘要（只读对账用）。"""
    if not isinstance(reg_view, dict):
        return None
    keys = (
        "total_cards",
        "enabled",
        "enabled_ids",
        "enabled_card_ids",
        "registry_only_enabled_ids",
        "shadow_candidates",
        "shadow_candidate_ids",
        "default_enable_new_strategies",
        "registry_semantics_unchanged",
        "card_ids",
        "weights",
        "top_n",
    )
    return {key: reg_view.get(key) for key in keys if key in reg_view}


def _unavailable_cards_view(error: str) -> dict:
    """显式 unavailable 视图（来源不可用≠没有卡，不臆造空卡集）。"""
    return {
        "available": False,
        "source": "unavailable",
        "path": None,
        "error": error,
        "cards": {},
        "enabled": None,
        "shadow_candidates": None,
        "duplicates": 0,
        "ignored": 0,
    }


def resolve_cards(cards=None, cards_file=None) -> tuple:
    """解析策略卡视图：返回 (cards_view, build_kwargs, 附带宽 registry 摘要)。

    build_kwargs 直接透传给 strategy_health.build_strategy_health
    （cards 与 cards_file 二选一，避免把文件路径当卡片列表传错）。

    - 注入 cards / cards_file：直接走 strategy_health.load_cards_view；
    - 都不给：尝试 strategy_cards.load_cards() + registry_view()，把卡列表 +
      enabled/shadow 组合成视图（不改任何文件）；
    - 不可用：显式 unavailable（error 带原因），不臆造卡片。
    """
    if cards is not None:
        view = _health.load_cards_view(cards=cards)
        return view, {"cards": cards, "cards_file": None}, None
    if cards_file:
        view = _health.load_cards_view(cards_file=cards_file)
        return view, {"cards": None, "cards_file": cards_file}, None
    try:
        module = _import_strategy_cards()
    except Exception as exc:  # noqa: BLE001 - 并行交付缺失即显式降级
        error = "strategy_cards 暂不可用（%s: %s）" % (type(exc).__name__, exc)
        return _unavailable_cards_view(error), {"cards": None, "cards_file": None}, None
    try:
        card_list = module.load_cards()
        reg_view = module.registry_view(card_list)
    except Exception as exc:  # noqa: BLE001
        error = "strategy_cards 读取失败（%s: %s）" % (type(exc).__name__, exc)
        return _unavailable_cards_view(error), {"cards": None, "cards_file": None}, None
    payload = {
        "cards": card_list,
        "enabled": reg_view.get("enabled_card_ids"),
        "shadow_candidates": reg_view.get("shadow_candidate_ids"),
    }
    view = _health.load_cards_view(cards=payload)
    if view.get("available"):
        view["source"] = "strategy_cards"
    return (
        view,
        {"cards": payload, "cards_file": None},
        _registry_view_summary(reg_view),
    )


def _owner_map(cards_view: dict) -> dict:
    """策略 → 上游 owner（仅策略卡给了 owner 才有；缺的不猜）。"""
    owners = {}
    for sid, card in (cards_view.get("cards") or {}).items():
        owner = card.get("owner")
        if owner:
            owners[sid] = owner
    return owners


# ── 窗口内按日聚合 ────────────────────────────────────────────────────
def _daily_breakdown(norm_rows: list, strategy_id: str) -> list:
    """窗口内该策略逐交易日聚合（样本/胜率/均收益/分歧，缺则显式 0/null）。"""
    per_day = {}
    for row in norm_rows:
        if strategy_id not in row["contributions"]:
            continue
        day = _parse_date(row["trade_date"])
        key = _date_str(day) if day else None
        bucket = per_day.setdefault(
            key, {"count": 0, "t1": [], "t5": [], "divergence": 0}
        )
        bucket["count"] += 1
        for window in RETURN_WINDOWS:
            if row["returns"][window] is not None:
                bucket[window].append(row["returns"][window])
        if row["divergence"]:
            bucket["divergence"] += 1
    daily = []
    for key in sorted(per_day, key=lambda item: (item is None, item or "")):
        bucket = per_day[key]
        daily.append(
            {
                "trade_date": key,
                "sample_count": bucket["count"],
                "t1": _window_stats(bucket["t1"]),
                "t5": _window_stats(bucket["t5"]),
                "divergence_count": bucket["divergence"],
            }
        )
    return daily


def _strategy_weekly_block(norm_rows: list, strategy_id: str) -> dict:
    daily = _daily_breakdown(norm_rows, strategy_id)
    dates = [item["trade_date"] for item in daily if item["trade_date"]]
    return {
        "rows": sum(item["sample_count"] for item in daily),
        "trading_days": len(dates),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "daily": daily,
    }


# ── BiasTicket：在 strategy_health 最小 schema 上追加“上游”字段 ──────
def _evidence_refs(strategy_entry: dict, period: dict) -> list:
    """结构化证据引用：下游可据此回查周报/健康卡/策略卡。"""
    sid = strategy_entry["strategy_id"]
    windows = strategy_entry["windows"]
    refs = [
        {
            "type": "health_card",
            "strategy_id": sid,
            "ref": "strategies[%s]" % sid,
            "period": {"start": period["start"], "end": period["end"]},
        },
        {
            "type": "metric",
            "strategy_id": sid,
            "field": "sample_count",
            "value": strategy_entry["sample_count"],
        },
        {
            "type": "metric",
            "strategy_id": sid,
            "field": "windows.t1.win_rate",
            "value": windows["t1"]["win_rate"],
        },
        {
            "type": "metric",
            "strategy_id": sid,
            "field": "windows.t5.avg_ret",
            "value": windows["t5"]["avg_ret"],
        },
    ]
    if strategy_entry["regime_status"] == "available":
        refs.append(
            {
                "type": "regime_breakdown",
                "strategy_id": sid,
                "regimes": sorted(strategy_entry["regime_breakdown"]),
            }
        )
    if strategy_entry["card"]["present"]:
        refs.append(
            {
                "type": "strategy_card",
                "strategy_id": sid,
                "source": strategy_entry["card"]["source"],
                "display_name": strategy_entry["card"]["display_name"],
                "owner": strategy_entry["card"]["owner"],
            }
        )
    return refs


def _weekly_ticket(
    base_ticket: dict,
    strategy_entry: dict,
    owners: dict,
    asof: str,
    period: dict,
) -> dict:
    sid = base_ticket["strategy_id"]
    owner = owners.get(sid)
    return {
        "schema": TICKET_SCHEMA,
        "ticket_id": "BTW-%s-%s-%s"
        % (asof or "na", sid, base_ticket["bias_type"]),
        "strategy_id": sid,
        "bias_type": base_ticket["bias_type"],
        "period": {"start": period["start"], "end": period["end"]},
        "asof": asof,
        "owner_upstream": owner,
        "owner_source": OWNER_SOURCE_CARDS if owner is not None else None,
        "evidence": list(base_ticket.get("evidence") or []),
        "evidence_refs": _evidence_refs(strategy_entry, period),
        "suggested_action": base_ticket.get("suggested_action"),
        "priority": base_ticket.get("priority"),
        "status": "draft",
        "not_auto_apply": True,
    }


# ── 主入口：build_weekly_health_report ───────────────────────────────
def build_weekly_health_report(
    rows=None,
    rows_file=None,
    rows_source=None,
    cards=None,
    cards_file=None,
    registry=None,
    registry_path=None,
    days=DEFAULT_DAYS,
    asof=None,
    start_date=None,
    end_date=None,
    equity_curves=None,
    costs=None,
    thresholds=None,
    out_path=None,
) -> dict:
    """产出周度 Strategy Health 报告（只读；可选原子落盘 JSON）。

    Parameters
    ----------
    rows / rows_file : list[dict] | str | None
        多日后验行（契约同 strategy_health：trade_date/symbol/contributions/
        divergence/ret_t1/ret_t5/regime/turnover/cost）。
    days : int
        自然日窗口长度（默认 7）：窗口 = [asof-(days-1), asof]。
    asof : str | None
        窗口锚点日（YYYYMMDD / YYYY-MM-DD）；缺省 = 全部输入行的最大 trade_date。
    start_date / end_date : str | None
        显式窗口覆盖（YYYYMMDD / YYYY-MM-DD）；start_date 缺省用 days 推算。
    cards / cards_file / registry / registry_path / equity_curves / costs /
    thresholds : 透传视图/口径（见模块 docstring）。
    out_path : str | None
        给出则原子写 JSON 产出档（唯一允许的落盘路径）。
    """
    days = _norm_days(days)
    if rows is None:
        if rows_file:
            rows = _health.load_rows_file(rows_file)
            if rows_source is None:
                rows_source = "rows_file:%s" % os.path.abspath(rows_file)
        else:
            rows = []
            if rows_source is None:
                rows_source = "empty"
    elif rows_source is None:
        rows_source = "injected"
    if not isinstance(rows, (list, tuple)):
        raise ValueError("rows 须为行 dict 列表: %s" % type(rows).__name__)

    norm_rows, invalid_rows = _health.normalize_rows(rows)

    # 锚点/窗口：显式 asof/end_date 优先，否则取输入行最大 trade_date
    input_dates = sorted(
        date for date in (_parse_date(row["trade_date"]) for row in norm_rows) if date
    )
    global_asof = input_dates[-1] if input_dates else None
    explicit_asof = None if asof is None else _parse_date(asof)
    if asof is not None and explicit_asof is None:
        raise ValueError("asof 须为 YYYYMMDD / YYYY-MM-DD: %r" % (asof,))
    explicit_end = None if end_date is None else _parse_date(end_date)
    if end_date is not None and explicit_end is None:
        raise ValueError("end_date 须为 YYYYMMDD / YYYY-MM-DD: %r" % (end_date,))
    window_end = explicit_end or explicit_asof or global_asof
    if start_date is not None:
        window_start = _parse_date(start_date)
        if window_start is None:
            raise ValueError("start_date 须为 YYYYMMDD / YYYY-MM-DD: %r" % (start_date,))
    elif window_end is not None:
        window_start = window_end - timedelta(days=days - 1)
    else:
        window_start = None
    if window_start is not None and window_end is not None and window_start > window_end:
        raise ValueError(
            "窗口非法：start=%s > end=%s"
            % (_date_str(window_start), _date_str(window_end))
        )

    window_rows = []
    out_of_window = 0
    missing_trade_date = 0
    for row in norm_rows:
        if not row["trade_date"]:
            missing_trade_date += 1
            continue
        day = _parse_date(row["trade_date"])
        if day is None:
            missing_trade_date += 1
            continue
        if window_start is None or window_end is None:
            window_rows.append(row)
        elif window_start <= day <= window_end:
            window_rows.append(row)
        else:
            out_of_window += 1

    window_dates = sorted(
        {
            _date_str(day)
            for day in (_parse_date(row["trade_date"]) for row in window_rows)
            if day
        }
    )

    # 策略卡 / registry 视图（只读；不可用显式降级）
    cards_view, build_cards, cards_registry_view = resolve_cards(
        cards=cards, cards_file=cards_file
    )
    owners = _owner_map(cards_view)

    # 复用单次健康检查：窗口内行 → 每策略 HealthCard + 基础 BiasTicket
    health_doc = _health.build_strategy_health(
        rows=window_rows,
        cards=build_cards["cards"],
        cards_file=build_cards["cards_file"],
        registry=registry,
        registry_path=registry_path,
        equity_curves=equity_curves,
        costs=costs,
        thresholds=thresholds,
    )
    health_cards = health_doc["strategies"]

    asof_str = _date_str(window_end) if window_end is not None else ""
    period = {
        "days_requested": days if start_date is None else None,
        "start": _date_str(window_start) if window_start is not None else None,
        "end": asof_str or None,
        "trading_days": len(window_dates),
        "rows": len(window_rows),
        "rows_out_of_window": out_of_window,
        "rows_missing_trade_date": missing_trade_date,
    }

    # 逐策略周度条目（HealthCard 全字段 + weekly.daily）
    strategies = []
    entries_by_sid = {}
    for sid in sorted(health_cards):
        health_card = health_cards[sid]
        entry = {
            "strategy_id": sid,
            "health_level": health_card["health_level"],
            "health_reasons": health_card["health_reasons"],
            "sample_count": health_card["sample_count"],
            "windows": health_card["windows"],
            "max_drawdown": health_card["max_drawdown"],
            "turnover": health_card["turnover"],
            "cost": health_card["cost"],
            "divergence_count": health_card["divergence_count"],
            "divergence_rate": health_card["divergence_rate"],
            "regime_breakdown": health_card["regime_breakdown"],
            "regime_status": health_card["regime_status"],
            "concentration": health_card["concentration"],
            "data_quality": health_card["data_quality"],
            "card": health_card["card"],
            "registry": health_card["registry"],
            "discovered_from": health_card["discovered_from"],
            "weekly": _strategy_weekly_block(window_rows, sid),
            "bias_ticket_ids": [],
        }
        entries_by_sid[sid] = entry
        strategies.append(entry)

    # 偏差票：有样本的策略全发；无样本策略仅 registry/卡片 enabled 时发 insufficient
    tickets = []
    for base_ticket in health_doc["bias_tickets"]:
        sid = base_ticket["strategy_id"]
        entry = entries_by_sid.get(sid)
        if entry is None:
            continue
        enabled = entry["registry"]["enabled"] is True or entry["card"].get("enabled") is True
        if entry["sample_count"] <= 0 and not enabled:
            continue
        ticket = _weekly_ticket(base_ticket, entry, owners, asof_str, period)
        tickets.append(ticket)
        entry["bias_ticket_ids"].append(ticket["ticket_id"])

    level_counts = {
        level: sum(1 for entry in strategies if entry["health_level"] == level)
        for level in _health.HEALTH_LEVELS
    }
    priority_counts = {"P1": 0, "P2": 0}
    type_counts = {}
    for ticket in tickets:
        if ticket["priority"] in priority_counts:
            priority_counts[ticket["priority"]] += 1
        type_counts[ticket["bias_type"]] = type_counts.get(ticket["bias_type"], 0) + 1
    owner_counts = {}
    for ticket in tickets:
        owner = ticket["owner_upstream"] or "(unknown)"
        owner_counts[owner] = owner_counts.get(owner, 0) + 1

    summary = {
        "strategies": len(strategies),
        "strategies_with_samples": sum(1 for e in strategies if e["sample_count"] > 0),
        "strategies_without_samples": sum(
            1 for e in strategies if e["sample_count"] <= 0
        ),
        "bias_tickets": len(tickets),
        "tickets_by_priority": priority_counts,
        "tickets_by_type": type_counts,
        "upstream_owners": owner_counts,
        "rows": len(window_rows),
        "rows_input": len(norm_rows),
        "rows_invalid": invalid_rows,
        "trading_days": len(window_dates),
    }

    doc = {
        "schema": SCHEMA,
        "status": STATUS_OK if window_rows else STATUS_INSUFFICIENT,
        "readonly": True,
        "side_effects": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": asof_str or None,
        "period": period,
        "inputs": {
            "rows": {
                "source": rows_source,
                "count_input": len(norm_rows),
                "rows_in_window": len(window_rows),
                "invalid_rows": invalid_rows,
                "out_of_window": out_of_window,
                "missing_trade_date": missing_trade_date,
            },
            "cards": {
                "source": cards_view["source"],
                "path": cards_view["path"],
                "available": cards_view["available"],
                "error": cards_view["error"],
                "count": len(cards_view["cards"]),
                "duplicates": cards_view["duplicates"],
                "ignored": cards_view["ignored"],
                "enabled": cards_view["enabled"],
                "shadow_candidates": cards_view["shadow_candidates"],
                "registry_view": cards_registry_view,
            },
            "registry": health_doc["inputs"]["registry"],
            "equity_curves": health_doc["inputs"]["equity_curves"],
            "costs": health_doc["inputs"]["costs"],
        },
        "thresholds": health_doc["thresholds"],
        "caliber": {
            "period": "自然日窗口 [asof-(days-1), asof]；交易日 = 窗口内实际出现的"
            " trade_date 数（不自带交易日历，不猜交易日）",
            "returns": "百分数（T+N 收益，1.23 表示 +1.23%）",
            "win_rate": "收益 > 0 占比（%），分母为该窗口收益可算样本",
            "sample_count": "窗口内 contributions 出现该策略的有效行数（多源行跨桶）",
            "max_drawdown": "峰谷回撤百分数（负数）；仅注入权益曲线时算，否则 null",
            "turnover_cost": "行级 turnover/cost 或 costs 注入；单位未给则 unit=null",
            "health_level": "沿用 strategy_health 阈值：T+1 主口径，覆盖不足回退 T+5",
            "bias_ticket": "status 恒 draft 且 not_auto_apply=true；只建议人工复核，"
            "不自动改权重/registry/交易",
            "ticket_scope": "有样本策略全发；无样本策略仅 enabled（registry/卡片）"
            "发 insufficient，shadow/未启用策略只列卡不出票",
        },
        "summary": summary,
        "health_levels": level_counts,
        "strategies": strategies,
        "bias_tickets": tickets,
        "notes": [
            _NOTE_READONLY,
            "缺数据一律显式 insufficient / null：无权益曲线不算最大回撤、"
            "无成本口径不猜单位、无 regime 标签不做分层结论",
            "无行/窗口为空时 status=insufficient_data，不把空周报当 0 收益",
            "上游 owner 取 Strategy Card.owner；卡片缺失或未登记 → "
            "owner_upstream=null（不猜工位）",
        ],
    }
    if out_path:
        doc["out_path"] = _write_json_atomic(doc, out_path)
    return doc


# ── Markdown 渲染 ─────────────────────────────────────────────────────
def _fmt(value, digits=2):
    if value is None:
        return "—"
    return ("%%.%df" % digits) % float(value)


def _fmt_pct(value):
    if value is None:
        return "—"
    return "%.1f%%" % float(value)


def render_markdown(doc: dict) -> str:
    """周报 → Markdown（决策台/上游回执可直接贴）。"""
    period = doc["period"]
    summary = doc["summary"]
    lines = [
        "# 策略健康周报 · Weekly Strategy Health",
        "",
        "- 周期：%s ~ %s（请求 %s 天 / 实际 %d 交易日 / %d 行）"
        % (
            period["start"] or "—",
            period["end"] or "—",
            period["days_requested"] if period["days_requested"] is not None else "—",
            period["trading_days"],
            period["rows"],
        ),
        "- asof：%s｜生成：%s｜schema：%s"
        % (doc["asof"] or "—", doc["generated_at"], doc["schema"]),
        "- 状态：%s｜只读：是（不自动改权重/registry、不下单、无副作用）"
        % doc["status"],
        "",
        "## 一、健康总览",
        "",
        "| 等级 | 策略数 |",
        "|---|---|",
    ]
    for level in _health.HEALTH_LEVELS:
        lines.append("| %s | %d |" % (level, doc["health_levels"][level]))
    lines += [
        "",
        "- 策略 %d 个（有样本 %d / 无样本 %d）｜偏差票 %d 张"
        "（P1 %d / P2 %d）"
        % (
            summary["strategies"],
            summary["strategies_with_samples"],
            summary["strategies_without_samples"],
            summary["bias_tickets"],
            summary["tickets_by_priority"]["P1"],
            summary["tickets_by_priority"]["P2"],
        ),
        "- 上游 owner 分布：%s"
        % (
            "，".join(
                "%s=%d" % (owner, count)
                for owner, count in sorted(summary["upstream_owners"].items())
            )
            or "（无偏差票）"
        ),
        "",
        "## 二、策略周度明细",
        "",
        "| 策略 | 等级 | 样本 | 交易日 | T+1 胜率 | T+1 均收益 | T+5 胜率 "
        "| T+5 均收益 | 最大回撤 | 分歧率 | 偏差票 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in doc["strategies"]:
        windows = entry["windows"]
        lines.append(
            "| %s | %s | %d | %d | %s | %s | %s | %s | %s | %s | %s |"
            % (
                entry["strategy_id"],
                entry["health_level"],
                entry["sample_count"],
                entry["weekly"]["trading_days"],
                _fmt_pct(windows["t1"]["win_rate"]),
                _fmt(windows["t1"]["avg_ret"]),
                _fmt_pct(windows["t5"]["win_rate"]),
                _fmt(windows["t5"]["avg_ret"]),
                _fmt(entry["max_drawdown"]["max_drawdown"]),
                _fmt_pct(entry["divergence_rate"]),
                ", ".join(entry["bias_ticket_ids"]) or "—",
            )
        )

    lines += ["", "## 三、健康原因（非 green）", ""]
    flagged = [entry for entry in doc["strategies"] if entry["health_level"] != "green"]
    if not flagged:
        lines.append("（无：全部策略为 green）")
    for entry in flagged:
        lines.append(
            "- **%s**（%s）：%s"
            % (
                entry["strategy_id"],
                entry["health_level"],
                "；".join(entry["health_reasons"]),
            )
        )

    lines += ["", "## 四、上游 Bias Ticket（draft / not_auto_apply）", ""]
    if not doc["bias_tickets"]:
        lines.append("（无：本周未命中偏差规则）")
    else:
        lines += [
            "| 票号 | 策略 | 上游 owner | 类型 | 优先级 | 证据 | 建议动作 |",
            "|---|---|---|---|---|---|---|",
        ]
        for ticket in doc["bias_tickets"]:
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s |"
                % (
                    ticket["ticket_id"],
                    ticket["strategy_id"],
                    ticket["owner_upstream"] or "—",
                    ticket["bias_type"],
                    ticket["priority"],
                    "；".join(ticket["evidence"]),
                    ticket["suggested_action"],
                )
            )

    lines += ["", "## 五、regime 分层（有标签才算）", ""]
    regime_lines = []
    for entry in doc["strategies"]:
        if entry["regime_status"] != "available":
            continue
        regime_lines.append("### %s" % entry["strategy_id"])
        regime_lines += [
            "| regime | 样本 | T+1 胜率 | 平均 T+1 | T+5 胜率 | 平均 T+5 |",
            "|---|---|---|---|---|---|",
        ]
        for regime in sorted(entry["regime_breakdown"]):
            block = entry["regime_breakdown"][regime]
            regime_lines.append(
                "| %s | %d | %s | %s | %s | %s |"
                % (
                    regime,
                    block["count"],
                    _fmt_pct(block["t1"]["win_rate"]),
                    _fmt(block["t1"]["avg_ret"]),
                    _fmt_pct(block["t5"]["win_rate"]),
                    _fmt(block["t5"]["avg_ret"]),
                )
            )
    lines += regime_lines or ["（无：行未携带 regime 标签，显式不做分层结论）"]

    lines += ["", "## 六、口径与数据缺口", ""]
    registry = doc["inputs"]["registry"]
    cards = doc["inputs"]["cards"]
    lines += [
        "- 窗口：%s；交易日按实际出现 trade_date 计（无交易日历，不猜）"
        % doc["caliber"]["period"],
        "- 行：窗口内 %d / 输入 %d（窗口外 %d，缺 trade_date %d，无效 %d）"
        % (
            doc["period"]["rows"],
            doc["inputs"]["rows"]["count_input"],
            doc["period"]["rows_out_of_window"],
            doc["period"]["rows_missing_trade_date"],
            doc["inputs"]["rows"]["invalid_rows"],
        ),
        "- registry：%s（enabled=%s）"
        % (
            "available" if registry["available"] else "unavailable",
            registry["enabled"],
        ),
        "- 策略卡：%s（count=%s）"
        % ("available" if cards["available"] else "unavailable", cards["count"]),
        "- 收益单位：百分数；最大回撤/换手/成本缺失时显式 null（不造数）",
        "- BiasTicket status 恒 draft、not_auto_apply=true：只建议上游人工复核，"
        "不自动改权重/registry/交易",
    ]
    return "\n".join(lines)


# ── CLI（--rows-file / --days / --format / --out 等；只读） ──────────
USAGE = """用法：
  python strategy_health_report.py [--rows-file F] [--days N]
      [--format json|md] [--out F]
      [--asof D] [--start-date D] [--end-date D]
      [--cards-file F] [--registry F] [--min-samples N] [--limit N]

只读：多日后验行 → 周度健康报告 JSON/MD；不写 PG、不改 registry/权重、
不下单、不新增任务。不给 --out 时输出到 stdout；--out 为原子写。
"""

_VALUE_FLAGS = (
    "--rows-file",
    "--days",
    "--format",
    "--out",
    "--asof",
    "--start-date",
    "--end-date",
    "--cards-file",
    "--registry",
    "--min-samples",
    "--limit",
)


def _cli_summary(doc: dict) -> str:
    summary = doc["summary"]
    return (
        "[strategy_health_report] period=%s~%s days=%d strategies=%d green=%d "
        "yellow=%d red=%d insufficient=%d tickets=%d asof=%s"
        % (
            doc["period"]["start"] or "-",
            doc["period"]["end"] or "-",
            doc["period"]["trading_days"],
            summary["strategies"],
            doc["health_levels"]["green"],
            doc["health_levels"]["yellow"],
            doc["health_levels"]["red"],
            doc["health_levels"]["insufficient"],
            summary["bias_tickets"],
            doc["asof"] or "-",
        )
    )


def main(argv=None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    options = {
        "rows_file": None,
        "days": DEFAULT_DAYS,
        "format": "json",
        "out": None,
        "asof": None,
        "start_date": None,
        "end_date": None,
        "cards_file": None,
        "registry": None,
        "min_samples": None,
        "limit": None,
    }
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg not in _VALUE_FLAGS:
            print("[strategy_health_report] 未知参数: %s" % arg, file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return 2
        if index + 1 >= len(argv):
            print("[strategy_health_report] 参数 %s 缺值" % arg, file=sys.stderr)
            return 2
        value = argv[index + 1]
        index += 2
        if arg == "--rows-file":
            options["rows_file"] = value
        elif arg == "--days":
            try:
                options["days"] = int(value)
            except ValueError:
                print("[strategy_health_report] --days 须为整数: %r" % value, file=sys.stderr)
                return 2
        elif arg == "--format":
            options["format"] = value
        elif arg == "--out":
            options["out"] = value
        elif arg == "--asof":
            options["asof"] = value
        elif arg == "--start-date":
            options["start_date"] = value
        elif arg == "--end-date":
            options["end_date"] = value
        elif arg == "--cards-file":
            options["cards_file"] = value
        elif arg == "--registry":
            options["registry"] = value
        elif arg == "--min-samples":
            try:
                options["min_samples"] = int(value)
            except ValueError:
                print(
                    "[strategy_health_report] --min-samples 须为整数: %r" % value,
                    file=sys.stderr,
                )
                return 2
        elif arg == "--limit":
            try:
                options["limit"] = int(value)
            except ValueError:
                print("[strategy_health_report] --limit 须为整数: %r" % value, file=sys.stderr)
                return 2
    if options["format"] not in ("json", "md"):
        print(
            "[strategy_health_report] --format 只认 json/md: %r" % options["format"],
            file=sys.stderr,
        )
        return 2

    rows = None
    rows_source = None
    if options["rows_file"]:
        try:
            rows = _health.load_rows_file(options["rows_file"])
        except (OSError, ValueError) as exc:
            print("[strategy_health_report] rows-file 读取失败: %s" % exc, file=sys.stderr)
            return 2
        rows_source = "rows_file:%s" % os.path.abspath(options["rows_file"])
    if options["limit"] is not None and rows is not None:
        rows = rows[: options["limit"]]
        rows_source = "%s#limit=%d" % (rows_source, options["limit"])

    thresholds = {}
    if options["min_samples"] is not None:
        thresholds["min_samples"] = options["min_samples"]

    try:
        doc = build_weekly_health_report(
            rows=rows,
            rows_source=rows_source,
            cards_file=options["cards_file"],
            registry_path=options["registry"],
            days=options["days"],
            asof=options["asof"],
            start_date=options["start_date"],
            end_date=options["end_date"],
            thresholds=thresholds,
            out_path=None if options["format"] == "md" else options["out"],
        )
    except ValueError as exc:
        print("[strategy_health_report] 参数/输入非法: %s" % exc, file=sys.stderr)
        return 2

    if options["format"] == "md":
        text = render_markdown(doc)
        if options["out"]:
            path = _write_text_atomic(text, options["out"])
            print(_cli_summary(doc) + " → " + path)
        else:
            print(text)
        return 0
    if options["out"]:
        print(_cli_summary(doc) + " → " + doc.get("out_path", ""))
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "DEFAULT_DAYS",
    "SCHEMA",
    "TICKET_SCHEMA",
    "build_weekly_health_report",
    "main",
    "render_markdown",
    "resolve_cards",
]


if __name__ == "__main__":
    raise SystemExit(main())
