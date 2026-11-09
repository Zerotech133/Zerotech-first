# -*- coding: utf-8 -*-
"""Offline contract probes for the ops-today GET route and cache.

Only the reviewed ``compute`` function's AST is executed, with all paths and
helpers replaced by temporary fixtures. Neither production module is imported.
The AST digest is a fail-closed review lock, not a sandbox: trusted stdlib
json/time objects remain injected; never run this against untrusted source.
"""

import ast
import builtins
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_OPS_SOURCE = _REPO_ROOT / "quant_backtest" / "ops_signal_today.py"
_PORTAL_SOURCE = _REPO_ROOT / "portal" / "portal_api.py"
_POOL_FILE = "poc_adapter_chanlun_offline.json"
# Pin the exact extracted function: changes require a manual safety review before execution.
# Re-anchored 2026-11-09: compute 增加 cache_error 且移除 logging；已复核无 import/scope 变更，
# 调用仍在白名单内，按当前 Python 3.12 AST 重新锚定。
_COMPUTE_AST_SHA256 = "9ed83061931ca5becba7ac0c2d7a5a32fe1c0b87ea8919aba561adb53b0cd0fb"
_ALLOWED_CALLS = frozenset({
    "(chan_analyze(bars) or {}).get", "_latest_pool_file", "_load_books",
    "_portfolio_context", "books.items", "c.get", "c.get('pool', {}).get",
    "chan_analyze", "cur.items", "cur.setdefault", "d.items", "float", "int",
    "json.dump", "json.load", "last_dates.items", "last_dates.values", "len",
    "max", "open", "os.makedirs", "os.path.exists", "os.path.join",
    "prev.items", "round", "seg.tail", "seg.tail(WARMUP).itertuples",
    "set", "sg.get", "sigs.append", "sigs.sort", "sorted", "state.get",
    "str", "sum", "tiers.get", "tiers.values", "time.strftime", "x.get",
})


class KnownContractGap(Exception):
    """Mark only the specific, independently verified missing contract."""


def _parse_source(path: Path) -> ast.Module:
    """Parse source without importing or executing its module-level code."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _guarded_filesystem(tmp_path: Path) -> tuple[Any, Any]:
    """Restrict extracted compute's file operations to the disposable test root."""
    original_open = builtins.open
    original_makedirs = os.makedirs
    root = tmp_path.resolve(strict=True)

    def check_path(path: Any) -> None:
        candidate = Path(os.fspath(path)).resolve()
        if not candidate.is_relative_to(root):
            raise AssertionError(f"compute attempted access outside temporary root: {candidate}")

    def guarded_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        check_path(path)
        return original_open(path, *args, **kwargs)

    def guarded_makedirs(path: Any, *args: Any, **kwargs: Any) -> None:
        check_path(path)
        original_makedirs(path, *args, **kwargs)

    def guarded_exists(path: Any) -> bool:
        check_path(path)
        return os.path.exists(path)

    isolated_os = SimpleNamespace(
        makedirs=guarded_makedirs,
        path=SimpleNamespace(join=os.path.join, exists=guarded_exists),
    )
    return guarded_open, isolated_os


def _compute_from_ast(tmp_path: Path, load_books: Any) -> Any:
    """Compile only compute and inject isolated filesystem/data collaborators."""
    tree = _parse_source(_OPS_SOURCE)
    candidates = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "compute"
    ]
    if len(candidates) != 1:
        raise RuntimeError("expected exactly one top-level compute function")
    compute_node = candidates[0]
    forbidden = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)
    if any(isinstance(node, forbidden) for node in ast.walk(compute_node)):
        raise RuntimeError("compute AST contains forbidden import or scope mutation")
    calls = {ast.unparse(node.func) for node in ast.walk(compute_node)
             if isinstance(node, ast.Call)}
    if not calls <= _ALLOWED_CALLS:
        raise RuntimeError(f"compute AST contains unknown calls: {calls - _ALLOWED_CALLS}")
    digest = hashlib.sha256(
        ast.dump(compute_node, include_attributes=False).encode("utf-8")
    ).hexdigest()
    if digest != _COMPUTE_AST_SHA256:
        raise RuntimeError("compute AST changed: review all side effects before executing")
    function_module = ast.fix_missing_locations(
        ast.Module(body=[compute_node], type_ignores=[])
    )
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    (pool_dir / _POOL_FILE).write_text(
        json.dumps([{"stock": "000001.SZ", "_tier": "A"}]),
        encoding="utf-8",
    )
    guarded_open, isolated_os = _guarded_filesystem(tmp_path)
    namespace = {
        "__builtins__": {
            "open": guarded_open,
            "sorted": sorted,
            "set": set,
            "str": str,
            "int": int,
            "len": len,
            "sum": sum,
            "max": max,
            "any": any,
            "float": float,
            "round": round,
            "Exception": Exception,
        },
        "os": isolated_os,
        "json": json,
        "time": time,
        "STATE_DIR": str(tmp_path / "state"),
        "POOL_DIR": str(pool_dir),
        "WARMUP": 250,
        "POLICY": "offline-test-policy",
        "_latest_pool_file": lambda: _POOL_FILE,
        "_load_books": load_books,
        "_portfolio_context": lambda: {"note": "offline-test-ledger"},
        "chan_analyze": lambda bars: {"signals": []},
    }
    exec(compile(function_module, str(_OPS_SOURCE), "exec"), namespace)
    return namespace["compute"]


def _ops_get_branch() -> ast.If:
    """Require one direct equality branch in the unique Handler.do_GET chain."""
    tree = _parse_source(_PORTAL_SOURCE)
    handlers = [
        method
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == "Handler"
        for method in cls.body
        if isinstance(method, ast.FunctionDef) and method.name == "do_GET"
    ]
    if len(handlers) != 1:
        raise RuntimeError("expected exactly one Handler.do_GET")
    handler = handlers[0]
    if not any(
        isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "path"
        and isinstance(statement.value, ast.Subscript)
        and isinstance(statement.value.value, ast.Call)
        and isinstance(statement.value.value.func, ast.Attribute)
        and statement.value.value.func.attr == "split"
        and isinstance(statement.value.value.func.value, ast.Name)
        and statement.value.value.func.value.id == "raw"
        for statement in handler.body
    ):
        raise RuntimeError("GET path normalization must be reviewed")

    def is_target(test: ast.expr) -> bool:
        return (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "path"
            and len(test.ops) == len(test.comparators) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "/api/ops_today"
        )

    matches = [node for node in ast.walk(handler) if isinstance(node, ast.If) and is_target(node.test)]
    if len(matches) != 1:
        raise RuntimeError("expected exactly one GET /api/ops_today branch")
    branch = matches[0]
    # Earlier independent top-level guards return; the final top-level If
    # owns the long GET dispatch chain containing this route.
    top_level = [node for node in handler.body if isinstance(node, ast.If)]
    if not top_level:
        raise RuntimeError("GET dispatch chain missing")
    chain_root = top_level[-1]
    chain_nodes: list[ast.If] = []
    while chain_root is not None:
        chain_nodes.append(chain_root)
        chain_root = (
            chain_root.orelse[0]
            if len(chain_root.orelse) == 1 and isinstance(chain_root.orelse[0], ast.If)
            else None
        )
    if not any(node is branch for node in chain_nodes):
        raise RuntimeError("route is not in top-level GET dispatch")
    return branch


def _calls_ops_compute(branch: ast.If) -> bool:
    """Detect only the exact direct response call; reject ambiguous control flow."""
    imported = [
        node for node in branch.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "ops_signal_today"
        and len(node.names) == 1
        and node.names[0].name == "compute"
        and node.names[0].asname == "_ops_compute"
    ]
    responses = [
        node for node in branch.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "self"
        and node.value.func.attr == "_json"
        and len(node.value.args) == 1
        and isinstance(node.value.args[0], ast.Call)
        and isinstance(node.value.args[0].func, ast.Name)
        and node.value.args[0].func.id == "_ops_compute"
        and not node.value.args[0].args
        and not node.value.args[0].keywords
    ]
    direct_compute_calls = [
        node for statement in branch.body for node in ast.walk(statement)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_ops_compute"
    ]
    if not direct_compute_calls and not imported:
        return False
    if len(imported) != 1 or len(responses) != 1 or len(direct_compute_calls) != 1:
        raise RuntimeError(
            "ops route compute wiring changed: review before asserting read-only behavior"
        )
    if branch.body.index(imported[0]) >= branch.body.index(responses[0]):
        raise RuntimeError("compute alias must be imported before the response")
    return True


def test_nested_ops_route_cannot_masquerade_as_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A syntactically matching route hidden in a nested branch is rejected."""
    original_parse = _parse_source

    def altered_parse(path: Path) -> ast.Module:
        tree = original_parse(path)
        if path != _PORTAL_SOURCE:
            return tree
        handler = next(
            node for cls in tree.body if isinstance(cls, ast.ClassDef)
            and cls.name == "Handler" for node in cls.body
            if isinstance(node, ast.FunctionDef) and node.name == "do_GET"
        )
        route = next(
            node for node in ast.walk(handler)
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
            and any(isinstance(value, ast.Constant)
                    and value.value == "/api/ops_today"
                    for value in node.test.comparators)
        )
        for parent in ast.walk(handler):
            if not isinstance(parent, ast.If):
                continue
            for field in (parent.body, parent.orelse):
                if route in field:
                    field.remove(route)
                    handler.body.append(
                        ast.If(test=ast.Constant(value=False), body=[route], orelse=[])
                    )
                    return tree
        raise RuntimeError("target route parent missing")

    monkeypatch.setitem(_ops_get_branch.__globals__, "_parse_source", altered_parse)
    with pytest.raises(RuntimeError, match="not in top-level GET dispatch"):
        _ops_get_branch()


def test_ambiguous_compute_alias_cannot_pass_route_check() -> None:
    """A nested alias invocation never proves a direct response computation."""
    branch = ast.If(
        test=ast.Constant(value=True),
        body=[ast.ImportFrom(module="ops_signal_today", names=[
            ast.alias(name="compute", asname="_ops_compute")], level=0),
            ast.If(test=ast.Constant(value=False), body=[ast.Expr(value=ast.Call(
                func=ast.Name(id="_ops_compute", ctx=ast.Load()), args=[], keywords=[]))],
                orelse=[])],
        orelse=[],
    )
    with pytest.raises(RuntimeError, match="compute wiring changed"):
        _calls_ops_compute(branch)


def test_modified_compute_ast_is_rejected_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even one newly introduced call blocks the extracted function entirely."""
    original_parse = _parse_source

    def altered_parse(path: Path) -> ast.Module:
        tree = original_parse(path)
        candidates = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "compute"
        ]
        assert len(candidates) == 1
        candidates[0].body.insert(
            0, ast.Expr(value=ast.Call(func=ast.Name(id="unknown_side_effect", ctx=ast.Load()),
                                       args=[], keywords=[])),
        )
        return tree

    monkeypatch.setitem(_compute_from_ast.__globals__, "_parse_source", altered_parse)
    with pytest.raises(RuntimeError, match="unknown calls"):
        _compute_from_ast(tmp_path, lambda stocks: {})
    assert not (tmp_path / "pool").exists()


def test_import_in_compute_ast_is_rejected_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new import cannot execute even if optimizations disable assertions."""
    original_parse = _parse_source

    def altered_parse(path: Path) -> ast.Module:
        tree = original_parse(path)
        candidates = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "compute"
        ]
        if len(candidates) != 1:
            raise RuntimeError("unexpected compute count")
        candidates[0].body.insert(0, ast.Import(names=[ast.alias(name="socket")]))
        return tree

    monkeypatch.setitem(_compute_from_ast.__globals__, "_parse_source", altered_parse)
    with pytest.raises(RuntimeError, match="forbidden import"):
        _compute_from_ast(tmp_path, lambda stocks: {})
    assert not (tmp_path / "pool").exists()


def test_guarded_filesystem_rejects_paths_outside_temp_root(tmp_path: Path) -> None:
    """The isolated builtins and injected os refuse any non-temporary path."""
    guarded_open, isolated_os = _guarded_filesystem(tmp_path)
    outside = tmp_path.parent / "outside-ops-test"
    with pytest.raises(AssertionError, match="outside temporary root"):
        guarded_open(outside, "w")
    with pytest.raises(AssertionError, match="outside temporary root"):
        isolated_os.makedirs(outside)
    with pytest.raises(AssertionError, match="outside temporary root"):
        isolated_os.path.exists(outside)
    assert not outside.exists()


def test_isolated_compute_writes_state_and_cache(tmp_path: Path) -> None:
    """A cache miss computes and writes both files under a temporary root."""
    compute = _compute_from_ast(tmp_path, lambda stocks: {})
    result = compute()
    state_dir = tmp_path / "state"
    assert result["ok"] is True
    assert (state_dir / "signal_state.json").exists()
    assert (state_dir / "ops_today_cache.json").exists()
    assert json.loads((state_dir / "ops_today_cache.json").read_text(encoding="utf-8"))[
        "pool"
    ]["file"] == _POOL_FILE


def test_get_route_statically_calls_the_writing_compute() -> None:
    """Tie the GET branch to the independently exercised write-backed entrypoint."""
    assert _calls_ops_compute(_ops_get_branch())


@pytest.mark.xfail(
    strict=True, raises=KnownContractGap,
    reason="GET /api/ops_today directly invokes write-backed compute",
)
def test_get_route_should_not_invoke_write_backed_compute() -> None:
    """A read-only GET projection must not trigger state/cache creation."""
    if _calls_ops_compute(_ops_get_branch()):
        raise KnownContractGap("GET route invokes write-backed compute")


@pytest.mark.parametrize("contract", ["reload_count", "freshness_asof"])
@pytest.mark.xfail(
    strict=True, raises=KnownContractGap,
    reason="cache hit compares pool filename but not data as-of",
)
def test_same_pool_filename_must_not_mask_changed_market_data(
    tmp_path: Path, contract: str,
) -> None:
    """Check reload count and returned as-of independently for changed market data."""
    latest_date = [20260102]
    load_calls = []

    def load_books(stocks: Any) -> Any:
        load_calls.append(tuple(stocks))
        # An integer trade_date yields a string asof, so the signal loop skips
        # this fake book; no pandas/parquet or live signal analysis is needed.
        dates = SimpleNamespace(iloc={-1: latest_date[0]})
        return {"000001.SZ": {"trade_date": dates}}

    compute = _compute_from_ast(tmp_path, load_books)
    first = compute()
    assert first["freshness"]["asof"] == "20260102"
    latest_date[0] = 20260103
    second = compute()
    if contract == "reload_count":
        if len(load_calls) != 2:
            raise KnownContractGap("market data changed without reloading books")
    else:
        if second["freshness"]["asof"] != "20260103":
            raise KnownContractGap("cached market data as-of is stale")


def test_unchanged_pool_cache_hit_does_not_reload_books(tmp_path: Path) -> None:
    """An unchanged pool and data snapshot may reuse the saved cache."""
    load_calls: list[tuple[str, ...]] = []

    def load_books(stocks: Any) -> dict[str, Any]:
        load_calls.append(tuple(stocks))
        return {}

    compute = _compute_from_ast(tmp_path, load_books)
    first = compute()
    second = compute()
    assert len(load_calls) == 1
    assert first["pool"] == second["pool"]
    assert second["cached"] is True
    assert first.get("cached") is not True


@pytest.mark.parametrize("contract", ["reload_count", "pool_members", "tier_count"])
@pytest.mark.xfail(
    strict=True, raises=KnownContractGap,
    reason="cache hit compares filename but ignores changed contents of the same pool file",
)
def test_same_pool_filename_must_not_mask_changed_pool_contents(
    tmp_path: Path, contract: str,
) -> None:
    """A changed same-name pool must invalidate cached stock and tier metadata."""
    load_calls: list[tuple[str, ...]] = []

    def load_books(stocks: Any) -> dict[str, Any]:
        load_calls.append(tuple(stocks))
        return {}

    compute = _compute_from_ast(tmp_path, load_books)
    first = compute()
    assert first["pool"]["n"] == 1
    pool_file = tmp_path / "pool" / _POOL_FILE
    pool_file.write_text(
        json.dumps([
            {"stock": "000001.SZ", "_tier": "B"},
            {"stock": "000002.SZ", "_tier": "B"},
        ]),
        encoding="utf-8",
    )
    second = compute()
    if contract == "reload_count":
        if load_calls != [("000001.SZ",), ("000001.SZ", "000002.SZ")]:
            raise KnownContractGap("changed same-name pool did not reload stocks")
    elif contract == "pool_members":
        if second["pool"]["n"] != 2:
            raise KnownContractGap("changed same-name pool returned stale stock count")
    else:
        if second["pool"]["tiers"] != {"B": 2}:
            raise KnownContractGap("changed same-name pool returned stale tiers")
