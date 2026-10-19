# -*- coding: utf-8 -*-
"""Offline discovery test for cross-ledger replay(root) path isolation.

Only Python source is parsed. The synthetic .db files are path sentinels, not
SQLite databases; neither production replay_query nor a database is opened.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "adapters" / "replay_query.py"
LEDGERS = {
    "_load_idem_rows": ("idempotency", "idempotency.db", "_IDEM_DB"),
    "_load_approvals": ("approvals", "approvals.db", "_APPROVALS_DB"),
}
KNOWN_GAPS = {
    "_load_idem_rows": [
        "loader lacks a root parameter",
        "exists/connect path is not derived from root",
        "actual read references module-global _IDEM_DB",
        "replay does not pass root to the loader",
        "replay status check references _IDEM_DB",
    ],
    "_load_approvals": [
        "loader lacks a root parameter",
        "exists/connect path is not derived from root",
        "actual read references module-global _APPROVALS_DB",
        "replay does not pass root to the loader",
    ],
}


def _definition(tree: ast.Module, name: str) -> ast.FunctionDef:
    """Return a top-level function or fail as a test setup error."""
    matches = [node for node in tree.body
               if isinstance(node, ast.FunctionDef) and node.name == name]
    assert len(matches) == 1, f"Expected exactly one {name} definition"
    return matches[0]


def _calls(function: ast.FunctionDef, name: str) -> List[ast.Call]:
    """Find direct calls, without executing any production expression."""
    return [node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name]


class _KnownReplayIsolationGap(AssertionError):
    """Only the exact, previously observed ledger-root isolation defect."""


def _root_dependent(expression: ast.AST, function: ast.FunctionDef,
                    tree: ast.Module, visited: frozenset[str] = frozenset()) -> bool:
    """Conservatively prove a path is built from root, never execute it.

    Unknown expression shapes fail closed. In particular, a root appearing in
    one branch or a discarded tuple element is not sufficient evidence.
    """
    if isinstance(expression, ast.Name):
        if expression.id == "root":
            return "root" in {arg.arg for arg in
                              function.args.posonlyargs + function.args.args}
        if expression.id in visited:
            return False
        values = [node.value for node in function.body
                  if isinstance(node, (ast.Assign, ast.AnnAssign))
                  and any(isinstance(target, ast.Name)
                          and target.id == expression.id
                          for target in (node.targets if isinstance(node, ast.Assign)
                                         else [node.target]))]
        return bool(values) and all(
            _root_dependent(value, function, tree, visited | {expression.id})
            for value in values)
    if isinstance(expression, ast.BinOp):
        if isinstance(expression.op, (ast.Div, ast.Mod)):
            operands = (expression.left, expression.right)
            return (all(_root_dependent(value, function, tree, visited)
                        or isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                        for value in operands)
                    and any(_root_dependent(value, function, tree, visited)
                            for value in operands))
        return False
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Attribute):
            return (expression.func.attr == "as_posix"
                    and not expression.args and not expression.keywords
                    and _root_dependent(expression.func.value, function,
                                        tree, visited))
        if not isinstance(expression.func, ast.Name):
            return False
        if expression.func.id in {"Path", "str"}:
            return (len(expression.args) == 1 and not expression.keywords
                    and _root_dependent(expression.args[0], function,
                                        tree, visited))
        helpers = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == expression.func.id]
        if len(helpers) != 1:
            return False
        helper = helpers[0]
        arguments = helper.args.posonlyargs + helper.args.args
        root_positions = [index for index, arg in enumerate(arguments)
                          if arg.arg == "root"]
        returns = [node.value for node in ast.walk(helper)
                   if isinstance(node, ast.Return) and node.value is not None]
        return bool(root_positions and returns) and (
            any(index < len(expression.args)
                and _root_dependent(expression.args[index], function,
                                    tree, visited)
                for index in root_positions)
            or any(keyword.arg == "root"
                   and _root_dependent(keyword.value, function, tree, visited)
                   for keyword in expression.keywords)
        ) and all(_root_dependent(value, helper, tree) for value in returns)
    return False


def _actual_read_paths_are_scoped(function: ast.FunctionDef,
                                  tree: ast.Module) -> bool:
    """Check exists guards and sqlite connect arguments, not unused path hints."""
    checks = [node for node in ast.walk(function)
              if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "exists"]
    connects = [node for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sqlite3"
                and node.func.attr == "connect"]
    return (bool(checks) and bool(connects)
            and all(_root_dependent(node.func.value, function, tree)
                    for node in checks)
            and all(node.args and _root_dependent(node.args[0], function, tree)
                    for node in connects))


@pytest.fixture
def isolated_replay_tree(tmp_path: Path) -> tuple[ast.Module, Path]:
    """Prepare a wholly synthetic root and parse only replay_query.py source."""
    agent_root = tmp_path / "agent_platform"
    for directory, filename, _global_name in LEDGERS.values():
        ledger = agent_root / "data" / directory
        ledger.mkdir(parents=True)
        (ledger / filename).write_bytes(b"SYNTHETIC PATH SENTINEL; NOT SQLITE\n")
    source = SOURCE.read_text(encoding="utf-8")
    parsed = ast.parse(source, filename=str(SOURCE))
    assert isinstance(parsed, ast.Module)
    return parsed, tmp_path


def test_offline_fixture_and_actual_read_sites(
        isolated_replay_tree: tuple[ast.Module, Path]) -> None:
    """Fail normally if the fixture or AST evidence cannot be inspected."""
    tree, root = isolated_replay_tree
    for loader_name, (directory, filename, _global_name) in LEDGERS.items():
        assert (root / "agent_platform" / "data" / directory / filename).is_file()
        loader = _definition(tree, loader_name)
        assert any(isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute)
                   and isinstance(node.func.value, ast.Name)
                   and node.func.value.id == "sqlite3"
                   and node.func.attr == "connect"
                   for node in ast.walk(loader)), loader_name
    _definition(tree, "replay")


@pytest.mark.parametrize("expression", [
    "root if False else _IDEM_DB",
    "(root, _IDEM_DB)[1]",
    "root if condition else _IDEM_DB",
])
def test_root_dependency_rejects_discarded_or_conditional_root(
        expression: str) -> None:
    """Synthetic AST adversaries cannot spoof a root-scoped ledger read."""
    tree = ast.parse("def loader(root):\n    return " + expression + "\n")
    loader = _definition(tree, "loader")
    result = loader.body[0]
    assert isinstance(result, ast.Return) and result.value is not None
    assert not _root_dependent(result.value, loader, tree)


def test_replay_root_scopes_both_actual_ledger_reads(
        isolated_replay_tree: tuple[ast.Module, Path], request: pytest.FixtureRequest
) -> None:
    """Strict expected failure until both ledger reads follow the caller root."""
    tree, _synthetic_root = isolated_replay_tree
    replay = _definition(tree, "replay")
    findings: Dict[str, List[str]] = {}
    for loader_name, (_directory, _filename, global_name) in LEDGERS.items():
        loader = _definition(tree, loader_name)
        issues: List[str] = []
        if "root" not in {arg.arg for arg in loader.args.args}:
            issues.append("loader lacks a root parameter")
        if not _actual_read_paths_are_scoped(loader, tree):
            issues.append("exists/connect path is not derived from root")
        if any(isinstance(node, ast.Name) and node.id == global_name
               for node in ast.walk(loader)):
            issues.append(f"actual read references module-global {global_name}")
        calls = _calls(replay, loader_name)
        if not calls or not all(
                any(isinstance(arg, ast.Name) and arg.id == "root"
                    for arg in call.args)
                or any(keyword.arg == "root"
                       and isinstance(keyword.value, ast.Name)
                       and keyword.value.id == "root"
                       for keyword in call.keywords)
                for call in calls):
            issues.append("replay does not pass root to the loader")
        if any(isinstance(node, ast.Name) and node.id == global_name
               for node in ast.walk(replay)):
            issues.append(f"replay status check references {global_name}")
        if issues:
            findings[loader_name] = issues

    # Unknown failures and AST shape drift must never be classified as known.
    # Keep the strict marker even after a fix: XPASS(strict) prompts review and
    # removal rather than silently passing a stale expected-failure contract.
    if findings and findings != KNOWN_GAPS:
        pytest.fail(f"Unexpected replay isolation findings: {findings}")
    request.node.add_marker(pytest.mark.xfail(
        strict=True, raises=_KnownReplayIsolationGap,
        reason="REBASELINE-001: exact known replay ledger-root gap"))
    if findings == KNOWN_GAPS:
        raise _KnownReplayIsolationGap(f"Known unscoped ledger reads: {findings}")
