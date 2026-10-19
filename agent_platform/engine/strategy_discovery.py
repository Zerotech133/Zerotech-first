# -*- coding: utf-8 -*-
"""显式策略清单驱动的 StrategyAgent 发现与装配。"""
import importlib
import json
from pathlib import Path
import sys

try:
    from strategy_registry import StrategyAgent, StrategyError
except ImportError:  # pragma: no cover - 包方式导入时使用
    from .strategy_registry import StrategyAgent, StrategyError


class StrategyDiscoveryError(StrategyError):
    """策略清单或插件装配失败。"""


def _non_empty_string(value, field, index):
    if not isinstance(value, str) or not value.strip():
        raise StrategyDiscoveryError(
            "清单第 %d 项 %s 必须是非空字符串" % (index, field))
    return value


def _load_entries(manifest_path):
    path = Path(manifest_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StrategyDiscoveryError(
            "无法读取策略清单 %s: %s" % (path, exc)) from exc
    except (TypeError, ValueError) as exc:
        raise StrategyDiscoveryError(
            "策略清单 %s 不是有效 JSON: %s" % (path, exc)) from exc

    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict) and isinstance(payload.get("strategies"), list):
        entries = payload["strategies"]
    else:
        raise StrategyDiscoveryError(
            "策略清单必须是插件项数组，或包含 strategies 数组")
    return entries


def _module_allowed(module_name, allowed_modules, allowed_packages):
    if allowed_modules is not None and module_name not in set(allowed_modules):
        return False
    if allowed_packages is not None:
        packages = tuple(allowed_packages)
        if not any(module_name == package or module_name.startswith(package + ".")
                   for package in packages):
            return False
    return True


def load_manifest(manifest_path, registry, allowed_modules=None,
                  allowed_packages=None):
    """按显式 JSON 清单导入并注册策略插件。

    每项清单必须声明 ``module``、``class``、``strategy_id``。不扫描目录、
    不猜测插件；可用 ``allowed_modules`` 或 ``allowed_packages`` 限制导入边界。
    成功返回传入的 ``registry``，失败抛出带清单项上下文的
    ``StrategyDiscoveryError``。
    """
    if registry is None or not hasattr(registry, "register"):
        raise StrategyDiscoveryError("registry 必须提供 register(agent) 方法")

    entries = _load_entries(manifest_path)
    manifest_path = Path(manifest_path)
    plugin_root = manifest_path.parent.resolve()
    plugin_root_text = str(plugin_root)
    added_plugin_path = plugin_root_text not in sys.path
    if added_plugin_path:
        sys.path.insert(0, plugin_root_text)
    loaded = []
    seen_ids = set()
    registered_ids = set(registry.list_ids()) if hasattr(registry, "list_ids") else set()

    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise StrategyDiscoveryError("清单第 %d 项必须是对象" % index)
        module_name = _non_empty_string(entry.get("module"), "module", index)
        class_name = _non_empty_string(entry.get("class"), "class", index)
        strategy_id = _non_empty_string(entry.get("strategy_id"), "strategy_id", index)

        if not _module_allowed(module_name, allowed_modules, allowed_packages):
            raise StrategyDiscoveryError(
                "清单第 %d 模块 %s 不在允许的导入范围内" % (index, module_name))
        if strategy_id in seen_ids or strategy_id in registered_ids:
            raise StrategyDiscoveryError(
                "清单第 %d strategy_id=%s 重复" % (index, strategy_id))
        seen_ids.add(strategy_id)

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise StrategyDiscoveryError(
                "清单第 %d 无法导入模块 %s: %s" % (index, module_name, exc)) from exc
        try:
            agent_class = getattr(module, class_name)
        except AttributeError as exc:
            raise StrategyDiscoveryError(
                "清单第 %d 模块 %s 不存在类 %s" %
                (index, module_name, class_name)) from exc
        if not isinstance(agent_class, type) or agent_class is StrategyAgent \
                or not issubclass(agent_class, StrategyAgent):
            raise StrategyDiscoveryError(
                "清单第 %d 类 %s.%s 非 StrategyAgent 子类" %
                (index, module_name, class_name))

        try:
            agent = agent_class()
        except Exception as exc:
            raise StrategyDiscoveryError(
                "清单第 %d 无法实例化 %s.%s: %s" %
                (index, module_name, class_name, exc)) from exc
        actual_id = getattr(agent, "strategy_id", None)
        if actual_id != strategy_id:
            raise StrategyDiscoveryError(
                "清单第 %d strategy_id=%s 与实例 ID=%s 不一致" %
                (index, strategy_id, actual_id))
        loaded.append(agent)

    for agent in loaded:
        try:
            registry.register(agent)
        except Exception as exc:
            raise StrategyDiscoveryError(
                "装配 strategy_id=%s 失败: %s" % (agent.strategy_id, exc)) from exc
    return registry


__all__ = ["StrategyDiscoveryError", "load_manifest"]
