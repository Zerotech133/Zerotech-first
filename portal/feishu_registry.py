# -*- coding: utf-8 -*-
"""飞书机器人注册表与路由表（邓芝台 · W1 底座，2026-10-24）。

唯一权威：
  portal/data/feishu/registry.json —— 角色/机器人台账（新增机器人只改这里）
  portal/data/feishu/routes.json   —— 入向路由（W43 受理后 domain→role→intent 两级路由）

设计约束（总纲 v1）：
  · 一个机器人只做一件事：role 唯一、display 唯一；
  · 通道策略固定：入向 W43、出向 8097、禁直连 OpenAPI、卡片动作走注册表；
  · 本模块只读文件、纯标准库、无副作用，供 bot/管理台/W43 共同引用。

用法：
  python feishu_registry.py --check        # 校验并打印摘要（管理台/CI 用，退出码 0/1）
  from feishu_registry import load_registry, load_routes, match_route
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.join(HERE, "data", "feishu", "registry.json")
DEFAULT_ROUTES = os.path.join(HERE, "data", "feishu", "routes.json")

DOMAINS = ("owner", "ops", "customer")
REQUIRED_ROLE_FIELDS = (
    "role",
    "display",
    "domain",
    "events",
    "perms",
    "group_policy",
    "status",
)
VALID_STATUS = ("planned", "active", "active_rename_pending", "reserved", "disabled")


class RegistryError(ValueError):
    """注册表/路由表不合法（配置错误必须显式失败，不静默降级）。"""


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        raise RegistryError("读不到 %s：%s" % (path, e))
    except ValueError as e:
        raise RegistryError("%s 不是合法 JSON：%s" % (path, e))


def validate_registry(data):
    """校验角色台账；返回 (roles_by_id, warnings)。非法即抛 RegistryError。"""
    if not isinstance(data, dict):
        raise RegistryError("registry 顶层必须是对象")
    if data.get("schema") != "zb.feishu.registry.v1":
        raise RegistryError("registry schema 版本不对：%r" % data.get("schema"))
    roles = data.get("roles")
    if not isinstance(roles, list) or not roles:
        raise RegistryError("registry.roles 必须是非空数组")
    seen, warnings, index = set(), [], {}
    for r in roles:
        if not isinstance(r, dict):
            raise RegistryError("roles 元素必须是对象")
        for field in REQUIRED_ROLE_FIELDS:
            if field not in r:
                raise RegistryError("角色缺字段 %s：%r" % (field, r.get("role")))
        role = str(r["role"]).strip()
        if not role.startswith("zb.") or role.count(".") != 2:
            raise RegistryError("角色 ID 必须形如 zb.<domain>.<role>：%r" % role)
        if role in seen:
            raise RegistryError("角色 ID 重复：%s" % role)
        seen.add(role)
        for d in r["domain"]:
            if d not in DOMAINS:
                raise RegistryError("角色 %s 的 domain 非法：%r" % (role, d))
        if r["status"] not in VALID_STATUS:
            raise RegistryError("角色 %s 的 status 非法：%r" % (role, r["status"]))
        if not str(r["display"]).startswith("零界·"):
            warnings.append(
                "角色 %s 的 display 未按'零界·<职能>'命名：%r" % (role, r["display"])
            )
        index[role] = r
    policy = data.get("channel_policy") or {}
    if policy.get("outbound") != "8097 only":
        raise RegistryError("channel_policy.outbound 必须固定为 '8097 only'")
    if policy.get("inbound") != "W43 only":
        raise RegistryError("channel_policy.inbound 必须固定为 'W43 only'")
    if policy.get("direct_openapi") != "forbidden":
        raise RegistryError("channel_policy.direct_openapi 必须为 forbidden")
    groups = data.get("group_whitelist") or []
    if not isinstance(groups, list):
        raise RegistryError("group_whitelist 必须是数组")
    for g in groups:
        if not isinstance(g, dict) or not g.get("name") or not g.get("chat_id"):
            raise RegistryError("group_whitelist 条目需要 name+chat_id：%r" % (g,))
    return index, warnings


def validate_routes(data, roles_index=None):
    """校验路由表；返回按 priority 降序排好的规则列表。"""
    if not isinstance(data, dict) or data.get("schema") != "zb.feishu.routes.v1":
        raise RegistryError("routes schema 版本不对")
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        raise RegistryError("routes 必须是非空数组")
    seen = set()
    for r in routes:
        for field in ("id", "match", "handler", "priority"):
            if field not in r:
                raise RegistryError("路由缺字段 %s：%r" % (field, r.get("id")))
        if r["id"] in seen:
            raise RegistryError("路由 id 重复：%s" % r["id"])
        seen.add(r["id"])
        if not isinstance(r["match"], dict):
            raise RegistryError("路由 %s 的 match 必须是对象" % r["id"])
        if not str(r["handler"]).strip():
            raise RegistryError("路由 %s 缺 handler" % r["id"])
        role = r["match"].get("role")
        if roles_index is not None and role and role not in roles_index:
            raise RegistryError("路由 %s 引用了未注册角色：%s" % (r["id"], role))
    return sorted(routes, key=lambda x: -int(x["priority"]))


def load_registry(path=None):
    return validate_registry(_read_json(path or DEFAULT_REGISTRY))


def load_routes(path=None, roles_index=None):
    return validate_routes(_read_json(path or DEFAULT_ROUTES), roles_index)


def match_route(routes, role="", domain="", intent="", chat_type=""):
    """按 priority 从高到低匹配；match 中每个键都要等于传入值（空 match 为兜底）。

    返回命中的 handler 字符串；全不中返回 None。
    """
    ctx = {"role": role, "domain": domain, "intent": intent, "chat": chat_type}
    for rule in routes:
        ok = True
        for key, want in rule["match"].items():
            if ctx.get(key) != want:
                ok = False
                break
        if ok:
            return str(rule["handler"])
    return None


def summary():
    roles, warnings = load_registry()
    routes = load_routes(roles_index=roles)
    lines = [
        "registry: %d 个角色, %d 条路由, %d 个白名单群"
        % (
            len(roles),
            len(routes),
            len(_read_json(DEFAULT_REGISTRY).get("group_whitelist") or []),
        )
    ]
    for rid, r in roles.items():
        lines.append(
            "  %-20s %-8s %-22s %s"
            % (rid, r["display"], "/".join(r["domain"]), r["status"])
        )
    for w in warnings:
        lines.append("  WARN " + w)
    return "\n".join(lines)


if __name__ == "__main__":
    if "--check" in sys.argv:
        try:
            print(summary())
            sys.exit(0)
        except RegistryError as e:  # noqa: BLE001
            print("REGISTRY ERROR: %s" % e)
            sys.exit(1)
    print("用法：python feishu_registry.py --check")
