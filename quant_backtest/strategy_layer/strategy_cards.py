# -*- coding: utf-8 -*-
r"""Phase 0-A：10 策略归一化 Strategy Card（overlay 只读层）。

把 ``quant_backtest/strategies/manifests/`` 的 10 个运行 manifest 归一为统一的
可组合 Strategy Card，供组合层 / 健康度层（Phase 0-B strategy_health）只读消费。

设计红线（WO-STRAT-CARDS-20261031）
-----------------------------------
- **不改 manifest 既有字段**：卡片字段由 ``strategy_cards.json`` overlay 承载，
  version / data_deps / capacity / modes 与 manifest 强一致（validate_card 校验），
  manifest 文件一个字节都不写。
- **不改 registry 运行语义**：``strategy_registry.json`` 只读；本模块不注册
  adapter、不 set_enabled / set_weight、不写盘，生产聚合链保持现状。
- **默认不启用新增策略**：10 张卡默认只有 registry 既有 enabled 的 chan_monitor
  在运行语义内；其余策略仅作为 shadow candidate 登记。
- 只读、离线：不新增计划任务、不重启服务、不改 paper/交易链、不改 Hermes。

归一字段（每张卡 15 个）
------------------------
``strategy_id / version / owner / universe / frequency / horizon_days /
signal_schema / regime_tags / known_failure_modes / data_deps / capacity /
params_schema / modes / health``

字段派生口径见 ``strategy_cards.json`` 的 ``_meta.field_derivation``；其中
version / data_deps / capacity / modes 镜像 manifest，其余为 A0 归一初值
（universe/frequency/horizon_days/signal_schema/regime_tags/known_failure_modes/
params_schema），由 Phase 0-B 健康度校准，不构成运行参数变更。``health`` 在
Phase 0-A 固定为空 ``{}``，健康度由 Phase 0-B ``strategy_health`` 只读产出为
独立 HealthCard，不回写卡片。

registry_view 数字口径（当前基线 10 卡 / enabled=2 / shadow_candidates=8）
--------------------------------------------------------------------------
- ``enabled`` = ``strategy_registry.json`` 的 ``enabled`` 条目数 = 2
  （kg_boost + chan_monitor）；其中只有 chan_monitor 有 manifest 卡，
  kg_boost 为 adapter-only（见 ``registry_only_enabled_ids``）。
- ``shadow_candidates`` = 10 张卡中未在 registry enabled、且不是非选股
  overlay（``signal_schema.kind == "position_scale"`` 的 vol_target 只输出
  仓位缩放系数、无独立选股信号流）的卡 = 10 - 1 - 1 = 8。

用法（在 quant_backtest 目录下，或任意目录显式加 sys.path）::

    from strategy_layer.strategy_cards import load_cards, validate_card, registry_view

    cards = load_cards()            # -> list[dict]，10 张卡（含字段校验）
    view = registry_view()          # -> {"total_cards": 10, "enabled": 2, ...}
    validated = validate_card(cards[0])

命令行只读自检::

    python -X utf8 strategy_layer/strategy_cards.py            # 人类可读摘要
    python -X utf8 strategy_layer/strategy_cards.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
QUANT_ROOT = os.path.dirname(HERE)
DEFAULT_CARDS_PATH = os.path.join(HERE, "strategy_cards.json")
DEFAULT_MANIFESTS_DIR = os.path.join(QUANT_ROOT, "strategies", "manifests")
DEFAULT_REGISTRY_PATH = os.path.join(HERE, "strategy_registry.json")

# 每张卡必须齐全的字段（Phase 0-A 验收口径）
CARD_FIELDS = (
    "strategy_id",
    "version",
    "owner",
    "universe",
    "frequency",
    "horizon_days",
    "signal_schema",
    "regime_tags",
    "known_failure_modes",
    "data_deps",
    "capacity",
    "params_schema",
    "modes",
    "health",
)
# 允许的运行模式（与 agent_platform/engine/strategy_config.py 的 ALLOWED_MODES 同域）
ALLOWED_MODES = ("BACKTEST", "REPLAY", "SHADOW", "SIM_MARKET")
CAPACITY_KEYS = ("timeout_s", "max_concurrency", "max_context_bytes")
# 非选股 overlay：只输出仓位缩放系数，无独立选股信号流，不进信号影子候选
NON_SIGNAL_SCHEMA_KINDS = ("position_scale",)
# A0 基线卡数（10 个 manifest 一一对应；manifest 增减须同步本单口径）
EXPECTED_CARD_COUNT = 10

SHADOW_CANDIDATE_RULE = (
    "shadow_candidates = 10 卡中未在 registry enabled、且 signal_schema.kind "
    "不属于非选股 overlay（position_scale）的卡；当前 = 10 - chan_monitor(在册启用) "
    "- vol_target(仓位缩放 overlay) = 8。只读口径，不改运行。"
)


# ── 基础读写 ──────────────────────────────────────────────────────────
def _load_json(path: str) -> Any:
    """读 UTF-8 JSON；缺失/损坏显式 ValueError（fail-visible，不静默降级）。"""
    if not path or not os.path.isfile(path):
        raise ValueError("JSON 文件不存在: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("JSON 读取失败 %s: %s" % (path, exc)) from exc


def _require(cond: Any, msg: str) -> None:
    """断言失败统一抛 ValueError（卡片校验的单一异常类型）。"""
    if not cond:
        raise ValueError(msg)


def _is_str_list(value: Any) -> bool:
    """非空、无重复、元素为非空字符串的 list。"""
    if not isinstance(value, list) or not value:
        return False
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return False
    return len(set(value)) == len(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ── manifest 基线 ─────────────────────────────────────────────────────
def load_manifests(manifests_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """读 ``strategies/manifests/*.manifest.json`` → ``{strategy_id: manifest}``。

    仅只读；manifest 顶层字段原样返回，供卡片一致性校验与来源留痕。
    """
    base = manifests_dir or DEFAULT_MANIFESTS_DIR
    _require(os.path.isdir(base), "manifest 目录不存在: %s" % base)
    out: Dict[str, Dict[str, Any]] = {}
    for name in sorted(os.listdir(base)):
        if not name.endswith(".manifest.json"):
            continue
        manifest = _load_json(os.path.join(base, name))
        _require(isinstance(manifest, Mapping), "manifest 顶层必须为 dict: %s" % name)
        sid = manifest.get("strategy_id")
        _require(
            isinstance(sid, str) and bool(sid.strip()),
            "manifest 缺合法 strategy_id: %s" % name,
        )
        _require(sid not in out, "manifest 重复 strategy_id: %s" % sid)
        out[sid] = dict(manifest)
    _require(bool(out), "manifest 目录为空（无可归一策略）: %s" % base)
    return out


# ── 卡片校验 ──────────────────────────────────────────────────────────
def validate_card(
    card: Any,
    manifests: Optional[Mapping[str, Mapping[str, Any]]] = None,
    manifests_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """校验单张 Strategy Card，通过返回原卡；不合法抛 :class:`ValueError`。

    校验项：
    - 15 个归一字段齐全 + 类型/取值域；
    - ``strategy_id`` 必须属于 ``strategies/manifests/`` 的既有策略（未知策略拒绝）；
    - ``health`` 在 Phase 0-A 必须为空 dict（健康度由 Phase 0-B 只读产出）；
    - 与对应 manifest 强一致：version / data_deps / capacity / modes
      （overlay 只做归一，不允许与 manifest 运行口径漂移）。
    """
    _require(isinstance(card, Mapping), "卡片必须为 dict，收到 %s" % type(card).__name__)
    missing = [field for field in CARD_FIELDS if field not in card]
    _require(not missing, "卡片缺字段: %s" % ", ".join(missing))

    sid = card["strategy_id"]
    _require(isinstance(sid, str) and bool(sid.strip()), "strategy_id 必须非空字符串")

    known = manifests if manifests is not None else load_manifests(manifests_dir)
    _require(
        sid in known,
        "未知策略: %s（不在 strategies/manifests/ 的既有策略内: %s）"
        % (sid, ", ".join(sorted(known))),
    )
    manifest = known[sid]

    version = card["version"]
    _require(
        isinstance(version, str) and bool(version.strip()), "version 必须非空字符串"
    )
    owner = card["owner"]
    _require(isinstance(owner, str) and bool(owner.strip()), "owner 必须非空字符串")
    _require(_is_str_list(card["universe"]), "universe 必须为非空无重复字符串 list")
    frequency = card["frequency"]
    _require(
        isinstance(frequency, str) and bool(frequency.strip()), "frequency 必须非空字符串"
    )
    horizon = card["horizon_days"]
    _require(
        isinstance(horizon, int) and not isinstance(horizon, bool) and horizon > 0,
        "horizon_days 必须为正整数，收到 %r" % (horizon,),
    )
    _require(isinstance(card["signal_schema"], Mapping), "signal_schema 必须为 dict")
    _require(
        _is_str_list(card["regime_tags"]), "regime_tags 必须为非空无重复字符串 list"
    )
    _require(
        _is_str_list(card["known_failure_modes"]),
        "known_failure_modes 必须为非空无重复字符串 list",
    )
    _require(_is_str_list(card["data_deps"]), "data_deps 必须为非空无重复字符串 list")
    _require(isinstance(card["params_schema"], Mapping), "params_schema 必须为 dict")

    capacity = card["capacity"]
    _require(isinstance(capacity, Mapping), "capacity 必须为 dict")
    missing_cap = [key for key in CAPACITY_KEYS if key not in capacity]
    _require(not missing_cap, "capacity 缺字段: %s" % ", ".join(missing_cap))
    _require(
        _is_number(capacity["timeout_s"]) and float(capacity["timeout_s"]) > 0,
        "capacity.timeout_s 必须为正数",
    )
    concurrency = capacity["max_concurrency"]
    _require(
        isinstance(concurrency, int) and not isinstance(concurrency, bool) and concurrency > 0,
        "capacity.max_concurrency 必须为正整数",
    )
    ctx_bytes = capacity["max_context_bytes"]
    _require(
        isinstance(ctx_bytes, int) and not isinstance(ctx_bytes, bool) and ctx_bytes > 0,
        "capacity.max_context_bytes 必须为正整数",
    )

    modes = card["modes"]
    _require(_is_str_list(modes), "modes 必须为非空无重复字符串 list")
    bad_modes = [mode for mode in modes if mode not in ALLOWED_MODES]
    _require(not bad_modes, "modes 含非法取值: %s" % ", ".join(bad_modes))

    health = card["health"]
    _require(isinstance(health, Mapping), "health 必须为 dict（Phase 0-A 固定为空）")
    _require(
        len(health) == 0,
        "health 在 Phase 0-A 必须为空 dict（健康度由 Phase 0-B strategy_health 只读产出）",
    )

    # ── 与 manifest 强一致（overlay 只归一，不改运行口径） ──
    _require(
        str(manifest.get("version")) == version,
        "version 与 manifest 不一致: card=%r manifest=%r" % (version, manifest.get("version")),
    )
    manifest_data = manifest.get("allowed_data")
    _require(
        isinstance(manifest_data, list) and set(card["data_deps"]) == set(manifest_data),
        "data_deps 与 manifest.allowed_data 不一致: card=%r manifest=%r"
        % (list(card["data_deps"]), manifest_data),
    )
    manifest_modes = manifest.get("allowed_modes")
    _require(
        isinstance(manifest_modes, list) and set(modes) == set(manifest_modes),
        "modes 与 manifest.allowed_modes 不一致: card=%r manifest=%r"
        % (list(modes), manifest_modes),
    )
    manifest_budget = manifest.get("resource_budget")
    _require(
        isinstance(manifest_budget, Mapping)
        and dict(capacity) == {key: manifest_budget.get(key) for key in CAPACITY_KEYS},
        "capacity 与 manifest.resource_budget 不一致: card=%r manifest=%r"
        % (dict(capacity), manifest_budget),
    )
    return dict(card)


# ── 卡片加载 ──────────────────────────────────────────────────────────
def load_cards(
    path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """加载并校验 10 张 Strategy Card（overlay 只读，不改任何运行文件）。

    Parameters
    ----------
    path : str | None
        overlay 路径，缺省 ``strategy_layer/strategy_cards.json``。
    manifests_dir : str | None
        manifest 目录，缺省 ``strategies/manifests/``；测试可注入临时目录。

    Raises
    ------
    ValueError
        缺文件 / JSON 损坏 / 重复 strategy_id / 未知策略 / 缺卡 / 字段不合规。
    """
    cards_path = path or DEFAULT_CARDS_PATH
    doc = _load_json(cards_path)
    _require(isinstance(doc, Mapping), "overlay 顶层必须为 dict: %s" % cards_path)
    raw_cards = doc.get("cards")
    _require(
        isinstance(raw_cards, list) and bool(raw_cards),
        "overlay 缺非空 cards 列表: %s" % cards_path,
    )
    _require(
        all(isinstance(item, Mapping) for item in raw_cards),
        "overlay.cards 存在非 dict 元素: %s" % cards_path,
    )

    manifests = load_manifests(manifests_dir)
    ids = [str(item.get("strategy_id") or "") for item in raw_cards]
    _require(all(ids), "overlay 存在缺 strategy_id 的卡片: %s" % cards_path)
    dup_ids = sorted({sid for sid in ids if ids.count(sid) > 1})
    _require(not dup_ids, "重复 strategy_id: %s" % ", ".join(dup_ids))

    unknown = sorted(set(ids) - set(manifests))
    _require(not unknown, "未知策略（无 manifest）: %s" % ", ".join(unknown))
    missing = sorted(set(manifests) - set(ids))
    _require(
        not missing,
        "manifest 未归一为卡片（缺卡）: %s" % ", ".join(missing),
    )
    _require(
        len(ids) == EXPECTED_CARD_COUNT,
        "卡片数异常: 期望 %d，实际 %d" % (EXPECTED_CARD_COUNT, len(ids)),
    )

    cards: List[Dict[str, Any]] = []
    for raw_card in raw_cards:
        card = copy.deepcopy(dict(raw_card))
        validate_card(card, manifests=manifests)
        cards.append(card)
    return cards


def load_card_index(
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """``load_cards`` 的按 id 索引形态（重复 id 拒绝），供组合/健康度层消费。"""
    items = list(cards) if cards is not None else load_cards(path, manifests_dir)
    index: Dict[str, Dict[str, Any]] = {}
    for card in items:
        sid = str(card.get("strategy_id") or "")
        _require(sid, "卡片缺 strategy_id")
        _require(sid not in index, "重复 strategy_id: %s" % sid)
        index[sid] = dict(card)
    return index


# ── registry 只读视图 ─────────────────────────────────────────────────
def registry_view(
    cards: Optional[Sequence[Mapping[str, Any]]] = None,
    registry_path: Optional[str] = None,
    cards_path: Optional[str] = None,
    manifests_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """10 张卡 × 当前 ``strategy_registry.json`` 的只读对账视图（基线 10/2/8）。

    只读：不写 registry、不注册 adapter、不改任何运行开关；``enabled`` 为
    registry 当前 enabled 条目数（kg_boost + chan_monitor = 2），
    ``shadow_candidates`` 口径见模块 docstring / ``SHADOW_CANDIDATE_RULE``。

    Returns
    -------
    dict
        total_cards / enabled / enabled_ids / enabled_card_ids /
        registry_only_enabled_ids / shadow_candidates / shadow_candidate_ids /
        non_signal_overlay_ids / default_enable_new_strategies /
        registry_semantics_unchanged / weights / top_n / shadow_candidate_rule /
        card_ids / sources。
    """
    items = list(cards) if cards is not None else load_cards(cards_path, manifests_dir)
    card_ids = [str(card["strategy_id"]) for card in items]
    dup_ids = sorted({sid for sid in card_ids if card_ids.count(sid) > 1})
    _require(not dup_ids, "重复 strategy_id: %s" % ", ".join(dup_ids))

    reg_path = registry_path or DEFAULT_REGISTRY_PATH
    registry = _load_json(reg_path)
    _require(isinstance(registry, Mapping), "registry 顶层必须为 dict: %s" % reg_path)
    raw_enabled = registry.get("enabled")
    _require(
        isinstance(raw_enabled, list),
        "registry.enabled 必须为 list: %s" % reg_path,
    )
    enabled_ids = [str(sid) for sid in raw_enabled]
    enabled_set = set(enabled_ids)
    card_set = set(card_ids)

    enabled_card_ids = [sid for sid in card_ids if sid in enabled_set]
    registry_only_enabled_ids = [sid for sid in enabled_ids if sid not in card_set]
    non_signal_overlay_ids = [
        card["strategy_id"]
        for card in items
        if isinstance(card.get("signal_schema"), Mapping)
        and card["signal_schema"].get("kind") in NON_SIGNAL_SCHEMA_KINDS
    ]
    excluded = enabled_set | set(non_signal_overlay_ids)
    shadow_candidate_ids = [sid for sid in card_ids if sid not in excluded]

    weights = registry.get("weights")
    top_n = registry.get("top_n")
    return {
        "total_cards": len(items),
        "enabled": len(enabled_ids),
        "enabled_ids": enabled_ids,
        "enabled_card_ids": enabled_card_ids,
        "registry_only_enabled_ids": registry_only_enabled_ids,
        "shadow_candidates": len(shadow_candidate_ids),
        "shadow_candidate_ids": shadow_candidate_ids,
        "non_signal_overlay_ids": non_signal_overlay_ids,
        "default_enable_new_strategies": False,
        "registry_semantics_unchanged": True,
        "shadow_candidate_rule": SHADOW_CANDIDATE_RULE,
        "card_ids": card_ids,
        "weights": dict(weights) if isinstance(weights, Mapping) else {},
        "top_n": dict(top_n) if isinstance(top_n, Mapping) else {},
        "sources": {
            "cards": os.path.abspath(cards_path or DEFAULT_CARDS_PATH),
            "manifests": os.path.abspath(manifests_dir or DEFAULT_MANIFESTS_DIR),
            "registry": os.path.abspath(reg_path),
        },
    }


# ── CLI（只读自检） ────────────────────────────────────────────────────
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 0-A Strategy Card 只读自检（10 卡 / enabled=2 / shadow=8）"
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--cards", default="", help="overlay 路径（缺省内置）")
    parser.add_argument("--registry", default="", help="registry 路径（缺省内置，只读）")
    parser.add_argument("--manifests", default="", help="manifest 目录（缺省内置）")
    args = parser.parse_args(argv)

    cards = load_cards(
        path=args.cards or None,
        manifests_dir=args.manifests or None,
    )
    view = registry_view(
        cards=cards,
        registry_path=args.registry or None,
        manifests_dir=args.manifests or None,
    )
    if args.json:
        print(json.dumps({"view": view, "card_ids": view["card_ids"]},
                         ensure_ascii=False, indent=2))
        return 0
    print(
        "[strategy_cards] cards=%d enabled=%d shadow_candidates=%d"
        % (view["total_cards"], view["enabled"], view["shadow_candidates"])
    )
    print(
        "[strategy_cards] enabled=%s enabled_card_ids=%s"
        % (view["enabled_ids"], view["enabled_card_ids"])
    )
    print(
        "[strategy_cards] shadow_candidate_ids=%s"
        % (view["shadow_candidate_ids"],)
    )
    print("[strategy_cards] 只读 overlay：不改 manifest / registry 运行语义，默认不启用新增策略")
    return 0


__all__ = [
    "ALLOWED_MODES",
    "CARD_FIELDS",
    "CAPACITY_KEYS",
    "DEFAULT_CARDS_PATH",
    "DEFAULT_MANIFESTS_DIR",
    "DEFAULT_REGISTRY_PATH",
    "EXPECTED_CARD_COUNT",
    "NON_SIGNAL_SCHEMA_KINDS",
    "SHADOW_CANDIDATE_RULE",
    "load_card_index",
    "load_cards",
    "load_manifests",
    "main",
    "registry_view",
    "validate_card",
]


if __name__ == "__main__":
    sys.exit(main())
