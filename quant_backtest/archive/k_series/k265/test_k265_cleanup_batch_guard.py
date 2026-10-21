# -*- coding: utf-8 -*-
"""Offline regression for the K265 cleanup deletion batch boundary.

The production script has import-time filesystem and graph side effects.  This
module only reads its source and executes two isolated list comprehensions;
no production module, ledger, graph client, or production script is loaded.
"""

import ast
import re
from pathlib import Path
from typing import Any

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "k265_rj_cleanup.py"
_SOURCE_TREE = ast.parse(_SCRIPT.read_text(encoding="utf-8"), filename=str(_SCRIPT))


def _assignment(name: str) -> ast.Assign:
    """Return exactly one top-level assignment, failing on source drift."""
    found = [
        node for node in _SOURCE_TREE.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    if len(found) != 1:
        raise ValueError(f"Expected one top-level {name} assignment, found {len(found)}")
    return found[0]


def _deletion_query() -> str:
    """Extract only the Cypher string from the guarded sess.run DELETE call."""
    candidates: list[str] = []
    for node in ast.walk(_SOURCE_TREE):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "run"):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "sess"):
            continue
        if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        if re.search(r"\bDELETE\s+r\b", node.args[0].value, re.IGNORECASE):
            candidates.append(node.args[0].value)
    if len(candidates) != 1:
        raise ValueError(f"Expected one literal DELETE query, found {len(candidates)}")
    return candidates[0]


def _batch_guard(query: str) -> bool:
    """Recognize the exact rj1 batch guard in DELETE's WHERE clause."""
    normalized = " ".join(query.split())
    section = re.search(r"\bWHERE\b(.*?)\bDELETE\s+r\b", normalized, re.IGNORECASE)
    if section is None:
        raise ValueError("DELETE query lacks an identifiable WHERE ... DELETE r section")
    return bool(re.search(
        r"\br\.batch\s*=\s*['\"]rj1['\"]", section.group(1), re.IGNORECASE
    ))


# Resolve AST prerequisites during collection, before any expected-failure mark
# can mask a missing file, syntax error, ambiguous query, or changed selector.
_RJ1_ASSIGNMENT = _assignment("rj1")
_KILL_ASSIGNMENT = _assignment("kill")
if not isinstance(_RJ1_ASSIGNMENT.value, ast.ListComp):
    raise ValueError("rj1 selector is no longer a list comprehension")
if not isinstance(_KILL_ASSIGNMENT.value, ast.ListComp):
    raise ValueError("kill selector is no longer a list comprehension")
_DELETE_QUERY = _deletion_query()
_DELETE_WHERE = re.search(r"\bWHERE\b(.*?)\bDELETE\s+r\b", " ".join(_DELETE_QUERY.split()), re.IGNORECASE)
if _DELETE_WHERE is None:
    raise ValueError("DELETE query does not expose its deletion predicate")
if not re.search(r"\btype\(r\)\s*=\s*k\.ty\b", _DELETE_WHERE.group(1), re.IGNORECASE):
    raise ValueError("DELETE predicate no longer matches the selected edge type")
if not re.search(r"\br\.source\s*=\s*['\"]concept_llm_v1['\"]", _DELETE_WHERE.group(1), re.IGNORECASE):
    raise ValueError("DELETE predicate no longer matches the selected edge source")
_HAS_BATCH_GUARD = _batch_guard(_DELETE_QUERY)


def _selected_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate just the two pure selector assignments against fake graph rows."""
    isolated = ast.fix_missing_locations(ast.Module(
        body=[_RJ1_ASSIGNMENT, _KILL_ASSIGNMENT], type_ignores=[]
    ))
    scope: dict[str, Any] = {"__builtins__": {}, "edges": edges, "white": set()}
    exec(compile(isolated, str(_SCRIPT), "exec"), scope)
    selected: list[dict[str, Any]] = scope["kill"]
    return selected


def _matching_delete_candidates(
    rows: list[dict[str, Any]], edges: list[dict[str, Any]], batch_guard: bool
) -> list[dict[str, Any]]:
    """Model only the name/type/source match and optional batch restriction."""
    return [
        edge for edge in edges
        if any(edge["s"] == row["s"] and edge["t"] == row["t"]
               and edge["ty"] == row["ty"] for row in rows)
        and edge["source"] == "concept_llm_v1"
        and (not batch_guard or edge["b"] == "rj1")
    ]


def test_selector_and_cypher_risk_with_two_batches() -> None:
    """Prove rj1 selection cannot by itself protect the other-batch twin."""
    edges: list[dict[str, Any]] = [
        {"s": "synthetic_src", "t": "synthetic_dst", "ty": "SUPPLIES",
         "source": "concept_llm_v1", "b": "rj1", "c": 0.3},
        {"s": "synthetic_src", "t": "synthetic_dst", "ty": "SUPPLIES",
         "source": "concept_llm_v1", "b": "other_batch", "c": 0.8},
        {"s": "synthetic_src", "t": "synthetic_dst", "ty": "SUPPLIES",
         "source": "different_source", "b": "rj1", "c": 0.5},
    ]
    # The real read query filters r.source upstream; mirror that input to the
    # selectors so only one graph row enters kill.
    graph_rows = [edge for edge in edges if edge["source"] == "concept_llm_v1"]
    selected = _selected_edges(graph_rows)
    assert len(selected) == 1
    assert selected[0]["b"] == "rj1"
    params = [{"s": edge["s"], "t": edge["t"], "ty": edge["ty"]} for edge in selected]
    broad_matches = _matching_delete_candidates(params, edges, batch_guard=False)
    assert {edge["b"] for edge in broad_matches} == {"rj1", "other_batch"}
    actual_matches = _matching_delete_candidates(params, edges, _HAS_BATCH_GUARD)
    assert len(actual_matches) == (1 if _HAS_BATCH_GUARD else 2)
    assert all(edge["source"] == "concept_llm_v1" for edge in actual_matches)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="K265 DELETE Cypher currently lacks r.batch='rj1' despite rj1 kill selection",
)
def test_delete_cypher_must_restrict_batch_rj1() -> None:
    """Target contract: DELETE itself must protect other-batch twin edges."""
    assert _HAS_BATCH_GUARD, "DELETE matches name/type/source but not r.batch"
