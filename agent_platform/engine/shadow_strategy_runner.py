# -*- coding: utf-8 -*-
"""独立的 SHADOW 验证入口。

本模块只把显式策略清单、显式策略配置、内存注册表、调度器和仲裁器
串成一个只读验证链。默认只返回内存结果，不写策略账本、不写交易账本、
不调用任何交易执行接口。
"""
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
from collections.abc import Mapping

try:  # 直接从 engine 目录导入
    from strategy_discovery import load_manifest
    from strategy_registry import StrategyRegistry
    from strategy_config import load_config, StrategyConfigError
    from strategy_scheduler import MultiStrategyScheduler
    from strategy_evaluation import arbitrate_strategy_results
except ImportError:  # pragma: no cover - 包方式导入
    from .strategy_discovery import load_manifest
    from .strategy_registry import StrategyRegistry
    from .strategy_config import load_config, StrategyConfigError
    from .strategy_scheduler import MultiStrategyScheduler
    from .strategy_evaluation import arbitrate_strategy_results


SHADOW_MODE = "SHADOW"


class ShadowValidationError(ValueError):
    """SHADOW 验证入口的边界或配置绑定错误。"""


def _explicit_path(value, field):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ShadowValidationError("必须显式提供 %s" % field)
    try:
        return Path(value)
    except TypeError as exc:
        raise ShadowValidationError("%s 必须是有效路径" % field) from exc


def _context_bytes(context):
    try:
        encoded = json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ShadowValidationError("context 必须可序列化为 JSON") from exc
    return len(encoded)


def _bind_config(registry, config, mode, context_bytes):
    registry_ids = registry.list_ids()
    config_ids = config.list_ids()
    if registry_ids != config_ids:
        raise ShadowValidationError(
            "manifest/config strategy_id 不匹配：manifest=%s config=%s"
            % (registry_ids, config_ids)
        )

    authorizations = {}
    for strategy_id in registry_ids:
        agent = registry.get(strategy_id)
        try:
            config.validate_agent(agent)
            authorizations[strategy_id] = config.bind_for_run(
                registry, strategy_id, mode, context_bytes)
        except StrategyConfigError as exc:
            raise ShadowValidationError(
                "strategy_id=%s 配置不匹配：%s" % (strategy_id, exc)
            ) from exc
    return authorizations


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _append_evidence(path, root, result):
    """仅在调用方显式给出 evidence_path 时追加一条 JSONL 证据。"""
    if root is None:
        raise ShadowValidationError("写证据时必须显式提供 root")
    root_path = _explicit_path(root, "root").resolve()
    evidence_path = _explicit_path(path, "evidence_path").resolve()
    try:
        evidence_path.relative_to(root_path)
    except ValueError as exc:
        raise ShadowValidationError("evidence_path 必须位于 root 内") from exc
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_jsonable(result), ensure_ascii=False) + "\n")
    return str(evidence_path)


def run_shadow_validation(
    manifest_path,
    config_path,
    root,
    context,
    trade_mode,
    *,
    evidence_path=None,
    max_workers=None,
    overall_timeout_s=None,
):
    """执行一次纯内存 SHADOW 验证。

    参数均为显式输入；``root`` 只作为结果中的隔离根标识，默认不会触碰
    文件系统。``evidence_path`` 只有在调用方明确要求时才会以 append-only
    JSONL 写入，并且必须位于显式 ``root`` 下。
    """
    manifest = _explicit_path(manifest_path, "manifest_path")
    config_file = _explicit_path(config_path, "config_path")
    root_path = _explicit_path(root, "root")
    if trade_mode != SHADOW_MODE:
        raise ShadowValidationError(
            "独立 SHADOW 验证只接受 trade_mode=%s，实际为 %s"
            % (SHADOW_MODE, trade_mode)
        )
    if not isinstance(context, Mapping):
        raise ShadowValidationError("context 必须是映射")

    context_bytes = _context_bytes(context)
    registry = load_manifest(manifest, StrategyRegistry())
    config = load_config(config_file)
    authorizations = _bind_config(registry, config, trade_mode, context_bytes)

    timeouts = {
        strategy_id: authorization.resource_budget["timeout_s"]
        for strategy_id, authorization in authorizations.items()
    }
    configured_workers = max_workers
    if configured_workers is None:
        configured_workers = max(
            authorization.resource_budget["max_concurrency"]
            for authorization in authorizations.values()
        )
    scheduler = MultiStrategyScheduler(
        registry,
        max_workers=configured_workers,
        overall_timeout_s=overall_timeout_s,
        allowed_lifecycle=("experimental", "active"),
    )
    strategy_results = scheduler.run(
        registry.list_ids(),
        context,
        strategy_timeouts=timeouts,
    )
    arbitration = arbitrate_strategy_results(strategy_results)
    result = {
        "mode": SHADOW_MODE,
        "strategy_results": strategy_results,
        "arbitration": arbitration,
        "config_path": str(config_file),
        "manifest_path": str(manifest),
        "root": str(root_path),
        "authorizations": {
            strategy_id: authorization.as_dict()
            for strategy_id, authorization in authorizations.items()
        },
        "no_execution": True,
        "execution_allowed": False,
    }
    if evidence_path is not None:
        result["evidence_path"] = _append_evidence(evidence_path, root_path, result)
    return result


__all__ = ["SHADOW_MODE", "ShadowValidationError", "run_shadow_validation"]
