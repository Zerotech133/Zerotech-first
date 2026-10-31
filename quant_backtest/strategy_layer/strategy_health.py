# -*- coding: utf-8 -*-
"""Strategy Health / Bias Ticket 第一版（WO-STRAT-HEALTH-20261031，Phase 0-B）。

只读健康检查工具：吃「融合信号后验行」（可注入 list / JSON 文件）＋ 可选
策略卡视图（strategy_cards）/ registry 视图（strategy_registry.json），
为每个策略产出 HealthCard 与偏差票（BiasTicket draft），让上游（鲁肃）
能反向回答「哪些策略有偏差、该怎么调」。

本模块**只读**（硬约束）：
  · 不写 PG / 不写生产数据（落盘仅限调用方显式给的 --out / out_path）；
  · 不改 strategy_registry.json / strategy_cards.json 的权重或启用态；
  · 不自动调权、不自动改 registry、不新增计划任务、不重启服务；
  · 缺数据一律显式 null / insufficient，不造数（同 combined_posterior、
    k_stock/paper/combo_review 的「不造数」判例）。

输入行（rows）最小契约（其余字段可缺，缺即显式 null）::

    {
      "trade_date": "20260105",       # 或 symbol 至少给一个
      "symbol": "600000.SH",
      "contributions": {"kg_boost": {...}},  # 必填非空（融合行同构）
      "divergence": false,
      "ret_t1": 1.23,                 # T+1 收益（百分数，+1.23%），或 returns.t1
      "ret_t5": 2.34,                 # T+5 收益，或 returns.t5
      "regime": "bull",               # 可选：regime 分层标签
      "turnover": 0.8,                # 可选：行级换手（单位由来源给）
      "cost": 1.5                     # 可选：行级成本（单位由来源给）
    }

可选注入：
  · equity_curves={sid: [equity, ...]}（或 {"equity": [...], "source": ...}）
      有权益曲线才算最大回撤，否则显式 null + status=not_provided；
  · costs={sid: {"cost_ratio": .., "turnover_mean": .., "cost_total": ..,
                 "cost_per_row": .., "unit": "CNY", "source": "..."}}
      有才算换手/成本，否则显式 null；
  · cards=[...] / cards_file=...（strategy_cards 视图）；不传则尝试
      import strategy_layer.strategy_cards，不可用显式 unavailable 不臆造；
  · registry={...} / registry_path=...（默认 strategy_registry.json）。

HealthCard 口径（全部只读统计）：
  · sample_count：该策略在 contributions 中出现的有效行数（多源行跨桶计数，
    与 combined_posterior.per_strategy 同口径）；
  · windows.t1 / windows.t5：{count, win_rate(%), avg_ret(%)}；胜率 = 收益 > 0
    占比，分母为该窗口收益可算的样本；
  · max_drawdown：仅当注入权益曲线且点数 >= 2、峰值恒正时计算（峰谷回撤
    百分数，负数），否则 null + status=not_provided/insufficient；
  · turnover / cost：仅当行级字段或 costs 注入存在才算；行级口径 unit 恒 null
    （来源没给单位就不猜）；
  · divergence_rate：该策略参与行中 divergence=true 的占比（%）；
  · regime_breakdown：按 row.regime 分层，无标签则 regime_status=insufficient；
  · health_level：green / yellow / red / insufficient（阈值见
    DEFAULT_THRESHOLDS，可经 thresholds 覆盖；T+1 覆盖不足时回退 T+5 评级，
    两窗口都不足 → insufficient）。

BiasTicket 最小 schema（全部 status=draft，只建议人工复核，永不自动生效）::

    {
      "schema": "bias_ticket/v1",
      "ticket_id": "BT-<strategy>-<bias_type>",
      "strategy_id": "...",
      "bias_type": "regime|overfit|cost|execution|concentration|data|insufficient",
      "evidence": ["...", ...],
      "suggested_action": "...",
      "priority": "P1|P2",
      "status": "draft",
      "asof": "YYYYMMDD"
    }

用法::

    python strategy_health.py --rows-file rows.json --out health.json
    python strategy_health.py --rows-file rows.json --format md
    python strategy_health.py --rows-file rows.json --format md --out health.md
    python strategy_health.py --rows-file rows.json --cards-file cards.json \
        --registry strategy_registry.json --min-samples 20 --format json
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from datetime import datetime

try:  # 只读复用后验层的权重分带口径（>=0.5 full / 0-0.5 shadow / 0 off）
    from strategy_layer import combined_posterior as _combined_posterior
except ImportError:  # 直接脚本运行时的路径兜底（同 combined_posterior 判例）
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from strategy_layer import combined_posterior as _combined_posterior

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, "strategy_registry.json")

SCHEMA = "strategy_health/v1"
BIAS_SCHEMA = "bias_ticket/v1"
STATUS_DRAFT = "draft"

HEALTH_LEVELS = ("green", "yellow", "red", "insufficient")
BIAS_TYPES = (
    "regime",
    "overfit",
    "cost",
    "execution",
    "concentration",
    "data",
    "insufficient",
)
PRIORITIES = ("P1", "P2")  # P1=先补数/先复核；P2=优化项
RETURN_WINDOWS = ("t1", "t5")

# 健康等级/偏差票阈值（可经 thresholds 覆盖；未知键显式 ValueError，不静默吞）
DEFAULT_THRESHOLDS = {
    "min_samples": 20,  # 样本数 < 门槛 → insufficient
    "min_return_coverage": 0.5,  # 收益覆盖率 < 门槛 → 该窗口不可评级
    "green_win_rate": 50.0,  # 胜率 >= 此值且其余达标才是 green
    "red_win_rate": 40.0,  # 胜率 < 此值 → red
    "red_avg_ret": -1.0,  # 平均收益 < 此值（%）→ red
    "yellow_max_drawdown": -10.0,  # 最大回撤 <= 此值（%）→ yellow
    "red_max_drawdown": -20.0,  # 最大回撤 <= 此值（%）→ red
    "divergence_rate_warn": 20.0,  # 分歧率 >= 此值（%）→ yellow
    "divergence_rate_red": 50.0,  # 分歧率 >= 此值（%）→ red
    "regime_min_samples": 10,  # regime 分桶参与胜率差判定的最小样本
    "regime_gap_pp": 30.0,  # regime 分层胜率差 >= 此值（百分点）→ 偏差票
    "overfit_t1_win_rate": 55.0,  # T+1 胜率 >= 此值且 T+5 平均收益过低 → 过拟合票
    "overfit_t5_avg_ret": -1.0,  # T+5 平均收益 <= 此值（%）
    "cost_ratio_high": 0.30,  # 成本/收益比 >= 此值 → 成本偏差票
    "turnover_high": 3.0,  # 行级平均换手 >= 此值 → 成本偏差票
    "concentration_top_symbol_share": 0.30,  # 单标的最大样本占比 >= 此值 → 集中度票
    "min_cost_rows": 10,  # 行级换手/成本参与判定的最小覆盖行数
}

_NOTE_READONLY = "本模块只读：不自动改权重/registry、不写生产数据、不重启服务"


# ── 数值小工具 ────────────────────────────────────────────────────────
def _num(value):
    """转有限 float；非法/NaN/inf → None（显式缺失，不填 0 充数）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _round(value, ndigits=2):
    """四舍五入且消除 -0.0（口径稳定，便于 JSON/MD 对照）。"""
    r = round(float(value), ndigits)
    return 0.0 if r == 0 else r


def _ratio(part, whole, ndigits=4):
    """比例 = part/whole；分母为 0 → None（显式缺失）。"""
    if not whole:
        return None
    return _round(float(part) / float(whole), ndigits)


def _pct(part, whole, ndigits=1):
    """百分数 = 100*part/whole；分母为 0 → None（显式缺失）。"""
    if not whole:
        return None
    return _round(100.0 * float(part) / float(whole), ndigits)


def _norm_str(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_str_list(value):
    """字符串列表归一；非列表 → None（缺失≠空列表，不造数）。"""
    if not isinstance(value, (list, tuple)):
        return None
    out = []
    for item in value:
        text = _norm_str(item)
        if text is not None:
            out.append(text)
    return out


def _norm_int(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v


def merge_thresholds(overrides=None) -> dict:
    """合并阈值覆盖表；未知键/非法值 → ValueError（fail-visible）。"""
    th = dict(DEFAULT_THRESHOLDS)
    if overrides is None:
        return th
    if not isinstance(overrides, dict):
        raise ValueError("thresholds 须为 dict 或 None: %r" % (type(overrides).__name__,))
    unknown = sorted(set(overrides) - set(DEFAULT_THRESHOLDS))
    if unknown:
        raise ValueError("未知阈值键: %s" % ", ".join(unknown))
    for key, raw in overrides.items():
        value = _num(raw)
        if value is None:
            raise ValueError("阈值 %s 须为有限数值: %r" % (key, raw))
        th[key] = value
    return th


# ── 原子写（K-STOCK-393 判例：tmp + os.replace，失败不留半文件） ────────
def _write_text_atomic(text: str, path: str) -> str:
    """文本原子写：先写 tmp 再 os.replace；异常清理 tmp 后原样抛出。"""
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
                "silent-except %s: %s", "quant_backtest/strategy_layer/strategy_health.py", exc
            )
        raise
    return path


def _write_json_atomic(doc: dict, path: str) -> str:
    """产出档原子写（本模块唯一允许的落盘路径，由调用方显式给出）。"""
    return _write_text_atomic(json.dumps(doc, ensure_ascii=False, indent=2), path)


# ── 行归一化（不造数：非法行显式计数，不静默补值） ────────────────────
def load_rows_file(path: str) -> list:
    """离线回放：JSON 行列表（或 {"rows": [...]}）；结构非法显式抛错。

    编码用 utf-8-sig：兼容 Windows 工具链（PowerShell Set-Content）写出的 BOM。
    """
    with open(path, "r", encoding="utf-8-sig") as fh:
        doc = json.load(fh)
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise ValueError("rows-file 须为行 dict 列表（或 {rows: [...]}）: %s" % path)
    return rows


def _coerce_contributions(raw):
    """contributions 归一：JSON 串双形态 → dict；只保留值为 dict 的条目。"""
    contribs = raw
    if isinstance(contribs, (str, bytes)):
        try:
            contribs = json.loads(contribs)
        except ValueError:
            contribs = {}
    if not isinstance(contribs, dict):
        return {}
    out = {}
    for sid, item in contribs.items():
        key = _norm_str(sid)
        if key is None or not isinstance(item, dict):
            continue
        out[key] = item
    return out


def row_returns(row: dict) -> dict:
    """行级 T+1/T+5 收益（%）：ret_t1/ret_t5 优先，其次 returns/ret 嵌套。"""
    nested = row.get("returns")
    if nested is None:
        nested = row.get("ret")
    if isinstance(nested, (str, bytes)):
        try:
            nested = json.loads(nested)
        except ValueError:
            nested = {}
    if not isinstance(nested, dict):
        nested = {}
    out = {}
    for window in RETURN_WINDOWS:
        raw = row.get("ret_%s" % window)
        if raw is None:
            aliases = nested.get(window)
            if aliases is None:
                aliases = nested.get("%s_ret" % window)
            raw = aliases
        out[window] = _num(raw)
    return out


def _row_regime(row: dict):
    raw = row.get("regime")
    if isinstance(raw, dict):
        raw = raw.get("label") or raw.get("regime") or raw.get("tag")
    return _norm_str(raw)


def normalize_rows(rows) -> tuple:
    """行归一化：返回 (有效行, 无效行数)。

    有效行 = dict 且 contributions 归一后非空、trade_date/symbol 至少给一个。
    无效行显式计数（不静默补 trade_date/symbol，不造数）。
    """
    norm = []
    invalid = 0
    for raw in rows or []:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        trade_date = _norm_str(raw.get("trade_date"))
        symbol = _norm_str(raw.get("symbol"))
        if symbol is not None:
            symbol = symbol.upper()
        contribs = _coerce_contributions(raw.get("contributions"))
        if not contribs or (trade_date is None and symbol is None):
            invalid += 1
            continue
        norm.append(
            {
                "trade_date": trade_date or "",
                "symbol": symbol or "",
                "contributions": contribs,
                "divergence": bool(raw.get("divergence")),
                "returns": row_returns(raw),
                "regime": _row_regime(raw),
                "turnover": _num(raw.get("turnover")),
                "cost": _num(raw.get("cost")),
            }
        )
    return norm, invalid


# ── registry / 策略卡视图（只读；不可用显式 unavailable） ──────────────
def load_registry_view(registry=None, registry_path=None) -> dict:
    """registry 视图：enabled / weights / top_n（键缺失 → None，缺≠空）。"""
    doc = None
    error = None
    path = None
    if registry is not None:
        source = "injected"
        if not isinstance(registry, dict):
            error = "injected registry 须为 dict: %s" % type(registry).__name__
    else:
        source = "file"
        path = os.path.abspath(registry_path or REGISTRY_PATH)
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
    if registry is not None and isinstance(registry, dict):
        doc = registry
    if not isinstance(doc, dict):
        doc = {}

    enabled = _norm_str_list(doc.get("enabled")) if "enabled" in doc else None
    weights = None
    invalid_weights = 0
    if isinstance(doc.get("weights"), dict):
        weights = {}
        for sid, value in doc["weights"].items():
            key = _norm_str(sid)
            weight = _num(value)
            if key is None or weight is None:
                invalid_weights += 1
                continue
            weights[key] = weight
    top_n = None
    if isinstance(doc.get("top_n"), dict):
        top_n = {}
        for sid, value in doc["top_n"].items():
            key = _norm_str(sid)
            n = _norm_int(value)
            if key is None or n is None:
                continue
            top_n[key] = n
    return {
        "available": error is None,
        "source": source,
        "path": path,
        "error": error,
        "enabled": enabled,
        "weights": weights,
        "top_n": top_n,
        "invalid_weights": invalid_weights,
    }


def _import_strategy_cards():
    """延迟 import strategy_cards（Phase 0-A 并行交付；不可用由调用方降级）。"""
    from strategy_layer import strategy_cards

    return strategy_cards


def _normalize_card(sid: str, raw: dict) -> dict:
    """单张策略卡归一（只挑健康检查用得上的只读字段，其余留在 fields）。"""
    return {
        "strategy_id": sid,
        "display_name": _norm_str(
            raw.get("display_name") or raw.get("name") or raw.get("title")
        ),
        "owner": _norm_str(raw.get("owner")),
        "version": _norm_str(raw.get("version") or raw.get("card_version")),
        "horizon_days": _norm_int(raw.get("horizon_days") or raw.get("horizon")),
        "regime_tags": _norm_str_list(raw.get("regime_tags") or raw.get("regimes")),
        "known_failure_modes": _norm_str_list(
            raw.get("known_failure_modes") or raw.get("failure_modes")
        ),
        "modes": _norm_str_list(raw.get("modes")),
        "enabled": raw.get("enabled") if isinstance(raw.get("enabled"), bool) else None,
        "fields": sorted(str(key) for key in raw),
    }


def _extract_cards(obj):
    """从 strategy_cards 返回值提取卡列表（list / {"cards": [...]} / {sid: card}）。"""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("cards", "strategy_cards", "items", "card_list"):
            value = obj.get(key)
            if isinstance(value, list):
                return value
        if obj and all(isinstance(v, dict) for v in obj.values()):
            out = []
            for sid, card in obj.items():
                item = dict(card)
                item.setdefault("strategy_id", sid)
                out.append(item)
            return out
    return None


def _view_ids(view, keys) -> list | None:
    """视图里的 id 列表（enabled / shadow_candidates）；兼容 [{strategy_id}] 形态。"""
    if not isinstance(view, dict):
        return None
    for key in keys:
        if key not in view:
            continue
        raw = view[key]
        if not isinstance(raw, (list, tuple)):
            continue
        out = []
        for item in raw:
            if isinstance(item, dict):
                sid = _norm_str(item.get("strategy_id") or item.get("id"))
            else:
                sid = _norm_str(item)
            if sid is not None:
                out.append(sid)
        return out
    return None


def _unavailable_cards(source: str, path, error: str) -> dict:
    return {
        "available": False,
        "source": source,
        "path": path,
        "error": error,
        "cards": {},
        "enabled": None,
        "shadow_candidates": None,
        "duplicates": 0,
        "ignored": 0,
    }


def _normalize_cards_input(obj, source: str, path, view=None) -> dict:
    """策略卡输入归一：卡列表 + 视图级 enabled/shadow_candidates。"""
    cards_obj = _extract_cards(obj)
    if cards_obj is None:
        return _unavailable_cards(source, path, "策略卡返回值结构无法识别（需 list / {cards:[...]}）")
    cards = {}
    duplicates = 0
    ignored = 0
    for item in cards_obj:
        if not isinstance(item, dict):
            ignored += 1
            continue
        sid = _norm_str(item.get("strategy_id") or item.get("id"))
        if sid is None:
            ignored += 1
            continue
        if sid in cards:
            duplicates += 1
            continue
        cards[sid] = _normalize_card(sid, item)
    if view is None and isinstance(obj, dict):
        view = obj
    enabled = _view_ids(view, ("enabled", "enabled_ids"))
    shadow = _view_ids(view, ("shadow_candidates", "shadow", "shadow_ids"))
    if enabled is None:
        true_ids = [sid for sid, card in cards.items() if card.get("enabled") is True]
        enabled = true_ids or None
    return {
        "available": True,
        "source": source,
        "path": path,
        "error": None,
        "cards": cards,
        "enabled": enabled,
        "shadow_candidates": shadow,
        "duplicates": duplicates,
        "ignored": ignored,
    }


def load_cards_view(cards=None, cards_file=None) -> dict:
    """策略卡视图：注入 / JSON 文件 / 自动 import strategy_cards；不可用显式降级。"""
    if cards is not None:
        return _normalize_cards_input(cards, "injected", None)
    if cards_file:
        path = os.path.abspath(cards_file)
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                obj = json.load(fh)
        except (OSError, ValueError) as exc:
            return _unavailable_cards("cards_file", path, "%s: %s" % (type(exc).__name__, exc))
        return _normalize_cards_input(obj, "cards_file", path)
    try:
        module = _import_strategy_cards()
    except Exception as exc:  # noqa: BLE001 - 并行交付未落地即显式降级
        return _unavailable_cards(
            "unavailable",
            None,
            "strategy_cards 暂不可用（%s: %s）" % (type(exc).__name__, exc),
        )
    registry_view = getattr(module, "registry_view", None)
    load_cards = getattr(module, "load_cards", None)
    view = None
    try:
        if callable(registry_view):
            view = registry_view()
    except Exception as exc:  # noqa: BLE001 - 视图失败不冒泡，显式记录
        return _unavailable_cards(
            "strategy_cards", None, "strategy_cards.registry_view() 失败（%s: %s）"
            % (type(exc).__name__, exc),
        )
    obj = view
    if obj is None:
        if not callable(load_cards):
            return _unavailable_cards(
                "strategy_cards", None, "strategy_cards 既无 registry_view() 也无 load_cards()"
            )
        try:
            obj = load_cards()
        except Exception as exc:  # noqa: BLE001
            return _unavailable_cards(
                "strategy_cards", None, "strategy_cards.load_cards() 失败（%s: %s）"
                % (type(exc).__name__, exc),
            )
    return _normalize_cards_input(obj, "strategy_cards", None, view=view)


# ── 权益曲线 / 成本注入归一 ───────────────────────────────────────────
def _normalize_equity_curves(equity_curves):
    """{sid: [equity...]} 或 {sid: {"equity": [...], "source": ...}} 归一。"""
    out = {}
    if not isinstance(equity_curves, dict):
        return out
    for sid, raw in equity_curves.items():
        key = _norm_str(sid)
        if key is None:
            continue
        source = "injected"
        values = raw
        if isinstance(raw, dict):
            values = raw.get("equity")
            if values is None:
                values = raw.get("values")
            if values is None:
                values = raw.get("curve")
            source = _norm_str(raw.get("source")) or "injected"
        if not isinstance(values, (list, tuple)):
            continue
        out[key] = {"values": list(values), "source": source}
    return out


def _normalize_costs(costs):
    """{sid: {cost_ratio/turnover_mean/cost_total/cost_per_row/unit/source}} 归一。"""
    out = {}
    if not isinstance(costs, dict):
        return out
    for sid, raw in costs.items():
        key = _norm_str(sid)
        if key is None or not isinstance(raw, dict):
            continue
        out[key] = {
            "cost_ratio": _num(raw.get("cost_ratio")),
            "turnover_mean": _num(raw.get("turnover_mean")),
            "cost_total": _num(raw.get("cost_total")),
            "cost_per_row": _num(raw.get("cost_per_row")),
            "unit": _norm_str(raw.get("unit")),
            "source": _norm_str(raw.get("source")) or "injected",
        }
    return out


# ── 统计块 ────────────────────────────────────────────────────────────
def _new_state() -> dict:
    return {
        "n": 0,
        "t1": [],
        "t5": [],
        "div": 0,
        "symbols": {},
        "regimes": {},
        "regime_missing": 0,
        "turnover": [],
        "cost": [],
        "last_td": "",
    }


def _update_state(state: dict, row: dict) -> None:
    state["n"] += 1
    if row["trade_date"] and row["trade_date"] > state["last_td"]:
        state["last_td"] = row["trade_date"]
    returns = row["returns"]
    for window in RETURN_WINDOWS:
        if returns[window] is not None:
            state[window].append(returns[window])
    if row["divergence"]:
        state["div"] += 1
    symbol = row["symbol"]
    if symbol:
        state["symbols"][symbol] = state["symbols"].get(symbol, 0) + 1
    regime = row["regime"]
    if regime is None:
        state["regime_missing"] += 1
    else:
        bucket = state["regimes"].setdefault(
            regime, {"n": 0, "t1": [], "t5": [], "div": 0}
        )
        bucket["n"] += 1
        for window in RETURN_WINDOWS:
            if returns[window] is not None:
                bucket[window].append(returns[window])
        if row["divergence"]:
            bucket["div"] += 1
    if row["turnover"] is not None:
        state["turnover"].append(row["turnover"])
    if row["cost"] is not None:
        state["cost"].append(row["cost"])


def _window_stats(values) -> dict:
    """单窗口收益统计：count / win_rate(%) / avg_ret(%)。"""
    if not values:
        return {"count": 0, "win_rate": None, "avg_ret": None}
    count = len(values)
    wins = sum(1 for value in values if value > 0)
    return {
        "count": count,
        "win_rate": _pct(wins, count, 1),
        "avg_ret": _round(sum(values) / count, 2),
    }


def _max_drawdown_series(values):
    """峰谷最大回撤（%）；峰值 <=0 不可算 → None。"""
    peak = values[0]
    worst = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak <= 0:
            return None
        drawdown = 100.0 * (value / peak - 1.0)
        if drawdown < worst:
            worst = drawdown
    return _round(worst, 2)


def _max_drawdown_block(equity_curves: dict, sid: str) -> dict:
    injected = equity_curves.get(sid)
    block = {
        "status": "not_provided",
        "max_drawdown": None,
        "points": None,
        "source": None,
        "note": "未注入权益曲线，最大回撤显式 null（不臆造）",
    }
    if not isinstance(injected, dict):
        return block
    numbers = []
    for value in injected["values"]:
        number = _num(value)
        if number is None:
            block.update(
                {
                    "status": "insufficient",
                    "points": len(injected["values"]),
                    "source": injected["source"],
                    "note": "权益曲线含非法点，最大回撤不可算（insufficient）",
                }
            )
            return block
        numbers.append(number)
    if len(numbers) < 2:
        block.update(
            {
                "status": "insufficient",
                "points": len(numbers),
                "source": injected["source"],
                "note": "权益曲线点数 < 2，最大回撤不可算（insufficient）",
            }
        )
        return block
    drawdown = _max_drawdown_series(numbers)
    if drawdown is None:
        block.update(
            {
                "status": "insufficient",
                "points": len(numbers),
                "source": injected["source"],
                "note": "权益曲线峰值非正，最大回撤不可算（insufficient）",
            }
        )
        return block
    return {
        "status": "computed",
        "max_drawdown": drawdown,
        "points": len(numbers),
        "source": injected["source"],
        "note": None,
    }


def _turnover_block(state: dict, costs_injected: dict, sid: str) -> dict:
    """换手：costs 注入优先，其次行级 turnover 均值；都没有 → 显式 null。"""
    injected = costs_injected.get(sid)
    rows_with_value = len(state["turnover"])
    coverage = _ratio(rows_with_value, state["n"])
    block = {
        "available": False,
        "mean": None,
        "rows_with_value": rows_with_value,
        "coverage": coverage,
        "source": None,
        "unit": None,
        "note": "无换手数据（行级 turnover / costs.turnover_mean 均缺），显式 null",
    }
    if injected is not None and injected.get("turnover_mean") is not None:
        block.update(
            {
                "available": True,
                "mean": _round(injected["turnover_mean"], 4),
                "source": injected["source"],
                "unit": injected["unit"],
                "note": None,
            }
        )
        return block
    if rows_with_value:
        block.update(
            {
                "available": True,
                "mean": _round(sum(state["turnover"]) / rows_with_value, 4),
                "source": "rows",
                "unit": None,
                "note": "行级换手口径，单位未给（unit=null）",
            }
        )
    return block


def _cost_block(state: dict, costs_injected: dict, sid: str) -> dict:
    """成本：costs 注入优先，其次行级 cost 合计/均值；都没有 → 显式 null。"""
    injected = costs_injected.get(sid) or {}
    rows_with_value = len(state["cost"])
    coverage = _ratio(rows_with_value, state["n"])
    block = {
        "available": False,
        "total": None,
        "mean": None,
        "cost_ratio": None,
        "rows_with_value": rows_with_value,
        "coverage": coverage,
        "source": None,
        "unit": None,
        "note": "无成本数据（行级 cost / costs 注入均缺），显式 null",
    }
    injected_keys = ("cost_total", "cost_per_row", "cost_ratio")
    if any(injected.get(key) is not None for key in injected_keys):
        total = injected.get("cost_total")
        mean = injected.get("cost_per_row")
        if mean is None and total is not None and state["n"]:
            mean = total / state["n"]
        block.update(
            {
                "available": True,
                "total": None if total is None else _round(total, 4),
                "mean": None if mean is None else _round(mean, 4),
                "cost_ratio": (
                    None
                    if injected.get("cost_ratio") is None
                    else _round(injected["cost_ratio"], 4)
                ),
                "source": injected.get("source") or "injected",
                "unit": injected.get("unit"),
                "note": None,
            }
        )
        return block
    if rows_with_value:
        total = sum(state["cost"])
        block.update(
            {
                "available": True,
                "total": _round(total, 4),
                "mean": _round(total / rows_with_value, 4),
                "source": "rows",
                "unit": None,
                "note": "行级成本口径，单位未给（unit=null）",
            }
        )
    return block


def _concentration_block(state: dict) -> dict:
    """样本集中度：最大单标的样本占比（前 1 标的 / 参与行数）。"""
    if not state["symbols"] or not state["n"]:
        return {
            "available": False,
            "symbol_count": len(state["symbols"]),
            "top_symbol": None,
            "top_symbol_share": None,
        }
    top_symbol, top_count = max(
        sorted(state["symbols"].items()), key=lambda item: item[1]
    )
    return {
        "available": True,
        "symbol_count": len(state["symbols"]),
        "top_symbol": top_symbol,
        "top_symbol_share": _ratio(top_count, state["n"]),
    }


def _regime_breakdown(state: dict) -> tuple:
    """regime 分层统计；无标签 → ({}, insufficient)，不臆造 regime。"""
    if not state["regimes"]:
        return {}, "insufficient"
    out = {}
    for regime in sorted(state["regimes"]):
        bucket = state["regimes"][regime]
        out[regime] = {
            "count": bucket["n"],
            "t1": _window_stats(bucket["t1"]),
            "t5": _window_stats(bucket["t5"]),
            "divergence_rate": _pct(bucket["div"], bucket["n"]),
        }
    return out, "available"


# ── 策略卡 / registry 展示块 ──────────────────────────────────────────
def _card_block(sid: str, cards_view: dict, row_ids: set) -> dict:
    normalized = cards_view["cards"].get(sid)
    if normalized is None:
        return {
            "present": False,
            "source": None,
            "display_name": None,
            "owner": None,
            "version": None,
            "horizon_days": None,
            "regime_tags": None,
            "known_failure_modes": None,
            "modes": None,
            "enabled": None,
            "fields": None,
        }
    block = {
        "present": True,
        "source": cards_view["source"],
        "display_name": normalized["display_name"],
        "owner": normalized["owner"],
        "version": normalized["version"],
        "horizon_days": normalized["horizon_days"],
        "regime_tags": normalized["regime_tags"],
        "known_failure_modes": normalized["known_failure_modes"],
        "modes": normalized["modes"],
        "enabled": normalized["enabled"],
        "fields": normalized["fields"],
    }
    if sid not in row_ids and normalized["enabled"] is None:
        block["enabled"] = None
    return block


def _registry_block(sid: str, registry_view: dict, cards_view: dict) -> dict:
    available = registry_view["available"]
    weights = registry_view["weights"] or {}
    enabled_ids = registry_view["enabled"]
    top_n = registry_view["top_n"] or {}
    known = None
    if available:
        known = sid in weights or sid in top_n or bool(enabled_ids and sid in enabled_ids)
    enabled = None
    enabled_source = None
    if available and enabled_ids is not None:
        enabled = sid in enabled_ids
        enabled_source = "registry"
    elif cards_view["available"] and cards_view["enabled"] is not None:
        enabled = sid in cards_view["enabled"]
        enabled_source = "cards"
    weight = weights.get(sid) if available else None
    weight_group = None
    if weight is not None:
        weight_group = _combined_posterior.weight_group_of(weight)
    return {
        "known": known,
        "enabled": enabled,
        "enabled_source": enabled_source,
        "weight": weight,
        "weight_group": weight_group,
        "top_n": top_n.get(sid) if available else None,
        "registry_available": available,
    }


# ── 健康评级（T+1 主口径，覆盖不足回退 T+5；两窗口都不足 → insufficient） ──
def _grade_health(card: dict, thresholds: dict) -> tuple:
    n = card["sample_count"]
    min_samples = thresholds["min_samples"]
    if n < min_samples:
        return "insufficient", [
            "样本数 %d < 门槛 %s：不下健康结论（insufficient）" % (n, min_samples)
        ]
    data_quality = card["data_quality"]
    coverage_t1 = data_quality["coverage_t1"] or 0.0
    coverage_t5 = data_quality["coverage_t5"] or 0.0
    min_coverage = thresholds["min_return_coverage"]
    if coverage_t1 >= min_coverage:
        caliber = "t1"
    elif coverage_t5 >= min_coverage:
        caliber = "t5"
    else:
        return "insufficient", [
            "T+1/T+5 收益覆盖率 %.2f/%.2f 均 < %.2f：无可评收益（insufficient）"
            % (coverage_t1, coverage_t5, min_coverage)
        ]

    stats = card["windows"][caliber]
    win_rate = stats["win_rate"]
    avg_ret = stats["avg_ret"]
    reasons = []
    if caliber == "t5":
        reasons.append("T+1 覆盖不足，回退 T+5 口径评级")
    level = "green"
    order = {"green": 0, "yellow": 1, "red": 2}

    def bump(new_level):
        nonlocal level
        if order[new_level] > order[level]:
            level = new_level

    if win_rate is not None:
        if win_rate < thresholds["red_win_rate"]:
            bump("red")
            reasons.append(
                "%s 胜率 %.1f%% < 红线 %.1f%%（red）"
                % (caliber.upper(), win_rate, thresholds["red_win_rate"])
            )
        elif win_rate < thresholds["green_win_rate"]:
            bump("yellow")
            reasons.append(
                "%s 胜率 %.1f%% < 绿线 %.1f%%（yellow）"
                % (caliber.upper(), win_rate, thresholds["green_win_rate"])
            )
    if avg_ret is not None:
        if avg_ret < thresholds["red_avg_ret"]:
            bump("red")
            reasons.append(
                "%s 平均收益 %.2f%% < 红线 %.2f%%（red）"
                % (caliber.upper(), avg_ret, thresholds["red_avg_ret"])
            )
        elif avg_ret < 0:
            bump("yellow")
            reasons.append(
                "%s 平均收益 %.2f%% < 0（yellow）" % (caliber.upper(), avg_ret)
            )
    drawdown = card["max_drawdown"]["max_drawdown"]
    if drawdown is not None:
        if drawdown <= thresholds["red_max_drawdown"]:
            bump("red")
            reasons.append(
                "最大回撤 %.2f%% <= 红线 %.2f%%（red）"
                % (drawdown, thresholds["red_max_drawdown"])
            )
        elif drawdown <= thresholds["yellow_max_drawdown"]:
            bump("yellow")
            reasons.append(
                "最大回撤 %.2f%% <= 黄线 %.2f%%（yellow）"
                % (drawdown, thresholds["yellow_max_drawdown"])
            )
    divergence_rate = card["divergence_rate"]
    if divergence_rate is not None:
        if divergence_rate >= thresholds["divergence_rate_red"]:
            bump("red")
            reasons.append(
                "分歧率 %.1f%% >= 红线 %.1f%%（red）"
                % (divergence_rate, thresholds["divergence_rate_red"])
            )
        elif divergence_rate >= thresholds["divergence_rate_warn"]:
            bump("yellow")
            reasons.append(
                "分歧率 %.1f%% >= 黄线 %.1f%%（yellow）"
                % (divergence_rate, thresholds["divergence_rate_warn"])
            )
    if level == "green":
        reasons = [
            "样本 %d >= %s、%s 胜率 %.1f%% >= %.1f%%、平均收益 %.2f%% >= 0，"
            "回撤/分歧率未触黄线"
            % (
                n,
                min_samples,
                caliber.upper(),
                win_rate,
                thresholds["green_win_rate"],
                avg_ret,
            )
        ]
    return level, reasons


# ── 偏差票（BiasTicket draft；只建议，不自动改任何东西） ──────────────
def _make_ticket(
    sid: str, bias_type: str, evidence, suggested_action: str, priority: str, asof: str
) -> dict:
    return {
        "schema": BIAS_SCHEMA,
        "ticket_id": "BT-%s-%s" % (sid, bias_type),
        "strategy_id": sid,
        "bias_type": bias_type,
        "evidence": [str(item) for item in evidence],
        "suggested_action": suggested_action,
        "priority": priority,
        "status": STATUS_DRAFT,
        "asof": asof,
    }


def _build_bias_tickets(card: dict, thresholds: dict, asof: str) -> list:
    """按 HealthCard 生成偏差票：证据全部来自已算出的真实统计，缺数据不发票。"""
    sid = card["strategy_id"]
    level = card["health_level"]
    high = level in ("red", "insufficient")
    priority = "P1" if high else "P2"
    tickets = []

    def add(bias_type, evidence, suggested_action, forced_priority=None):
        tickets.append(
            _make_ticket(
                sid,
                bias_type,
                evidence,
                suggested_action,
                forced_priority or priority,
                asof,
            )
        )

    n = card["sample_count"]
    data_quality = card["data_quality"]
    coverage_t1 = data_quality["coverage_t1"] or 0.0
    coverage_t5 = data_quality["coverage_t5"] or 0.0

    # 1) 样本不足
    if n < thresholds["min_samples"]:
        add(
            "insufficient",
            ["样本数 %d < 门槛 %s，胜率/收益无统计效力" % (n, thresholds["min_samples"])],
            "补足回放样本（延长窗口/并入更多交易日）后重跑健康检查；"
            "样本达标前不依据本卡调整权重或 registry（%s）" % _NOTE_READONLY,
            "P1",
        )

    # 2) 数据缺口（覆盖不足；样本够才单独发，否则 insufficient 票已覆盖）
    min_coverage = thresholds["min_return_coverage"]
    if n >= thresholds["min_samples"] and (
        coverage_t1 < min_coverage or coverage_t5 < min_coverage
    ):
        add(
            "data",
            [
                "T+1 后验收益覆盖 %.1f%%（%d/%d 行），T+5 覆盖 %.1f%%"
                % (
                    coverage_t1 * 100.0,
                    data_quality["t1_return_rows"],
                    n,
                    coverage_t5 * 100.0,
                ),
                "收盘/结算行情未注入或配对缺失，胜率/收益分母不完整",
            ],
            "补齐行情或结算后验配对（显式注入 quote/settle 后重跑）；"
            "缺口补齐前不评估胜率与收益（不造数，%s）" % _NOTE_READONLY,
            "P1" if coverage_t1 == 0.0 and coverage_t5 == 0.0 else "P2",
        )

    # 3) 短窗/长窗背离（过拟合嫌疑）
    t1_stats = card["windows"]["t1"]
    t5_stats = card["windows"]["t5"]
    if (
        n >= thresholds["min_samples"]
        and t1_stats["win_rate"] is not None
        and t5_stats["avg_ret"] is not None
        and t1_stats["win_rate"] >= thresholds["overfit_t1_win_rate"]
        and t5_stats["avg_ret"] <= thresholds["overfit_t5_avg_ret"]
    ):
        add(
            "overfit",
            [
                "T+1 胜率 %.1f%%（>= %.1f%%）但 T+5 平均收益 %.2f%%（<= %.2f%%）"
                % (
                    t1_stats["win_rate"],
                    thresholds["overfit_t1_win_rate"],
                    t5_stats["avg_ret"],
                    thresholds["overfit_t5_avg_ret"],
                ),
                "短窗表现与持有窗收益背离，疑似短窗过拟合或成本/滑点未计",
            ],
            "复核 T+5 口径是否含成本/滑点，并抽样看 T+5 亏损分布；"
            "结论前暂缓上调权重（需人工复核，%s）" % _NOTE_READONLY,
        )

    # 4) regime 依赖
    regime_breakdown = card["regime_breakdown"]
    eligible = []
    for regime, block in regime_breakdown.items():
        if block["count"] < thresholds["regime_min_samples"]:
            continue
        stats = block["t1"] if block["t1"]["win_rate"] is not None else block["t5"]
        if stats["win_rate"] is not None:
            eligible.append((regime, block["count"], stats["win_rate"]))
    if len(eligible) >= 2:
        eligible.sort(key=lambda item: (-item[2], item[0]))
        best = eligible[0]
        worst = eligible[-1]
        gap = best[2] - worst[2]
        if gap >= thresholds["regime_gap_pp"]:
            add(
                "regime",
                [
                    "regime 分层胜率差 %.1f 个百分点（>= %.1f）：最佳 %s %.1f%% vs 最差 %s %.1f%%"
                    % (
                        gap,
                        thresholds["regime_gap_pp"],
                        best[0],
                        best[2],
                        worst[0],
                        worst[2],
                    ),
                    "分层样本：%s"
                    % "，".join(
                        "%s=%d 行" % (regime, count) for regime, count, _ in eligible
                    ),
                ],
                "按 regime 分层重估（或仅在高胜率 regime 启用）；"
                "需要人工复核分层口径后决定，%s" % _NOTE_READONLY,
            )

    # 5) 成本/换手
    cost = card["cost"]
    turnover = card["turnover"]
    if cost["cost_ratio"] is not None and (
        cost["cost_ratio"] >= thresholds["cost_ratio_high"]
    ):
        add(
            "cost",
            [
                "成本/收益比 %.2f >= 阈值 %.2f（%s）"
                % (
                    cost["cost_ratio"],
                    thresholds["cost_ratio_high"],
                    cost.get("source") or "injected",
                )
            ],
            "按当前成本假设重算净收益，复核费率/滑点；必要时降频后再评估（%s）"
            % _NOTE_READONLY,
        )
    elif (
        turnover["available"]
        and turnover["mean"] is not None
        and turnover["rows_with_value"] >= thresholds["min_cost_rows"]
        and turnover["mean"] >= thresholds["turnover_high"]
    ):
        add(
            "cost",
            [
                "行级平均换手 %.4f >= 阈值 %.2f（覆盖 %d 行）"
                % (
                    turnover["mean"],
                    thresholds["turnover_high"],
                    turnover["rows_with_value"],
                ),
                "换手偏高，成本敏感（行级 unit=null，成本金额需由成本账注入复核）",
            ],
            "复核成本敏感度：按当前费率/滑点重算净值，必要时降换手（%s）"
            % _NOTE_READONLY,
        )

    # 6) 执行/分歧
    divergence_rate = card["divergence_rate"]
    if (
        divergence_rate is not None
        and divergence_rate >= thresholds["divergence_rate_warn"]
    ):
        add(
            "execution",
            [
                "分歧率 %.1f%% >= 阈值 %.1f%%（%d/%d 行 divergence=true）"
                % (
                    divergence_rate,
                    thresholds["divergence_rate_warn"],
                    card["divergence_count"],
                    n,
                ),
                "多源 buy/sell 对立信号占比偏高，执行语义存在歧义",
            ],
            "核对执行链（信号对齐/下单时点/口径）；分歧票单独回放后再评估权重（%s）"
            % _NOTE_READONLY,
        )

    # 7) 集中度
    concentration = card["concentration"]
    share = concentration["top_symbol_share"]
    if (
        share is not None
        and share >= thresholds["concentration_top_symbol_share"]
    ):
        add(
            "concentration",
            [
                "最大单标的样本占比 %.1f%% >= 阈值 %.1f%%（%s，共 %d 个标的）"
                % (
                    share * 100.0,
                    thresholds["concentration_top_symbol_share"] * 100.0,
                    concentration["top_symbol"],
                    concentration["symbol_count"],
                )
            ],
            "补充多标的/全市场回放，避免结论被少数标的支配（%s）" % _NOTE_READONLY,
        )
    return tickets


# ── 主入口：build_strategy_health ─────────────────────────────────────
def _build_health_card(sid: str, state: dict, ctx: dict) -> tuple:
    """单策略 HealthCard + 偏差票。"""
    thresholds = ctx["thresholds"]
    n = state["n"]
    data_quality = {
        "t1_return_rows": len(state["t1"]),
        "t5_return_rows": len(state["t5"]),
        "coverage_t1": _ratio(len(state["t1"]), n),
        "coverage_t5": _ratio(len(state["t5"]), n),
        "divergence_rows": state["div"],
        "regime_rows": n - state["regime_missing"],
        "regime_missing_rows": state["regime_missing"],
        "turnover_rows": len(state["turnover"]),
        "cost_rows": len(state["cost"]),
    }
    regime_breakdown, regime_status = _regime_breakdown(state)
    card = {
        "strategy_id": sid,
        "discovered_from": [],
        "card": _card_block(sid, ctx["cards"], ctx["row_ids"]),
        "registry": _registry_block(sid, ctx["registry"], ctx["cards"]),
        "asof": state["last_td"] or ctx["asof"],
        "sample_count": n,
        "windows": {
            "t1": _window_stats(state["t1"]),
            "t5": _window_stats(state["t5"]),
        },
        "max_drawdown": _max_drawdown_block(ctx["equity"], sid),
        "turnover": _turnover_block(state, ctx["costs"], sid),
        "cost": _cost_block(state, ctx["costs"], sid),
        "divergence_count": state["div"],
        "divergence_rate": _pct(state["div"], n),
        "regime_breakdown": regime_breakdown,
        "regime_status": regime_status,
        "concentration": _concentration_block(state),
        "data_quality": data_quality,
        "health_level": None,
        "health_reasons": [],
        "bias_ticket_ids": [],
    }
    level, reasons = _grade_health(card, thresholds)
    card["health_level"] = level
    card["health_reasons"] = reasons
    tickets = _build_bias_tickets(card, thresholds, card["asof"] or ctx["asof"])
    card["bias_ticket_ids"] = [ticket["ticket_id"] for ticket in tickets]
    return card, tickets


def _discovered_from(sid: str, ctx: dict) -> list:
    sources = []
    registry_view = ctx["registry"]
    if registry_view["available"]:
        weights = registry_view["weights"] or {}
        enabled_ids = registry_view["enabled"] or []
        top_n = registry_view["top_n"] or {}
        if sid in weights or sid in top_n or sid in enabled_ids:
            sources.append("registry")
    if sid in ctx["cards"]["cards"]:
        sources.append("cards")
    if sid in ctx["row_ids"]:
        sources.append("rows")
    return sources


def build_strategy_health(
    rows=None,
    rows_file=None,
    rows_source=None,
    cards=None,
    cards_file=None,
    registry=None,
    registry_path=None,
    equity_curves=None,
    costs=None,
    thresholds=None,
    out_path=None,
) -> dict:
    """产出策略健康报告（只读统计 + 可选原子落盘为 JSON 产出档）。

    Parameters
    ----------
    rows : list[dict] | None        直接注入后验行（可含 ret_t1/ret_t5/regime/...）
    rows_file : str | None          经 load_rows_file 回放行 JSON
    rows_source : str | None        行来源标注（默认 injected / rows_file:...）
    cards : list | dict | None      策略卡视图注入（list 或 {"cards": [...]}）
    cards_file : str | None         策略卡视图 JSON 文件
    registry : dict | None          registry 视图注入（enabled/weights/top_n）
    registry_path : str | None      registry 文件（默认 strategy_registry.json）
    equity_curves : dict | None     {sid: [equity...]}，有才算最大回撤
    costs : dict | None             {sid: {...}}，有才算换手/成本
    thresholds : dict | None        阈值覆盖（未知键 ValueError）
    out_path : str | None           给出则原子写 JSON 产出档

    Returns
    -------
    dict
        {schema,status,readonly,side_effects,generated_at,asof,total_rows,
         invalid_rows,inputs,thresholds,caliber,summary,strategies,bias_tickets,notes}
    """
    thresholds = merge_thresholds(thresholds)

    if rows is None:
        if rows_file:
            rows = load_rows_file(rows_file)
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

    norm_rows, invalid_rows = normalize_rows(rows)

    registry_view = load_registry_view(registry=registry, registry_path=registry_path)
    cards_view = load_cards_view(cards=cards, cards_file=cards_file)
    equity = _normalize_equity_curves(equity_curves)
    cost_injected = _normalize_costs(costs)

    states = {}
    global_asof = ""
    for row in norm_rows:
        if row["trade_date"] and row["trade_date"] > global_asof:
            global_asof = row["trade_date"]
        for sid in row["contributions"]:
            state = states.setdefault(sid, _new_state())
            _update_state(state, row)

    row_ids = set(states)
    universe = set(row_ids)
    if registry_view["available"]:
        universe |= set(registry_view["enabled"] or [])
        universe |= set(registry_view["weights"] or {})
        universe |= set(registry_view["top_n"] or {})
    universe |= set(cards_view["cards"])

    ctx = {
        "thresholds": thresholds,
        "registry": registry_view,
        "cards": cards_view,
        "equity": equity,
        "costs": cost_injected,
        "row_ids": row_ids,
        "asof": global_asof,
    }
    strategies = {}
    all_tickets = []
    for sid in sorted(universe):
        state = states.get(sid) or _new_state()
        card, tickets = _build_health_card(sid, state, ctx)
        card["discovered_from"] = _discovered_from(sid, ctx)
        strategies[sid] = card
        all_tickets.extend(tickets)

    level_counts = {
        level: sum(1 for card in strategies.values() if card["health_level"] == level)
        for level in HEALTH_LEVELS
    }
    summary = {
        "strategies": len(strategies),
        "bias_tickets": len(all_tickets),
        "rows_total": len(norm_rows),
        "rows_invalid": invalid_rows,
    }
    summary.update(level_counts)

    doc = {
        "schema": SCHEMA,
        "status": "OK",
        "readonly": True,
        "side_effects": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "asof": global_asof,
        "total_rows": len(norm_rows),
        "invalid_rows": invalid_rows,
        "inputs": {
            "rows": {
                "source": rows_source,
                "count": len(norm_rows),
                "invalid_rows": invalid_rows,
            },
            "registry": {
                "source": registry_view["source"],
                "path": registry_view["path"],
                "available": registry_view["available"],
                "error": registry_view["error"],
                "enabled": registry_view["enabled"],
                "weights": registry_view["weights"],
                "top_n": registry_view["top_n"],
                "invalid_weights": registry_view["invalid_weights"],
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
            },
            "equity_curves": {
                "source": "injected" if equity else "not_provided",
                "count": len(equity),
            },
            "costs": {
                "source": "injected" if cost_injected else "not_provided",
                "count": len(cost_injected),
            },
        },
        "thresholds": thresholds,
        "caliber": {
            "returns": "百分数（T+N 收益，1.23 表示 +1.23%）",
            "win_rate": "收益 > 0 占比（%），分母为该窗口收益可算样本",
            "sample_count": "contributions 出现该策略的有效行数（多源行跨桶）",
            "max_drawdown": "峰谷回撤百分数（负数）；仅注入权益曲线时算，否则 null",
            "turnover_cost": "行级 turnover/cost 或 costs 注入；单位未给则 unit=null",
            "divergence_rate": "参与行中 divergence=true 占比（%）",
            "health_level": "T+1 主口径，T+1 覆盖不足回退 T+5；阈值见 thresholds",
            "bias_ticket": "status 恒 draft，仅建议人工复核；不自动改权重/registry",
        },
        "summary": summary,
        "strategies": strategies,
        "bias_tickets": all_tickets,
        "notes": [
            _NOTE_READONLY,
            "缺数据一律显式 null / insufficient：无权益曲线不算最大回撤、"
            "无成本口径不猜单位、无 regime 标签不做分层结论",
            "BiasTicket 全部 status=draft，须人工复核后才可能进入调权/registry 流程",
        ],
    }
    if out_path:
        doc["out_path"] = _write_json_atomic(doc, out_path)
    return doc


# ── Markdown 渲染（同一份数据，两种呈现） ─────────────────────────────
def _fmt(value, digits=2):
    if value is None:
        return "—"
    return ("%%.%df" % digits) % float(value)


def _fmt_pct(value):
    if value is None:
        return "—"
    return "%.1f%%" % float(value)


def _fmt_share(value):
    if value is None:
        return "—"
    return "%.1f%%" % (100.0 * float(value))


def render_markdown(doc: dict) -> str:
    """健康报告 → Markdown（决策台/汇报可直接贴）。"""
    summary = doc["summary"]
    lines = [
        "# 策略健康卡 · Strategy Health",
        "",
        "- 生成：%s｜asof：%s｜schema：%s"
        % (doc["generated_at"], doc["asof"] or "—", doc["schema"]),
        "- 只读：是（不自动改权重/registry、不写生产数据、无副作用）",
        "- 样本行：%d（无效行 %d）"
        % (doc["total_rows"], doc["invalid_rows"]),
        "",
        "## 一、健康总览",
        "",
        "| 等级 | 策略数 |",
        "|---|---|",
    ]
    for level in HEALTH_LEVELS:
        lines.append("| %s | %d |" % (level, summary[level]))
    lines += [
        "",
        "偏差票（BiasTicket·draft）：%d 张" % summary["bias_tickets"],
        "",
        "## 二、策略明细",
        "",
        "| 策略 | 等级 | 样本 | T+1 胜率 | T+5 胜率 | 平均 T+1 | 平均 T+5 "
        "| 最大回撤 | 分歧率 | 偏差票 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sid in sorted(doc["strategies"]):
        card = doc["strategies"][sid]
        windows = card["windows"]
        lines.append(
            "| %s | %s | %d | %s | %s | %s | %s | %s | %s | %s |"
            % (
                sid,
                card["health_level"],
                card["sample_count"],
                _fmt_pct(windows["t1"]["win_rate"]),
                _fmt_pct(windows["t5"]["win_rate"]),
                _fmt(windows["t1"]["avg_ret"]),
                _fmt(windows["t5"]["avg_ret"]),
                _fmt(card["max_drawdown"]["max_drawdown"]),
                _fmt_pct(card["divergence_rate"]),
                ", ".join(card["bias_ticket_ids"]) or "—",
            )
        )

    lines += ["", "## 三、健康原因（非 green）", ""]
    flagged = [
        doc["strategies"][sid]
        for sid in sorted(doc["strategies"])
        if doc["strategies"][sid]["health_level"] != "green"
    ]
    if not flagged:
        lines.append("（无：全部 strategy 为 green 或 insufficient 见下）")
    for card in flagged:
        lines.append(
            "- **%s**（%s）：%s"
            % (card["strategy_id"], card["health_level"], "；".join(card["health_reasons"]))
        )

    lines += ["", "## 四、偏差票（BiasTicket·draft）", ""]
    if not doc["bias_tickets"]:
        lines.append("（无：未命中偏差规则）")
    else:
        lines += [
            "| 票号 | 策略 | 类型 | 优先级 | 证据 | 建议动作 |",
            "|---|---|---|---|---|---|",
        ]
        for ticket in doc["bias_tickets"]:
            lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    ticket["ticket_id"],
                    ticket["strategy_id"],
                    ticket["bias_type"],
                    ticket["priority"],
                    "；".join(ticket["evidence"]),
                    ticket["suggested_action"],
                )
            )

    lines += ["", "## 五、regime 分层（有标签才算）", ""]
    regime_lines = []
    for sid in sorted(doc["strategies"]):
        card = doc["strategies"][sid]
        if card["regime_status"] != "available":
            continue
        regime_lines.append("### %s" % sid)
        regime_lines += [
            "| regime | 样本 | T+1 胜率 | 平均 T+1 | T+5 胜率 | 平均 T+5 |",
            "|---|---|---|---|---|---|",
        ]
        for regime in sorted(card["regime_breakdown"]):
            block = card["regime_breakdown"][regime]
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
        "- registry：%s（enabled=%s，weights=%s）"
        % (
            "available" if registry["available"] else "unavailable",
            registry["enabled"],
            registry["weights"],
        ),
        "- 策略卡：%s（count=%s）"
        % ("available" if cards["available"] else "unavailable", cards["count"]),
        "- 收益单位：百分数；最大回撤/换手/成本缺失时显式 null（不造数）",
        "- BiasTicket status 恒 draft：只建议人工复核，不自动改权重/registry",
    ]
    return "\n".join(lines)


# ── CLI（--rows-file / --out / --format；只读，不连 PG） ──────────────
USAGE = """用法：
  python strategy_health.py [--rows-file F] [--cards-file F] [--registry F]
                            [--min-samples N] [--format json|md]
                            [--out F] [--limit N]

只读：统计后验行并输出 JSON/MD；不写 PG、不改 registry/权重、不新增任务。
不给 --out 时 JSON 打到 stdout；--format md 时 Markdown 打到 stdout。
"""


def _cli_summary(doc: dict) -> str:
    summary = doc["summary"]
    return (
        "[strategy_health] strategies=%d green=%d yellow=%d red=%d insufficient=%d "
        "tickets=%d asof=%s"
        % (
            summary["strategies"],
            summary["green"],
            summary["yellow"],
            summary["red"],
            summary["insufficient"],
            summary["bias_tickets"],
            doc["asof"] or "-",
        )
    )


def main(argv=None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    rows_file = cards_file = registry_path = out = None
    fmt = "json"
    min_samples = None
    limit = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help"):
            print(USAGE)
            return 0
        if arg not in (
            "--rows-file",
            "--cards-file",
            "--registry",
            "--format",
            "--out",
            "--min-samples",
            "--limit",
        ):
            print("[strategy_health] 未知参数: %s" % arg, file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return 2
        if index + 1 >= len(argv):
            print("[strategy_health] 参数 %s 缺值" % arg, file=sys.stderr)
            return 2
        value = argv[index + 1]
        index += 2
        if arg == "--rows-file":
            rows_file = value
        elif arg == "--cards-file":
            cards_file = value
        elif arg == "--registry":
            registry_path = value
        elif arg == "--format":
            fmt = value
        elif arg == "--out":
            out = value
        elif arg == "--min-samples":
            try:
                min_samples = int(value)
            except ValueError:
                print("[strategy_health] --min-samples 须为整数: %r" % value, file=sys.stderr)
                return 2
        elif arg == "--limit":
            try:
                limit = int(value)
            except ValueError:
                print("[strategy_health] --limit 须为整数: %r" % value, file=sys.stderr)
                return 2
    if fmt not in ("json", "md"):
        print("[strategy_health] --format 只认 json/md: %r" % fmt, file=sys.stderr)
        return 2

    rows = None
    rows_source = None
    if rows_file:
        try:
            rows = load_rows_file(rows_file)
        except (OSError, ValueError) as exc:
            print("[strategy_health] rows-file 读取失败: %s" % exc, file=sys.stderr)
            return 2
        rows_source = "rows_file:%s" % os.path.abspath(rows_file)
    if limit is not None and rows is not None:
        rows = rows[:limit]
        rows_source = "%s#limit=%d" % (rows_source, limit)

    thresholds = {}
    if min_samples is not None:
        thresholds["min_samples"] = min_samples

    try:
        doc = build_strategy_health(
            rows=rows,
            rows_source=rows_source,
            cards_file=cards_file,
            registry_path=registry_path,
            thresholds=thresholds,
            out_path=None if fmt == "md" else out,
        )
    except ValueError as exc:
        print("[strategy_health] 参数/输入非法: %s" % exc, file=sys.stderr)
        return 2

    if fmt == "md":
        text = render_markdown(doc)
        if out:
            path = _write_text_atomic(text, out)
            print(_cli_summary(doc) + " → " + path)
        else:
            print(text)
        return 0
    if out:
        print(_cli_summary(doc) + " → " + doc.get("out_path", ""))
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "BIAS_SCHEMA",
    "BIAS_TYPES",
    "DEFAULT_THRESHOLDS",
    "HEALTH_LEVELS",
    "PRIORITIES",
    "SCHEMA",
    "build_strategy_health",
    "load_cards_view",
    "load_registry_view",
    "load_rows_file",
    "main",
    "merge_thresholds",
    "normalize_rows",
    "render_markdown",
]


if __name__ == "__main__":
    raise SystemExit(main())
