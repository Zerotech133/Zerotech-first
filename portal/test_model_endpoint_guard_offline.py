"""Offline, AST-isolated security regression for the two model-backed POST routes.

No portal module is imported or server/model process started. Only the do_POST
statements that lead to these two handlers are compiled with inert dependencies.
The strict expected failures document security gates that are not yet present.
"""

import ast
import hashlib
import io
import re
from pathlib import Path
from typing import Any, Callable

import pytest


PORTAL_DIR = Path(__file__).resolve().parent
MODEL_ROUTES = {
    "/api/audit_chat": "_api_audit_chat",
    "/api/ai_duty": "_api_ai_duty",
}
# Reviewed canonical statements produced by ast.unparse on Python 3.11/3.13.
# ast.dump differs across versions even when the source and behavior agree.
# Unknown syntax/calls and any changed executable statements fail closed.
# 2026-10-19 re-baseline: K-ORG-190 org-command block (portal_api do_POST)
# sits between the audit_chat and ai_duty dispatches inside the compiled
# prefix. Its calls compile inert here (org_command_api/log_exc/out are not
# injected into the probe namespace, so any stray execution raises NameError).
_REVIEWED_POST_AST_SHA256 = (
    "1b97f327fee4209b8239e7c09c488f638a6cf8257dc85783c3fe6ccb286e0b8f"
)
_REVIEWED_NODE_TYPES = frozenset(
    {
        ast.Add,
        ast.And,
        ast.Assign,
        ast.Attribute,
        ast.BinOp,
        ast.BoolOp,
        ast.Call,
        ast.Compare,
        ast.Constant,
        ast.Dict,
        ast.Eq,
        ast.ExceptHandler,
        ast.Expr,
        ast.FormattedValue,
        ast.Gt,
        ast.If,
        ast.IfExp,
        ast.In,
        ast.JoinedStr,
        ast.Load,
        ast.Lt,
        ast.Name,
        ast.Not,
        ast.Or,
        ast.Return,
        ast.Set,
        ast.Store,
        ast.Subscript,
        ast.Try,
        ast.Tuple,
        ast.UnaryOp,
        ast.keyword,
    }
)
_REVIEWED_CALLS = frozenset(
    {
        "(self.headers.get('X-Real-IP') or '').strip",
        "_rate_hit",
        "getattr",
        "int",
        "json.loads",
        "log_exc",
        "org_command_api.api_agent_learn",
        "org_command_api.api_agent_update",
        "org_command_api.api_preview",
        "org_command_api.api_project_preview",
        "org_command_api.api_project_register",
        "org_command_api.api_register",
        "out.get",
        "raw.decode",
        "self._api_ai_duty",
        "self._api_audit_chat",
        "self._chanlun_anno",
        "self._check_portal_token",
        "self._flow_rerun",
        "self._json",
        "self._warehouse_update_request",
        "self.headers.get",
        "self.path.split",
        "self.rfile.read",
    }
)


class _MissingModelProtection(AssertionError):
    """Only an observed security-gap assertion qualifies as an expected failure."""


def _require_protection(condition: bool, message: str) -> None:
    """Distinguish an expected missing gate from AST/stub execution errors."""
    if not condition:
        raise _MissingModelProtection(message)


def _portal_tree() -> ast.Module:
    """Parse source without importing or executing portal_api."""
    return ast.parse((PORTAL_DIR / "portal_api.py").read_text(encoding="utf-8"))


def _route_guard(if_node: ast.If, route: str) -> bool:
    """Identify the actual top-level dispatch condition for one POST path."""
    test = if_node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "path"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == route
    )


def _validate_executable_ast(statements: list[ast.stmt]) -> None:
    """Reject unreviewed syntax and calls before compiling any source node."""
    for statement in statements:
        for node in ast.walk(statement):
            if type(node) not in _REVIEWED_NODE_TYPES:
                raise AssertionError(
                    f"unreviewed executable AST node: {type(node).__name__}"
                )
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) not in _REVIEWED_CALLS
            ):
                raise AssertionError(
                    f"unreviewed executable call: {ast.unparse(node.func)}"
                )


def _isolated_dispatch(
    *,
    rate_limited: bool = False,
) -> tuple[Callable[..., Any], frozenset[str], list[tuple[tuple[str, str], int]]]:
    """Compile only original auth/rate/read/dispatch AST with inert globals.

    The original Handler class and its ancestors are never executed. Retaining
    the original guard and dispatch AST ensures the probes follow source logic
    rather than a reimplementation of it.
    """
    tree = _portal_tree()
    mutation_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            isinstance(target, ast.Name) and target.id == "_MUTATION_POSTS"
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    if len(mutation_assignments) != 1:
        raise AssertionError("_MUTATION_POSTS must have exactly one source assignment")
    mutation_assign = mutation_assignments[0]
    if not isinstance(mutation_assign, ast.Assign) or mutation_assign not in tree.body:
        raise AssertionError("_MUTATION_POSTS must be a top-level literal assignment")
    if not (
        isinstance(mutation_assign.value, ast.Call)
        and isinstance(mutation_assign.value.func, ast.Name)
        and mutation_assign.value.func.id == "frozenset"
        and len(mutation_assign.value.args) == 1
        and not mutation_assign.value.keywords
    ):
        raise AssertionError("_MUTATION_POSTS source expression changed shape")
    mutations = frozenset(ast.literal_eval(mutation_assign.value.args[0]))
    handler_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    method = next(
        node
        for node in handler_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
    )
    signature = method.args
    if (
        method.decorator_list
        or method.returns is not None
        or getattr(method, "type_params", ())
        or signature.posonlyargs
        or len(signature.args) != 1
        or signature.args[0].arg != "self"
        or signature.args[0].annotation is not None
        or signature.vararg is not None
        or signature.kwonlyargs
        or signature.kw_defaults
        or signature.kwarg is not None
        or signature.defaults
    ):
        raise AssertionError("POST function signature changed; refuse executable AST")
    statements = method.body
    try_node = next(node for node in statements if isinstance(node, ast.Try))
    pre_try = statements[: statements.index(try_node)]
    if not any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "path"
            for target in node.targets
        )
        for node in pre_try
    ):
        raise AssertionError("POST path assignment absent")
    if len([node for node in statements if isinstance(node, ast.Try)]) != 1:
        raise AssertionError(
            "POST try topology changed; inspect the AST isolation boundary"
        )
    dispatch_indices: list[int] = []
    for route, handler_name in MODEL_ROUTES.items():
        matches = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.If) and _route_guard(node, route)
        ]
        if len(matches) != 1:
            raise AssertionError(f"{route} must have exactly one route guard")
        match = matches[0]
        if match not in try_node.body:
            raise AssertionError(
                f"{route} guard moved outside the isolated top-level dispatch"
            )
        if match.orelse:
            raise AssertionError(f"{route} guard now has an unisolated else branch")
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == handler_name
            for node in ast.walk(match)
        ):
            raise AssertionError(f"{route} branch no longer invokes {handler_name}")
        dispatch_indices.append(try_node.body.index(match))
    # Include EVERY pre-try guard, plus every pre-dispatch branch (including
    # future body checks inside try), preserving their original control flow.
    selected = [*pre_try, *try_node.body[: max(dispatch_indices) + 1]]
    _validate_executable_ast(selected)
    reviewed_ast = "\n".join(ast.unparse(statement) for statement in selected)
    if hashlib.sha256(reviewed_ast.encode("utf-8")).hexdigest() != (
        _REVIEWED_POST_AST_SHA256
    ):
        raise AssertionError(
            "POST executable AST changed; review before updating the isolation boundary"
        )
    safe_args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg="self")],
        vararg=None,
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=None,
        defaults=[],
    )
    probe_method = ast.FunctionDef(
        name="isolated_do_post",
        args=safe_args,
        body=selected,
        decorator_list=[],
        returns=None,
        type_comment=None,
    )
    module = ast.fix_missing_locations(ast.Module(body=[probe_method], type_ignores=[]))
    rate_calls: list[tuple[tuple[str, str], int]] = []

    def rate_hit(key: tuple[str, str], maximum: int) -> bool:
        rate_calls.append((key, maximum))
        return rate_limited

    namespace: dict[str, Any] = {
        "__builtins__": {"int": int, "getattr": getattr},
        "_MUTATION_POSTS": mutations,
        "_WRITE_MAX_PER_MIN": 12,
        "_rate_hit": rate_hit,
    }
    exec(compile(module, str(PORTAL_DIR / "portal_api.py"), "exec"), namespace)  # nosec B102 - 测试隔离加载 portal_api（无外部输入）
    return namespace["isolated_do_post"], mutations, rate_calls


def test_ast_isolation_rejects_elif_route_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An elif dispatch cannot be silently dropped from the compiled prefix."""
    # _portal_tree() re-parses the source on every call, so the returned tree
    # is exclusively owned: mutate it in place (deepcopy of the full AST blows
    # Python 3.11's recursion limit and is unnecessary here).
    mutated_tree = _portal_tree()
    handler = next(
        node
        for node in mutated_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    method = next(
        node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
    )
    dispatch = next(node for node in method.body if isinstance(node, ast.Try))
    audit_branch = next(
        node
        for node in dispatch.body
        if isinstance(node, ast.If) and _route_guard(node, "/api/audit_chat")
    )
    duty_branch = next(
        node
        for node in dispatch.body
        if isinstance(node, ast.If) and _route_guard(node, "/api/ai_duty")
    )
    dispatch.body.remove(duty_branch)
    audit_branch.orelse = [duty_branch]
    monkeypatch.setitem(globals(), "_portal_tree", lambda: mutated_tree)
    with pytest.raises(AssertionError, match="guard now has an unisolated else branch"):
        _isolated_dispatch()


@pytest.mark.parametrize(
    "injected, rejection",
    [
        ("import os", "unreviewed executable AST node: Import"),
        ("open('must-not-create', 'w')", "unreviewed executable call: open"),
        ("__import__('socket')", "unreviewed executable call: __import__"),
        ("self.rfile.read(1)", "POST executable AST changed"),
        ("path = '/api/ai_duty'", "POST executable AST changed"),
    ],
)
def test_ast_isolation_rejects_unreviewed_statement_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    injected: str,
    rejection: str,
) -> None:
    """Unknown source statements cannot run, even before the model dispatch."""
    mutated_tree = _portal_tree()
    handler = next(
        node
        for node in mutated_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    method = next(
        node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
    )
    dispatch = next(node for node in method.body if isinstance(node, ast.Try))
    dispatch.body.insert(0, ast.parse(injected).body[0])
    monkeypatch.setitem(globals(), "_portal_tree", lambda: mutated_tree)
    with pytest.raises(AssertionError, match=rejection):
        _isolated_dispatch()


def test_ast_isolation_never_executes_injected_dangerous_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synthetic side-effect callable must never run from source drift."""
    mutated_tree = _portal_tree()
    handler = next(
        node
        for node in mutated_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    method = next(
        node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
    )
    dispatch = next(node for node in method.body if isinstance(node, ast.Try))
    dispatch.body.insert(0, ast.parse("dangerous_probe()").body[0])
    side_effects: list[str] = []

    def dangerous_probe() -> None:
        side_effects.append("executed")

    monkeypatch.setitem(globals(), "_portal_tree", lambda: mutated_tree)
    monkeypatch.setitem(globals(), "dangerous_probe", dangerous_probe)
    with pytest.raises(AssertionError, match="unreviewed executable call"):
        _isolated_dispatch()
    if side_effects:
        raise AssertionError("injected dangerous function unexpectedly executed")


@pytest.mark.parametrize("signature_drift", ["default", "annotation"])
def test_ast_isolation_rejects_executable_signature_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    signature_drift: str,
) -> None:
    """Default values and annotations never execute when the source drifts."""
    mutated_tree = _portal_tree()
    handler = next(
        node
        for node in mutated_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    method = next(
        node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "do_POST"
    )
    side_effects: list[str] = []

    def dangerous_probe() -> None:
        side_effects.append("executed")

    if signature_drift == "default":
        method.args.defaults.append(ast.parse("dangerous_probe()", mode="eval").body)
    else:
        method.args.args[0].annotation = ast.parse(
            "dangerous_probe()", mode="eval"
        ).body
    monkeypatch.setitem(globals(), "_portal_tree", lambda: mutated_tree)
    monkeypatch.setitem(globals(), "dangerous_probe", dangerous_probe)
    with pytest.raises(AssertionError, match="POST function signature changed"):
        _isolated_dispatch()
    if side_effects:
        raise AssertionError("injected dangerous function unexpectedly executed")


def test_ast_isolation_rejects_duplicate_mutation_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later mutation-set reassignment cannot hide a changed token gate."""
    mutated_tree = _portal_tree()
    assignment = next(
        node
        for node in mutated_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_MUTATION_POSTS"
            for target in node.targets
        )
    )
    duplicate = ast.parse(ast.unparse(assignment)).body[0]
    mutated_tree.body.insert(mutated_tree.body.index(assignment) + 1, duplicate)
    monkeypatch.setitem(globals(), "_portal_tree", lambda: mutated_tree)
    with pytest.raises(AssertionError, match="exactly one source assignment"):
        _isolated_dispatch()


class _ReadProbe(io.BytesIO):
    """In-memory body that records reads without touching files or HTTP."""

    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.read_calls = 0

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return super().read(size)


class _StubRequest:
    """An inert request exposing only the methods read by selected AST nodes."""

    def __init__(
        self,
        route: str,
        body: bytes = b"{}",
        *,
        token_ok: bool = True,
        length: int | None = None,
    ) -> None:
        self.path = route + "?offline=1"
        self.headers = {
            "Content-Length": str(len(body) if length is None else length),
            "X-Real-IP": "192.0.2.42",
        }
        self.client_address = ("192.0.2.42", 0)
        self.rfile = _ReadProbe(body)
        self.token_ok = token_ok
        self.token_checks = 0
        self.responses: list[tuple[int, dict[str, Any]]] = []
        self.dispatched: list[tuple[str, bytes]] = []

    def _check_portal_token(self) -> bool:
        self.token_checks += 1
        return self.token_ok

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.responses.append((status, payload))

    def _api_audit_chat(self, body: bytes) -> None:
        self.dispatched.append(("_api_audit_chat", body))

    def _api_ai_duty(self, body: bytes) -> None:
        self.dispatched.append(("_api_ai_duty", body))


@pytest.mark.parametrize("route,handler", MODEL_ROUTES.items())
def test_model_post_reaches_real_dispatch_with_inert_handler(
    route: str,
    handler: str,
) -> None:
    """Both branches remain wired to their correct handlers and pass raw bytes."""
    dispatch, _, _ = _isolated_dispatch()
    request = _StubRequest(route, b'{"question":"offline"}')
    dispatch(request)
    if request.dispatched != [(handler, b'{"question":"offline"}')]:
        raise AssertionError("model POST did not reach its expected inert handler")


@pytest.mark.parametrize("route", MODEL_ROUTES)
@pytest.mark.xfail(
    strict=True,
    raises=_MissingModelProtection,
    reason="model POST must require portal token for nonlocal client",
)
def test_model_post_rejects_unauthorized_request(route: str) -> None:
    """Future gate: a failed token check must prevent model dispatch."""
    dispatch, _, _ = _isolated_dispatch()
    request = _StubRequest(route, token_ok=False)
    dispatch(request)
    _require_protection(request.token_checks == 1, "token gate was not invoked")
    _require_protection(
        bool(request.responses) and request.responses[0][0] == 403,
        "model POST did not return forbidden",
    )
    _require_protection(not request.dispatched, "unauthorized model request dispatched")


@pytest.mark.parametrize("route", MODEL_ROUTES)
def test_model_route_stays_outside_shared_mutation_set(route: str) -> None:
    """The approved model protection must not reuse shared write semantics."""
    _, mutation_routes, _ = _isolated_dispatch()
    if route in mutation_routes:
        raise AssertionError("model route unexpectedly shares mutation protection")


@pytest.mark.parametrize("route", MODEL_ROUTES)
@pytest.mark.xfail(
    strict=True,
    raises=_MissingModelProtection,
    reason="model POST needs independent rate protection, not the shared write set",
)
def test_model_post_has_independent_rate_guard(route: str) -> None:
    """Future gate: independent model bucket must reject excess before dispatch."""
    dispatch, mutation_routes, rate_calls = _isolated_dispatch(rate_limited=True)
    request = _StubRequest(route)
    dispatch(request)
    if set(MODEL_ROUTES) & mutation_routes:
        raise AssertionError("model routes must not reuse the mutation set")
    _require_protection(bool(rate_calls), "model request bypassed the rate limiter")
    _require_protection(
        all(key[0] != "write" for key, _ in rate_calls),
        "model request reused shared write-rate bucket",
    )
    _require_protection(
        all(route in key[0] or key[0] == "model" for key, _ in rate_calls),
        "model request has no model-specific rate bucket",
    )
    _require_protection(
        bool(request.responses) and request.responses[0][0] == 429,
        "rate-limited model request did not return 429",
    )
    _require_protection(
        not request.dispatched, "rate-limited model request reached handler"
    )


@pytest.mark.parametrize("route", MODEL_ROUTES)
@pytest.mark.xfail(
    strict=True,
    raises=_MissingModelProtection,
    reason="model POST must reject oversized body before read/dispatch",
)
def test_model_post_rejects_oversized_body(route: str) -> None:
    """Future gate: impose a body cap without invoking a model handler."""
    dispatch, _, _ = _isolated_dispatch()
    request = _StubRequest(route, b"x" * 65537)
    dispatch(request)
    _require_protection(
        bool(request.responses) and request.responses[0][0] == 413,
        "oversized model request was not rejected",
    )
    _require_protection(
        request.rfile.read_calls == 0,
        "oversized model request was read before rejection",
    )
    _require_protection(not request.dispatched, "oversized model request dispatched")


@pytest.mark.parametrize("route", MODEL_ROUTES)
@pytest.mark.xfail(
    strict=True,
    raises=_MissingModelProtection,
    reason="model POST must reject negative Content-Length",
)
def test_model_post_rejects_negative_length(route: str) -> None:
    """Future gate: negative request lengths are invalid, not empty bodies."""
    dispatch, _, _ = _isolated_dispatch()
    request = _StubRequest(route, length=-1)
    dispatch(request)
    _require_protection(
        bool(request.responses) and request.responses[0][0] == 400,
        "negative Content-Length was not rejected",
    )
    _require_protection(
        request.rfile.read_calls == 0,
        "negative-length model request was read before rejection",
    )
    _require_protection(
        not request.dispatched, "negative-length model request dispatched"
    )


def test_audit_page_explicit_request_contract_has_no_token() -> None:
    """Current embedded client only supplies JSON content type, not auth."""
    source = (PORTAL_DIR / "api_misc.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Handler"
    )
    page = next(
        node
        for node in handler.body
        if isinstance(node, ast.FunctionDef) and node.name == "_audit_ai_page"
    )
    page_source = ast.get_source_segment(source, page) or ""
    if "fetch('/api/audit_chat',{method:'POST'," not in page_source:
        raise AssertionError("audit chat fetch call changed")
    if "headers:{'Content-Type':'application/json'}" not in page_source:
        raise AssertionError("audit chat request headers changed")
    if "body:JSON.stringify(Object.assign({messages:hist},payload))" not in page_source:
        raise AssertionError("audit chat request body changed")
    if re.search(r"X-Portal-Token|Authorization", page_source, re.I):
        raise AssertionError("audit chat token contract changed")


def test_paper_duty_explicit_request_contract_has_no_token() -> None:
    """Paper's duty call and API wrapper do not explicitly attach a token."""
    source = (PORTAL_DIR / "paper.html").read_text(encoding="utf-8")
    duty = source.split("async function askDuty(preset){", 1)[1].split(
        "// ===== K-STOCK-352 ②", 1
    )[0]
    wrapper = source.split("async function api(path,opt){", 1)[1].split("\n}", 1)[0]
    if (
        "api('/api/ai_duty',{method:'POST',headers:{'Content-Type':'application/json'},"
        not in duty
    ):
        raise AssertionError("duty POST request changed")
    if "body:JSON.stringify({question:q, ctx:'模拟仓平台 · 接待大厅'})" not in duty:
        raise AssertionError("duty request body changed")
    if "fetch(path,opt||{})" not in wrapper:
        raise AssertionError("duty fetch wrapper changed")
    if re.search(r"X-Portal-Token|Authorization", duty + wrapper, re.I):
        raise AssertionError("duty token contract changed")
