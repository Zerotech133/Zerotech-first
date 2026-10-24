# -*- coding: utf-8 -*-
"""os_gate_check 合同校验器回归测试（WP0 自测，全内存、不写盘）。

运行：
  cd tools && python -m pytest tests -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import os_gate_check as G  # noqa: E402
import os_samples as S  # noqa: E402


def _ev(events):
    return G.evaluate_events(S.seal(events))


def _fail_ids(rep):
    return [c["id"] for c in rep.checks if c["status"] == "FAIL"]


# ---------------------------------------------------------------- 正例

def test_p1_real_claim_pass():
    rep = _ev(S.base_events("real"))
    assert rep.verdict == "PASS", (rep.verdict, _fail_ids(rep))
    assert rep.exit_code == 0
    assert all(rep.gates["G%d" % i]["status"] == "PASS" for i in range(8))
    assert rep.honesty["model_calls"] == 2
    assert rep.honesty["rule_assisted"] == 1
    assert rep.honesty["user_verdict"] is True
    assert rep.state == G.S["ACCEPTED"]


def test_p2_simulated_claim_isolated_only():
    rep = _ev(S.base_events("simulated"))
    assert rep.verdict == "ISOLATED_ONLY", _fail_ids(rep)
    assert rep.exit_code == 3
    assert rep.gates["G5"]["status"] == "ISOLATED_ONLY"
    assert rep.gates["G7"]["status"] == "PASS"


def test_p3_blocked_authority():
    rep = G.evaluate_events(S._blocked_events())
    assert rep.verdict == "BLOCKED", _fail_ids(rep)
    assert rep.exit_code == 3
    assert rep.gates["G4"]["status"] == "BLOCKED"
    assert rep.gates["G5"]["status"] == "BLOCKED"


def test_p4_rule_only_not_full_pass():
    rep = _ev(S.base_events("real", rule_only=True))
    assert rep.verdict == "ISOLATED_ONLY", _fail_ids(rep)
    assert rep.gates["G2"]["status"] == "ISOLATED_ONLY"


def test_rework_changes_requested_not_accepted():
    events = S.base_events("real")
    S.find(events, "verdict.recorded")["payload"]["decision"] = "changes_requested"
    S.find(events, "verdict.recorded")["payload"]["feedback"] = "标题文案要改"
    rep = _ev(events)
    assert rep.verdict == "BLOCKED", _fail_ids(rep)
    assert rep.state == G.S["REWORK"]
    assert rep.honesty["user_verdict"] is False


# ---------------------------------------------------------------- 负例

@pytest.mark.parametrize("name,fn,expect", S.NEGATIVES,
                         ids=[n for n, _f, _c in S.NEGATIVES])
def test_negatives_rejected(name, fn, expect):
    if name == "N10_tamper":
        events = fn(S.seal(S.base_events("real")))
    else:
        events = S.seal(fn(S.base_events("real")))
    rep = G.evaluate_events(events)
    assert rep.exit_code == 1, "%s 应判失败，实际 %s / %s" % (name, rep.verdict, _fail_ids(rep))
    assert expect in _fail_ids(rep), "%s 缺 %s：%s" % (name, expect, _fail_ids(rep))


# ---------------------------------------------------------------- 链完整性

def test_hash_chain_detects_reorder():
    events = S.seal(S.base_events("real"))
    events[5], events[6] = events[6], events[5]  # 换位不重封
    rep = G.evaluate_events(events)
    assert "CHAIN_HASH" in _fail_ids(rep)
    assert rep.exit_code == 1


def test_envelope_requires_fields():
    events = S.seal(S.base_events("real"))
    del events[0]["actor"]
    rep = G.evaluate_events(events)
    assert "ENVELOPE_REQUIRED" in _fail_ids(rep)


def test_missing_ledger_input_error():
    rep = G.evaluate("<not-exist-dir>")
    assert rep.exit_code == 2
    assert rep.verdict == "INPUT_ERROR"


def test_selftest_mode_passes():
    assert G._selftest() == 0


def test_positive_samples_validate_against_schema():
    import json

    import jsonschema

    schema_path = os.path.join(os.path.dirname(TOOLS), "contracts",
                               "item_contract_v1.schema.json")
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = jsonschema.Draft202012Validator(schema)
    cases = [
        S.seal(S.base_events("real")),
        S.seal(S.base_events("simulated")),
        S.seal(S.base_events("real", rule_only=True)),
        S._blocked_events(),
    ]
    for events in cases:
        for ev in events:
            errs = list(validator.iter_errors(ev))
            assert not errs, (ev.get("type"), errs[0].message)

