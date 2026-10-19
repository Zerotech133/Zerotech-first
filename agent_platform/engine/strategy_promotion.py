# -*- coding: utf-8 -*-
"""独立策略生命周期晋级门。

本模块不修改 StrategyRegistry，只把 registry 的 experimental/active 状态
映射到 SHADOW/ACTIVE，并以 append-only JSONL 记录已经通过的晋级事件。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping


LIFECYCLE_STATES = (
    "DRAFT",
    "EXPERIMENT",
    "SHADOW",
    "APPROVED",
    "ACTIVE",
    "RETIRED",
)

REGISTRY_STATE_MAP = {
    "experimental": "SHADOW",
    "active": "ACTIVE",
}

LEGAL_TRANSITIONS = {
    "DRAFT": frozenset(("EXPERIMENT",)),
    "EXPERIMENT": frozenset(("SHADOW",)),
    "SHADOW": frozenset(("APPROVED",)),
    "APPROVED": frozenset(("ACTIVE", "RETIRED")),
    "ACTIVE": frozenset(("RETIRED",)),
    "RETIRED": frozenset(),
}

LEDGER_DIR_NAME = "strategy"
LEDGER_FILE_NAME = "strategy_promotions.jsonl"
_REQUIRED_ACTIVE_METRICS = ("sample_size", "win_rate", "max_drawdown")


class StrategyPromotionError(ValueError):
    """策略晋级门配置或账本路径非法。"""


def utc_now_iso() -> str:
    """返回带 UTC 时区的 ISO8601 时间。"""
    return datetime.now(timezone.utc).isoformat()


def map_registry_state(state: str) -> str:
    """把 registry 的生命周期值转换为晋级门的规范状态。"""
    if not isinstance(state, str):
        raise StrategyPromotionError("lifecycle state 必须是字符串")
    value = state.strip()
    if value in REGISTRY_STATE_MAP:
        return REGISTRY_STATE_MAP[value]
    upper = value.upper()
    if upper in LIFECYCLE_STATES:
        return upper
    raise StrategyPromotionError("不支持的 lifecycle state: %r" % state)


def promotion_ledger_path(root) -> Path:
    """返回晋级账本路径；root 必须由调用方显式提供。"""
    if root is None or not str(root).strip():
        raise StrategyPromotionError("root 必须显式提供，禁止写默认项目根")
    return Path(root) / LEDGER_DIR_NAME / LEDGER_FILE_NAME


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _as_non_negative_number(value, field: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyPromotionError("%s 必须是非负数字" % field)
    if value < 0:
        raise StrategyPromotionError("%s 必须是非负数字" % field)
    return value


def _normalise_metrics(metrics) -> dict:
    if metrics is None:
        return {}
    if not isinstance(metrics, Mapping):
        raise StrategyPromotionError("metrics 必须是映射")
    result = dict(metrics)
    aliases = {
        "sample_count": "sample_size",
        "samples": "sample_size",
        "winrate": "win_rate",
        "drawdown": "max_drawdown",
    }
    for source, target in aliases.items():
        if target not in result and source in result:
            result[target] = result[source]
    for field in _REQUIRED_ACTIVE_METRICS:
        if field in result:
            result[field] = _as_non_negative_number(result[field], field)
    if "win_rate" in result and result["win_rate"] > 1:
        raise StrategyPromotionError("win_rate 必须在 [0, 1] 内")
    if "max_drawdown" in result and result["max_drawdown"] > 1:
        raise StrategyPromotionError("max_drawdown 必须在 [0, 1] 内")
    return result


def _normalise_thresholds(thresholds, min_sample_size, min_win_rate,
                          max_drawdown) -> dict:
    result = dict(thresholds or {})
    aliases = {
        "sample_size": "min_sample_size",
        "sample_count": "min_sample_size",
        "win_rate": "min_win_rate",
    }
    for source, target in aliases.items():
        if target not in result and source in result:
            result[target] = result[source]
    if min_sample_size is not None:
        result["min_sample_size"] = min_sample_size
    if min_win_rate is not None:
        result["min_win_rate"] = min_win_rate
    if max_drawdown is not None:
        result["max_drawdown"] = max_drawdown
    if "min_sample_size" in result:
        result["min_sample_size"] = _as_non_negative_number(
            result["min_sample_size"], "min_sample_size")
    if "min_win_rate" in result:
        result["min_win_rate"] = _as_non_negative_number(
            result["min_win_rate"], "min_win_rate")
        if result["min_win_rate"] > 1:
            raise StrategyPromotionError("min_win_rate 必须在 [0, 1] 内")
    if "max_drawdown" in result:
        result["max_drawdown"] = _as_non_negative_number(
            result["max_drawdown"], "max_drawdown")
        if result["max_drawdown"] > 1:
            raise StrategyPromotionError("max_drawdown 必须在 [0, 1] 内")
    return result


def _normalise_refs(evidence_refs) -> list:
    if evidence_refs is None:
        return []
    if isinstance(evidence_refs, (str, bytes)):
        raise StrategyPromotionError("evidence_refs 必须是非字符串序列")
    try:
        refs = list(evidence_refs)
    except TypeError as exc:
        raise StrategyPromotionError("evidence_refs 必须是序列") from exc
    if any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise StrategyPromotionError("evidence_refs 只能包含非空字符串")
    return refs


def _rejected(event_id, strategy_id, version, from_state, to_state,
              metrics, evidence_refs, actor, reason, timestamp) -> dict:
    return {
        "status": "rejected",
        "accepted": False,
        "ok": False,
        "replayed": False,
        "event_id": event_id,
        "strategy_id": strategy_id,
        "version": version,
        "from_state": from_state,
        "to_state": to_state,
        "metrics": metrics,
        "evaluation_metrics": metrics,
        "evidence_refs": evidence_refs,
        "actor": actor,
        "reason": reason,
        "timestamp": timestamp,
        "event": None,
    }


class StrategyPromotionGate:
    """独立、可测的策略生命周期晋级门。"""

    def __init__(self, root):
        self.root = root
        self.ledger_path = promotion_ledger_path(root)

    def _events(self) -> list:
        if not self.ledger_path.exists():
            return []
        events = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        return events

    @staticmethod
    def _identity(event: Mapping[str, Any]) -> dict:
        return {key: event.get(key) for key in (
            "event_id", "strategy_id", "version", "from_state", "to_state",
            "metrics", "evaluation_metrics", "thresholds", "evidence_refs",
            "actor", "reason")}

    def _replay_if_present(self, event_id, candidate) -> dict | None:
        if not event_id:
            return None
        for event in self._events():
            if event.get("event_id") != event_id:
                continue
            if self._identity(event) == self._identity(candidate):
                return {
                    "status": "replayed",
                    "accepted": True,
                    "ok": True,
                    "replayed": True,
                    "event_id": event_id,
                    "strategy_id": event.get("strategy_id"),
                    "version": event.get("version"),
                    "from_state": event.get("from_state"),
                    "to_state": event.get("to_state"),
                    "reason": event.get("reason", ""),
                    "timestamp": event.get("timestamp"),
                    "event": event,
                }
            return _rejected(
                event_id, candidate.get("strategy_id", ""),
                candidate.get("version", ""), candidate.get("from_state", ""),
                candidate.get("to_state", ""), candidate.get("metrics", {}),
                candidate.get("evidence_refs", []), candidate.get("actor", ""),
                "event_id 已存在但事件内容不同，拒绝覆盖", candidate.get("timestamp"),
            )
        return None

    def promote(self, strategy_id, version, from_state, to_state, metrics=None,
                evidence_refs=None, actor="", reason="", timestamp=None,
                event_id=None, thresholds=None, min_sample_size=None,
                min_win_rate=None, max_drawdown=None) -> dict:
        """尝试晋级，返回 accepted/rejected/replayed 结果，不静默吞掉拒绝。"""
        timestamp = timestamp or utc_now_iso()
        metrics_out = dict(metrics) if isinstance(metrics, Mapping) else metrics
        refs_out = list(evidence_refs) if evidence_refs is not None and not isinstance(
            evidence_refs, (str, bytes)) else evidence_refs
        if not isinstance(event_id, str) or not event_id.strip():
            return _rejected(event_id, strategy_id, version, from_state, to_state,
                             metrics_out, refs_out or [], actor,
                             "event_id 必须显式提供", timestamp)
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            return _rejected(event_id, strategy_id, version, from_state, to_state,
                             metrics_out, refs_out or [], actor,
                             "strategy_id 必须是非空字符串", timestamp)
        if not isinstance(version, str) or not version.strip():
            return _rejected(event_id, strategy_id, version, from_state, to_state,
                             metrics_out, refs_out or [], actor,
                             "version 必须是非空字符串", timestamp)
        if not isinstance(actor, str) or not actor.strip():
            return _rejected(event_id, strategy_id, version, from_state, to_state,
                             metrics_out, refs_out or [], actor,
                             "actor 必须是非空字符串", timestamp)
        if not isinstance(reason, str) or not reason.strip():
            return _rejected(event_id, strategy_id, version, from_state, to_state,
                             metrics_out, refs_out or [], actor,
                             "reason 必须是非空字符串", timestamp)
        try:
            source = map_registry_state(from_state)
            target = map_registry_state(to_state)
            metrics_out = _normalise_metrics(metrics)
            refs_out = _normalise_refs(evidence_refs)
            thresholds_out = _normalise_thresholds(
                thresholds, min_sample_size, min_win_rate, max_drawdown)
        except StrategyPromotionError as exc:
            return _rejected(event_id, strategy_id, version, from_state, to_state,
                             metrics_out or {}, refs_out or [], actor, str(exc),
                             timestamp)

        candidate = {
            "event_id": event_id,
            "event_type": "strategy_promotion",
            "strategy_id": strategy_id,
            "version": version,
            "from_state": source,
            "to_state": target,
            "metrics": metrics_out,
            "evaluation_metrics": metrics_out,
            "thresholds": thresholds_out,
            "evidence_refs": refs_out,
            "actor": actor,
            "reason": reason,
            "timestamp": timestamp,
        }
        replay = self._replay_if_present(event_id, candidate)
        if replay is not None:
            return replay

        if target not in LEGAL_TRANSITIONS.get(source, frozenset()):
            return _rejected(event_id, strategy_id, version, source, target,
                             metrics_out, refs_out, actor,
                             "非法生命周期迁移: %s -> %s" % (source, target),
                             timestamp)
        if target == "ACTIVE":
            missing_metrics = [field for field in _REQUIRED_ACTIVE_METRICS
                               if field not in metrics_out]
            missing_thresholds = [field for field in (
                "min_sample_size", "min_win_rate", "max_drawdown")
                if field not in thresholds_out]
            if refs_out == []:
                return _rejected(event_id, strategy_id, version, source, target,
                                 metrics_out, refs_out, actor,
                                 "进入 ACTIVE 必须提供 evidence_refs", timestamp)
            if missing_metrics:
                return _rejected(event_id, strategy_id, version, source, target,
                                 metrics_out, refs_out, actor,
                                 "进入 ACTIVE 缺少评估指标: %s" % ",".join(missing_metrics),
                                 timestamp)
            if missing_thresholds:
                return _rejected(event_id, strategy_id, version, source, target,
                                 metrics_out, refs_out, actor,
                                 "进入 ACTIVE 缺少调用方阈值: %s" % ",".join(missing_thresholds),
                                 timestamp)
            failures = []
            if metrics_out["sample_size"] < thresholds_out["min_sample_size"]:
                failures.append("sample_size 未达到阈值")
            if metrics_out["win_rate"] < thresholds_out["min_win_rate"]:
                failures.append("win_rate 未达到阈值")
            if metrics_out["max_drawdown"] > thresholds_out["max_drawdown"]:
                failures.append("max_drawdown 超过阈值")
            if failures:
                return _rejected(event_id, strategy_id, version, source, target,
                                 metrics_out, refs_out, actor,
                                 "ACTIVE 门禁拒绝: %s" % "; ".join(failures), timestamp)

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(_canonical_json(candidate) + "\n")
        return {
            "status": "accepted",
            "accepted": True,
            "ok": True,
            "replayed": False,
            "event_id": event_id,
            "strategy_id": strategy_id,
            "version": version,
            "from_state": source,
            "to_state": target,
            "reason": reason,
            "timestamp": timestamp,
            "event": candidate,
        }

    transition = promote

    def load_events(self) -> list:
        """返回账本事件快照，不修改账本。"""
        return self._events()


__all__ = [
    "LEGAL_TRANSITIONS",
    "LIFECYCLE_STATES",
    "LEDGER_FILE_NAME",
    "REGISTRY_STATE_MAP",
    "StrategyPromotionError",
    "StrategyPromotionGate",
    "map_registry_state",
    "promotion_ledger_path",
    "utc_now_iso",
]
