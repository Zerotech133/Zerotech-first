# -*- coding: utf-8 -*-
"""策略显式 JSON 配置清单与运行前安全校验。

本模块只读取显式指定的 JSON 文件，不发现、不修改注册表，也不接交易执行。
清单中的每个策略必须绑定 strategy_id/version，并声明数据、资源、模式和权限。
"""

from dataclasses import dataclass
import json
import math
from pathlib import Path
from types import MappingProxyType


CONFIG_FILENAME = "strategy_config.json"
ALLOWED_MODES = frozenset(("BACKTEST", "REPLAY", "SHADOW", "SIM_MARKET"))
ALLOWED_PERMISSIONS = frozenset(("propose", "read_context"))
FORBIDDEN_PERMISSIONS = frozenset(("order", "account", "ledger_write"))
_REQUIRED_FIELDS = frozenset(
    (
        "strategy_id",
        "version",
        "allowed_data",
        "resource_budget",
        "allowed_modes",
        "permissions",
    )
)
_RESOURCE_FIELDS = frozenset(("timeout_s", "max_concurrency", "max_context_bytes"))


class StrategyConfigError(ValueError):
    """策略配置非法、缺失或不允许运行。"""


@dataclass(frozen=True)
class StrategyManifest:
    strategy_id: str
    version: str
    allowed_data: tuple
    resource_budget: MappingProxyType
    allowed_modes: frozenset
    permissions: frozenset

    def validate_for_run(self, mode, context_bytes):
        _validate_mode(mode)
        _validate_context_bytes(context_bytes)
        if mode not in self.allowed_modes:
            raise StrategyConfigError(
                "strategy_id=%s mode=%s 不在 allowed_modes=%s"
                % (self.strategy_id, mode, sorted(self.allowed_modes))
            )
        limit = self.resource_budget["max_context_bytes"]
        if context_bytes > limit:
            raise StrategyConfigError(
                "strategy_id=%s context_bytes=%s 超过 max_context_bytes=%s"
                % (self.strategy_id, context_bytes, limit)
            )
        return RunAuthorization(
            strategy_id=self.strategy_id,
            version=self.version,
            mode=mode,
            context_bytes=context_bytes,
            resource_budget=MappingProxyType(dict(self.resource_budget)),
            allowed_modes=frozenset(self.allowed_modes),
            permissions=frozenset(self.permissions),
            allowed_data=tuple(self.allowed_data),
        )

    def validate_agent(self, agent):
        """校验旧 Agent 的身份绑定，不要求 Agent 继承新类型。"""
        strategy_id = getattr(agent, "strategy_id", None)
        version = getattr(agent, "version", None)
        if strategy_id != self.strategy_id or version != self.version:
            raise StrategyConfigError(
                "Agent 身份不匹配：期望 %s/%s，实际 %s/%s"
                % (self.strategy_id, self.version, strategy_id, version)
            )
        return self


@dataclass(frozen=True)
class RunAuthorization:
    """通过配置门禁后的运行授权快照；不包含任何执行能力。"""

    strategy_id: str
    version: str
    mode: str
    context_bytes: int
    resource_budget: MappingProxyType
    allowed_modes: frozenset
    permissions: frozenset
    allowed_data: tuple

    def __post_init__(self):
        object.__setattr__(
            self, "resource_budget", MappingProxyType(dict(self.resource_budget))
        )
        object.__setattr__(self, "allowed_modes", frozenset(self.allowed_modes))
        object.__setattr__(self, "permissions", frozenset(self.permissions))
        object.__setattr__(self, "allowed_data", tuple(self.allowed_data))
        invalid_modes = self.allowed_modes - ALLOWED_MODES
        if invalid_modes:
            raise StrategyConfigError(
                "运行授权 allowed_modes 含非法模式 %s" % sorted(invalid_modes)
            )
        if set(self.resource_budget) != _RESOURCE_FIELDS:
            raise StrategyConfigError("运行授权 resource_budget 字段不完整或含未知字段")
        timeout_s = self.resource_budget["timeout_s"]
        max_concurrency = self.resource_budget["max_concurrency"]
        max_context_bytes = self.resource_budget["max_context_bytes"]
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise StrategyConfigError("运行授权 timeout_s 必须为有限正数")
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency <= 0
        ):
            raise StrategyConfigError("运行授权 max_concurrency 必须为正整数")
        if (
            isinstance(max_context_bytes, bool)
            or not isinstance(max_context_bytes, int)
            or max_context_bytes <= 0
        ):
            raise StrategyConfigError("运行授权 max_context_bytes 必须为正整数")
        if self.permissions != ALLOWED_PERMISSIONS:
            forbidden = self.permissions & FORBIDDEN_PERMISSIONS
            if forbidden:
                raise StrategyConfigError(
                    "运行授权禁止 %s；权限必须且只能包含 propose、read_context"
                    % ",".join(sorted(forbidden))
                )
            raise StrategyConfigError(
                "运行授权权限必须且只能包含 propose、read_context"
            )
        if self.mode not in self.allowed_modes or self.mode not in ALLOWED_MODES:
            raise StrategyConfigError("运行授权 mode 不在 allowed_modes")

    def as_dict(self):
        return {
            "strategy_id": self.strategy_id,
            "version": self.version,
            "mode": self.mode,
            "context_bytes": self.context_bytes,
            "resource_budget": dict(self.resource_budget),
            "allowed_modes": sorted(self.allowed_modes),
            "permissions": sorted(self.permissions),
            "allowed_data": list(self.allowed_data),
        }

    def bind_registry(self, registry):
        """将本快照绑定到同身份、同版本的 Registry 插件。"""
        if registry is None or not hasattr(registry, "bind_authorization"):
            raise StrategyConfigError("registry 必须提供 bind_authorization 方法")
        try:
            return registry.bind_authorization(self)
        except Exception as exc:
            if isinstance(exc, StrategyConfigError):
                raise
            raise StrategyConfigError(
                "strategy_id=%s 运行授权绑定失败：%s" % (self.strategy_id, exc)
            ) from exc


class StrategyConfig:
    """只读策略配置清单。"""

    def __init__(self, source_path, manifests):
        self.path = Path(source_path)
        self._manifests = MappingProxyType(dict(manifests))

    @property
    def strategies(self):
        return self._manifests

    def get(self, strategy_id):
        try:
            return self._manifests[strategy_id]
        except KeyError as exc:
            raise StrategyConfigError(
                "strategy_id=%s 未在配置清单中声明" % strategy_id
            ) from exc

    def validate_for_run(self, strategy_id, mode, context_bytes):
        return self.get(strategy_id).validate_for_run(mode, context_bytes)

    def bind_for_run(self, registry, strategy_id, mode, context_bytes):
        """校验并将运行授权快照绑定到 Registry。"""
        authorization = self.validate_for_run(strategy_id, mode, context_bytes)
        return authorization.bind_registry(registry)

    def validate_agent(self, agent):
        return self.get(getattr(agent, "strategy_id", None)).validate_agent(agent)

    def list_ids(self):
        return sorted(self._manifests)


def _error(path, message):
    raise StrategyConfigError("配置 %s 非法：%s" % (path, message))


def _validate_string(value, field, path):
    if not isinstance(value, str) or not value.strip():
        _error(path, "%s 必须为非空字符串" % field)
    return value


def _validate_string_list(value, field, path, allow_empty=False):
    if not isinstance(value, list):
        _error(path, "%s 必须为 JSON 数组" % field)
    if not allow_empty and not value:
        _error(path, "%s 不能为空" % field)
    if any(not isinstance(item, str) or not item.strip() for item in value):
        _error(path, "%s 必须只包含非空字符串" % field)
    if len(set(value)) != len(value):
        _error(path, "%s 不允许重复项" % field)
    return tuple(value)


def _validate_mode(mode):
    if not isinstance(mode, str) or mode not in ALLOWED_MODES:
        raise StrategyConfigError(
            "mode=%s 不在允许模式 %s" % (mode, sorted(ALLOWED_MODES))
        )


def _validate_context_bytes(context_bytes):
    if (
        isinstance(context_bytes, bool)
        or not isinstance(context_bytes, int)
        or context_bytes < 0
    ):
        raise StrategyConfigError("context_bytes 必须为非负整数")


def _manifest(raw, index, source_path):
    path = "%s.strategies[%s]" % (source_path, index)
    if not isinstance(raw, dict):
        _error(path, "必须为 JSON 对象")
    missing = _REQUIRED_FIELDS - set(raw)
    if missing:
        _error(path, "缺少字段 %s" % sorted(missing))
    unknown = set(raw) - _REQUIRED_FIELDS
    if unknown:
        _error(path, "存在未声明字段 %s" % sorted(unknown))

    strategy_id = _validate_string(raw["strategy_id"], "strategy_id", path)
    version = _validate_string(raw["version"], "version", path)
    allowed_data = _validate_string_list(raw["allowed_data"], "allowed_data", path)
    allowed_modes = _validate_string_list(raw["allowed_modes"], "allowed_modes", path)
    invalid_modes = set(allowed_modes) - ALLOWED_MODES
    if invalid_modes:
        _error(path, "allowed_modes 含非法模式 %s" % sorted(invalid_modes))

    permissions = _validate_string_list(raw["permissions"], "permissions", path)
    invalid_permissions = set(permissions) - ALLOWED_PERMISSIONS
    forbidden = set(permissions) & FORBIDDEN_PERMISSIONS
    if invalid_permissions:
        _error(path, "permissions 含未授权权限 %s" % sorted(invalid_permissions))
    if forbidden:
        _error(path, "permissions 明确禁止 %s" % sorted(forbidden))
    if set(permissions) != ALLOWED_PERMISSIONS:
        _error(path, "permissions 必须且只能包含 propose、read_context")

    budget = raw["resource_budget"]
    if not isinstance(budget, dict):
        _error(path, "resource_budget 必须为 JSON 对象")
    missing_budget = _RESOURCE_FIELDS - set(budget)
    if missing_budget:
        _error(path, "resource_budget 缺少字段 %s" % sorted(missing_budget))
    unknown_budget = set(budget) - _RESOURCE_FIELDS
    if unknown_budget:
        _error(path, "resource_budget 存在未声明字段 %s" % sorted(unknown_budget))

    timeout_s = budget["timeout_s"]
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        _error(path, "resource_budget.timeout_s 必须为有限正数")
    max_concurrency = budget["max_concurrency"]
    if (
        isinstance(max_concurrency, bool)
        or not isinstance(max_concurrency, int)
        or max_concurrency <= 0
    ):
        _error(path, "resource_budget.max_concurrency 必须为正整数")
    max_context_bytes = budget["max_context_bytes"]
    if (
        isinstance(max_context_bytes, bool)
        or not isinstance(max_context_bytes, int)
        or max_context_bytes <= 0
    ):
        _error(path, "resource_budget.max_context_bytes 必须为正整数")

    return StrategyManifest(
        strategy_id=strategy_id,
        version=version,
        allowed_data=allowed_data,
        resource_budget=MappingProxyType(
            {
                "timeout_s": timeout_s,
                "max_concurrency": max_concurrency,
                "max_context_bytes": max_context_bytes,
            }
        ),
        allowed_modes=frozenset(allowed_modes),
        permissions=frozenset(permissions),
    )


def _config_path(root_or_path):
    if root_or_path is None or (
        isinstance(root_or_path, str) and not root_or_path.strip()
    ):
        raise StrategyConfigError("必须显式提供 root 或配置文件路径")
    try:
        raw_path = Path(root_or_path)
    except TypeError as exc:
        raise StrategyConfigError("root 或配置文件路径必须是有效路径") from exc
    if raw_path.is_dir() or raw_path.suffix.lower() != ".json":
        return raw_path / CONFIG_FILENAME
    return raw_path


def load_config(root_or_path):
    """从显式文件或显式根目录只读加载 strategy_config.json。"""
    path = _config_path(root_or_path)
    if not path.exists():
        raise StrategyConfigError("配置文件不存在：%s" % path)
    if not path.is_file():
        raise StrategyConfigError("配置路径不是文件：%s" % path)
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StrategyConfigError("配置文件读取失败 %s：%s" % (path, exc)) from exc

    if not isinstance(raw, dict):
        _error(path, "顶层必须为 JSON 对象")
    if set(raw) != {"strategies"}:
        _error(path, "顶层只允许字段 strategies")
    strategies = raw["strategies"]
    if not isinstance(strategies, list) or not strategies:
        _error(path, "strategies 必须为非空 JSON 数组")

    manifests = {}
    for index, item in enumerate(strategies):
        manifest = _manifest(item, index, path)
        if manifest.strategy_id in manifests:
            _error(path, "strategy_id=%s 重复声明" % manifest.strategy_id)
        manifests[manifest.strategy_id] = manifest
    return StrategyConfig(path, manifests)


def validate_for_run(config, strategy_id, mode, context_bytes):
    """模块级校验入口；config 必须是 load_config 返回的清单。"""
    if not isinstance(config, StrategyConfig):
        raise StrategyConfigError("config 必须是 load_config 返回的 StrategyConfig")
    return config.validate_for_run(strategy_id, mode, context_bytes)


def bind_for_run(config, registry, strategy_id, mode, context_bytes):
    """模块级运行授权绑定入口。"""
    if not isinstance(config, StrategyConfig):
        raise StrategyConfigError("config 必须是 load_config 返回的 StrategyConfig")
    return config.bind_for_run(registry, strategy_id, mode, context_bytes)


__all__ = [
    "ALLOWED_MODES",
    "ALLOWED_PERMISSIONS",
    "CONFIG_FILENAME",
    "FORBIDDEN_PERMISSIONS",
    "RunAuthorization",
    "StrategyConfig",
    "StrategyConfigError",
    "StrategyManifest",
    "bind_for_run",
    "load_config",
    "validate_for_run",
]
