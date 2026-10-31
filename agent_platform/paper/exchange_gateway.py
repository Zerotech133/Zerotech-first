# -*- coding: utf-8 -*-
"""Paper Exchange Gateway（只读路由接入）—— WO-EXCHANGE-GATEWAY-20261031，Phase 2-C。

把已合并的 ``exchange_facade`` 只读能力接成**可被现有 HTTP 层调用的纯函数网关**：
本模块不新建端口、不启动服务、不注册路由、不写盘、不下单——只把
``handle_get(path, query)`` 的结果 ``(status, content_type, body)`` 交给既有
HTTP Handler 自己发送。**不修改 dashboard_api / portal_api / 任何服务进程。**

端点契约（只读 GET，响应恒 ``application/json; charset=utf-8``）::

    /paper_exchange/health                    两本账只读健康检查（facade.health 透传）
    /paper_exchange/route?book=<book>[&tenant=&account=&combo=]
                                              book 路由描述；带 tenant 时等价 resolve()
    /paper_exchange/accounts[?book=<book>]    账户目录（facade.list_accounts 透传）
    /paper_exchange/snapshot?namespace=...    账户快照（facade.account_snapshot 透传）
    /paper_exchange/snapshot?tenant=&account=&book=&combo=

HTTP 状态码只表达**传输层**语义；业务/数据可用性在信封 ``ok`` 与 ``data.status``：
- 200：请求按契约处理（即使 data.status=unavailable/degraded 也是 200，不假装成功：
  信封 ``ok`` 镜像 data.ok，``data.unavailable`` 给出缺失原因）；
- 400：参数坏 / 身份非法 / 未知或跨账本/跨租户（closed-on-failure，错误码=异常类名）；
- 404：未知路径（仅四条 /paper_exchange/* 契约路径，绝不透传其它服务路径）；
- 500：网关内部异常（JSON 错误，不泄露堆栈）。

只读边界（本单硬约束，测试锁定）：
- 两本账 official/legacy 沿用 facade 的物理隔离，不合并、不迁移；
- 不写 ledger/状态/任何文件，不下单、不启动撮合、不新增端口/服务/计划任务；
- 不 import engine/risk_gate/paper_book/k_stock；仅 ``exchange_facade`` +
  ``tenant_model`` + 标准库；
- 未知/跨账本/跨租户 closed-on-failure；缺失数据沿用 facade 的
  ``unavailable``/``null``（不造数）。

接入示例（现有 HTTP 层里加 3~4 行即可；本模块不替你做这件事）::

    from agent_platform.paper import exchange_gateway
    status, content_type, body = exchange_gateway.handle_get(path, query)
    self.send_response(status)
    self.send_header("Content-Type", content_type)
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

命令行（只读，不绑端口）::

    python -X utf8 -m agent_platform.paper.exchange_gateway health
    python -X utf8 -m agent_platform.paper.exchange_gateway route official --tenant t1 --account a1
    python -X utf8 -m agent_platform.paper.exchange_gateway accounts --book legacy
    python -X utf8 -m agent_platform.paper.exchange_gateway snapshot sim/legacy/t1/main
    python -X utf8 -m agent_platform.paper.exchange_gateway get /paper_exchange/health
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

try:  # 包内正常导入（仓库根在 sys.path 上）
    from agent_platform.paper import exchange_facade as facade
    from agent_platform.paper import tenant_model as tm
except ImportError:  # 直接 `python agent_platform/paper/exchange_gateway.py` 运行时兜底
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_platform.paper import exchange_facade as facade
    from agent_platform.paper import tenant_model as tm

# 网关信封版本：端点/字段语义变更必须升版本。
SCHEMA_VERSION = 1
GATEWAY_NAME = "paper_exchange_gateway"
CONTENT_TYPE = "application/json; charset=utf-8"
METHODS = ("GET",)

PATH_HEALTH = "/paper_exchange/health"
PATH_ROUTE = "/paper_exchange/route"
PATH_ACCOUNTS = "/paper_exchange/accounts"
PATH_SNAPSHOT = "/paper_exchange/snapshot"
ENDPOINTS = (PATH_HEALTH, PATH_ROUTE, PATH_ACCOUNTS, PATH_SNAPSHOT)
_PATH_KINDS = {
    PATH_HEALTH: "health",
    PATH_ROUTE: "route",
    PATH_ACCOUNTS: "accounts",
    PATH_SNAPSHOT: "snapshot",
}

# 明确只读边界（每个响应都带，供下游零解释断言）。
GATEWAY_BOUNDARIES = {
    "readonly": True,
    "writable": False,
    "trading_permitted": False,
    "ledger_write": False,
    "disk_write": False,
    "place_order": False,
    "start_matching": False,
    "migrate_ledger": False,
    "new_port": False,
    "start_service": False,
}


class GatewayError(ValueError):
    """网关层错误基类（HTTP 400/404 的上游语义，不吞身份错误）。"""


class GatewayBadRequest(GatewayError):
    """请求参数/路径用法错误 → HTTP 400。"""


class GatewayNotFound(GatewayError):
    """未知路径 → HTTP 404。"""


# ---------------------------------------------------------------------------
# 参数归一（query 兼容 dict / list 值 / 原始 query string）
# ---------------------------------------------------------------------------
def _parse_query_string(raw):
    """解析 `k=v&k2=v2` 片段 → {k: 最后值}（空值保留为空串）。"""
    if not raw:
        return {}
    parsed = {}
    for key, values in parse_qs(raw, keep_blank_values=True).items():
        if values:
            parsed[key] = values[-1]
    return parsed


def _normalize_query(query):
    """HTTP 层可能传 dict（parse_qs 的 list 值）/ 原始字符串 / None。"""
    if query is None:
        return {}
    if isinstance(query, str):
        return _parse_query_string(query)
    if not isinstance(query, dict):
        raise GatewayBadRequest("query 须为 dict/str/None: %r" % (query,))
    normalized = {}
    for key, value in query.items():
        if not isinstance(key, str) or not key:
            raise GatewayBadRequest("query 键须为非空字符串: %r" % (key,))
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = value[-1]
        if value is None:
            continue
        normalized[key] = str(value)
    return normalized


def _param(params, name, required=False):
    """取字符串参数（去首尾空白）；required 时缺失 → 400。"""
    value = params.get(name)
    if value is None:
        if required:
            raise GatewayBadRequest("缺少参数 %s" % name)
        return None
    value = value.strip()
    if not value:
        if required:
            raise GatewayBadRequest("参数 %s 不能为空" % name)
        return None
    return value


def _ensure_registry(registry):
    """registry 校验复用 facade 口径（duck-type TenantRegistry）。"""
    return facade._check_registry(registry)


def _boundaries():
    return dict(GATEWAY_BOUNDARIES)


# ---------------------------------------------------------------------------
# 对外只读 API（可编程调用；也是 HTTP 端点的实现体）
# ---------------------------------------------------------------------------
def resolve(book, tenant, account=None, *, combo=None, registry=None):
    """解析 (book, tenant, account[, combo]) → 只读路由结果（不做数据 IO）。

    - ``tenant`` 可为 tenant_id 或 namespace 字符串（``sim/<book>/<tenant>[/...]``）；
      namespace 账本前缀必须与 ``book`` 一致，否则跨账本 closed-on-failure；
    - 非法/未知 book/tenant/account → tenant_model 错误（closed-on-failure）；
    - 传 ``registry``（TenantRegistry）时强制「租户已登记、账户属该租户」；
    - 本函数只解析路由，不读账本、不因数据缺失失败（缺失由 snapshot 表达）。
    """
    route_doc = facade.route(book)  # 非法 book → TenantValidationError
    tenant_id, account_id, combo_id, namespace = _resolve_identity(
        book, tenant, account, combo)
    registry = _ensure_registry(registry)
    if registry is not None:
        if account_id is None:
            registry.get_tenant(tenant_id, book)
        else:
            registry.resolve(tenant_id, account_id, combo_id, book)
    return {
        "schema_version": SCHEMA_VERSION,
        "gateway": GATEWAY_NAME,
        "kind": "route",
        "book": book,
        "backend": facade.BOOK_BACKENDS[book],
        "tenant_id": tenant_id,
        "account_id": account_id,
        "combo_id": combo_id,
        "namespace": namespace,
        "resolved": True,
        "route": route_doc,
        "readonly": True,
        "writable": False,
        "trading_permitted": False,
        "registry_verified": registry is not None,
        "boundaries": _boundaries(),
    }


def _resolve_identity(book, tenant, account, combo):
    """把 tenant（id 或 namespace）+ account/combo 归一成 (tenant_id, account, combo, namespace)。"""
    if isinstance(tenant, str) and "/" in tenant:
        if account is not None or combo is not None:
            raise facade.ExchangeFacadeError(
                "tenant 为 namespace 形式时不得再传 account/combo")
        parsed = tm.parse_namespace(tenant)  # 非法 → TenantValidationError
        if parsed["book"] != book:
            raise tm.CrossTenantError(
                "跨账本路由：namespace %r 属 %s 账，请求的是 %s 账（两账不得合并）"
                % (tenant, parsed["book"], book))
        return parsed["tenant_id"], parsed["account_id"], parsed["combo_id"], \
            parsed["namespace"]
    namespace = tm.tenant_namespace(tenant, book, account_id=account,
                                    combo_id=combo)
    return tenant, account, combo, namespace


def route(book, tenant=None, account=None, *, combo=None, registry=None):
    """book 只读路由描述；带 tenant 时等价 resolve（含 namespace 口径）。"""
    if tenant is None:
        if account is not None or combo is not None:
            raise GatewayBadRequest("account/combo 需要同时给 tenant")
        route_doc = facade.route(book)  # 非法 book → TenantValidationError
        return {
            "schema_version": SCHEMA_VERSION,
            "gateway": GATEWAY_NAME,
            "kind": "route",
            "book": book,
            "backend": facade.BOOK_BACKENDS[book],
            "tenant_id": None,
            "account_id": None,
            "combo_id": None,
            "namespace": None,
            "resolved": False,
            "route": route_doc,
            "readonly": True,
            "writable": False,
            "trading_permitted": False,
            "registry_verified": False,
            "boundaries": _boundaries(),
        }
    return resolve(book, tenant, account, combo=combo, registry=registry)


def health():
    """两本账只读健康检查（直通 facade.health，含只读边界/隔离声明）。"""
    return facade.health()


def accounts(book=None, registry=None):
    """账户目录（直通 facade.list_accounts；没有写面）。"""
    return facade.list_accounts(book=book, registry=registry)


# facade 的命名也叫 list_accounts；网关对外主名 accounts（端点名一致）。
list_accounts = accounts


def snapshot(namespace=None, *, book=None, tenant=None, account=None,
             combo=None, registry=None):
    """账户快照（直通 facade.account_snapshot）。

    - ``namespace`` 形式：``snapshot(namespace="sim/legacy/t1/main")``；
    - 显式身份形式：``snapshot(book="legacy", tenant="t1", account="main")``；
    - 两种形式互斥；未知/跨账本/跨租户 closed-on-failure；缺失数据显式
      ``status=unavailable``/``null``（不造数）。
    """
    if namespace is not None:
        if book is not None or tenant is not None or account is not None \
                or combo is not None:
            raise facade.ExchangeFacadeError(
                "namespace 形式不得再传 book/tenant/account/combo")
        return facade.account_snapshot(namespace, registry=registry)
    if tenant is None:
        raise GatewayBadRequest("snapshot 需要 namespace 或 tenant")
    target_book = "official" if book is None else book
    return facade.account_snapshot(tenant, account, combo_id=combo,
                                   book=target_book, registry=registry)


def describe():
    """网关自述（端点/只读边界/内容类型），供接入方与巡检查询。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "gateway": GATEWAY_NAME,
        "endpoints": list(ENDPOINTS),
        "methods": list(METHODS),
        "content_type": CONTENT_TYPE,
        "readonly": True,
        "writable": False,
        "trading_permitted": False,
        "boundaries": _boundaries(),
    }


# ---------------------------------------------------------------------------
# HTTP 纯函数适配层（不建 server / 不占端口）
# ---------------------------------------------------------------------------
def _encode(payload):
    """信封 → UTF-8 bytes（HTTP Handler 直接写 wfile）。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=False).encode(
        "utf-8")


def _envelope(kind, data):
    """成功信封：ok 镜像 data.ok（有则取，无则 True）；边界恒声明。"""
    ok = True
    if isinstance(data, dict) and "ok" in data:
        ok = bool(data.get("ok"))
    return {
        "schema_version": SCHEMA_VERSION,
        "gateway": GATEWAY_NAME,
        "kind": kind,
        "ok": ok,
        "readonly": True,
        "writable": False,
        "trading_permitted": False,
        "boundaries": _boundaries(),
        "data": data,
        "error": {},
    }


def _error_envelope(kind, code, message, status):
    """错误信封（ok=false + error.code/message；不泄露堆栈）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "gateway": GATEWAY_NAME,
        "kind": kind,
        "ok": False,
        "readonly": True,
        "writable": False,
        "trading_permitted": False,
        "boundaries": _boundaries(),
        "status": status,
        "data": {},
        "error": {"code": code, "message": message},
    }


def _dispatch(path, query=None):
    """路径 → (kind, 参数 dict)；未知路径 → GatewayNotFound（404 兜底）。"""
    if not isinstance(path, str) or not path.strip():
        raise GatewayBadRequest("path 须为非空字符串: %r" % (path,))
    raw = path.strip()
    inline = ""
    if "?" in raw:
        raw, _, inline = raw.partition("?")
    normalized_path = raw.rstrip("/") or "/"
    kind = _PATH_KINDS.get(normalized_path)
    if kind is None:
        raise GatewayNotFound("未知路径（只读网关只认 %s）: %r"
                              % ("/".join(ENDPOINTS), normalized_path))
    params = _parse_query_string(inline)
    params.update(_normalize_query(query))  # 显式 query 参数优先于 path 内联 query
    return kind, params


def _snapshot_params(params):
    """snapshot 端点参数解析（namespace 与显式身份互斥）。"""
    namespace = _param(params, "namespace")
    tenant = _param(params, "tenant")
    if namespace is not None:
        if tenant is not None or _param(params, "account") is not None \
                or _param(params, "combo") is not None \
                or _param(params, "book") is not None:
            raise GatewayBadRequest(
                "snapshot 的 namespace 形式与 book/tenant/account/combo 互斥")
        return snapshot(namespace=namespace)
    if tenant is None:
        raise GatewayBadRequest("snapshot 需要 namespace 或 tenant 参数")
    return snapshot(book=_param(params, "book"), tenant=tenant,
                    account=_param(params, "account"),
                    combo=_param(params, "combo"))


def handle_get(path, query=None):
    """只读 GET 纯函数：返回 ``(status, content_type, body)``。

    - ``path``：``/paper_exchange/...`` 契约路径（可带 ``?k=v`` 内联 query）；
    - ``query``：dict（parse_qs 的 list 值可）/ 原始 query string / None；
    - body：UTF-8 JSON bytes，content_type 恒 ``application/json; charset=utf-8``；
    - 未知/跨账本/跨租户身份错误 → 400；未知路径 → 404；内部异常 → 500 JSON。
    """
    try:
        kind, params = _dispatch(path, query)
        if kind == "health":
            data = health()
        elif kind == "route":
            data = route(
                _param(params, "book", required=True),
                _param(params, "tenant"),
                _param(params, "account"),
                combo=_param(params, "combo"),
            )
        elif kind == "accounts":
            data = accounts(book=_param(params, "book"))
        else:
            data = _snapshot_params(params)
    except GatewayNotFound as exc:
        return 404, CONTENT_TYPE, _encode(
            _error_envelope("error", "not_found", str(exc), 404))
    except GatewayBadRequest as exc:
        return 400, CONTENT_TYPE, _encode(
            _error_envelope("error", "bad_request", str(exc), 400))
    except tm.TenantContractError as exc:
        return 400, CONTENT_TYPE, _encode(
            _error_envelope("error", type(exc).__name__, str(exc), 400))
    except facade.ExchangeFacadeError as exc:
        return 400, CONTENT_TYPE, _encode(
            _error_envelope("error", type(exc).__name__, str(exc), 400))
    except Exception as exc:  # noqa: BLE001 - fail-visible 500 JSON，不泄露堆栈
        return 500, CONTENT_TYPE, _encode(
            _error_envelope("error", "internal_error",
                            "%s: %s" % (type(exc).__name__, exc), 500))
    return 200, CONTENT_TYPE, _encode(_envelope(kind, data))


# ---------------------------------------------------------------------------
# CLI（只读；不绑端口/不起服务）
# ---------------------------------------------------------------------------
def main(argv=None):
    """CLI：health/route/accounts/snapshot 直调网关 API；get 直跑 handle_get。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent_platform.paper.exchange_gateway",
        description="Paper Exchange 只读网关 CLI（不绑端口/不起服务/不下单）")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health", help="两本账只读健康检查")
    p_route = sub.add_parser("route", help="book 只读路由（可带 tenant/account）")
    p_route.add_argument("book", choices=list(tm.BOOKS))
    p_route.add_argument("--tenant", default=None)
    p_route.add_argument("--account", default=None)
    p_route.add_argument("--combo", default=None)
    p_accounts = sub.add_parser("accounts", help="只读账户目录")
    p_accounts.add_argument("--book", choices=list(tm.BOOKS), default=None)
    p_snap = sub.add_parser("snapshot", help="只读账户快照")
    p_snap.add_argument("target", nargs="?", default=None,
                        help="namespace（sim/...）或 tenant_id")
    p_snap.add_argument("--tenant", default=None)
    p_snap.add_argument("--account", default=None)
    p_snap.add_argument("--combo", default=None)
    p_snap.add_argument("--book", choices=list(tm.BOOKS), default=None)
    p_get = sub.add_parser("get", help="直跑 handle_get 并打印响应体")
    p_get.add_argument("path")
    p_get.add_argument("--query", action="append", default=[], metavar="K=V")
    args = parser.parse_args(argv)

    try:
        if args.command == "health":
            payload = health()
        elif args.command == "route":
            payload = route(args.book, args.tenant, args.account,
                            combo=args.combo)
        elif args.command == "accounts":
            payload = accounts(book=args.book)
        elif args.command == "snapshot":
            target = args.target or args.tenant
            if not target:
                parser.error("snapshot 需要 target 或 --tenant")
            if "/" in target:
                payload = snapshot(namespace=target)
            else:
                payload = snapshot(book=args.book, tenant=target,
                                   account=args.account, combo=args.combo)
        else:
            query = {}
            for item in args.query:
                if "=" not in item:
                    parser.error("--query 须为 K=V: %r" % item)
                key, _, value = item.partition("=")
                query[key] = value
            status, _content_type, body = handle_get(args.path, query)
            print(body.decode("utf-8"))
            return 0 if status == 200 else 1
    except (GatewayError, tm.TenantContractError,
            facade.ExchangeFacadeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "error_type": type(exc).__name__},
                         ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "SCHEMA_VERSION",
    "GATEWAY_NAME",
    "CONTENT_TYPE",
    "METHODS",
    "PATH_HEALTH",
    "PATH_ROUTE",
    "PATH_ACCOUNTS",
    "PATH_SNAPSHOT",
    "ENDPOINTS",
    "GATEWAY_BOUNDARIES",
    "GatewayError",
    "GatewayBadRequest",
    "GatewayNotFound",
    "resolve",
    "route",
    "health",
    "accounts",
    "list_accounts",
    "snapshot",
    "describe",
    "handle_get",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
