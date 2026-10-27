#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EnvGate / GateDecision 契约测试：字段齐全、safe 三值透传、
判定即账本行、LakeFeeder 缺维整链降权。"""

import json

from gatekeeper import GateDecision, EnvGate
from gatekeeper.feeders.lake import LakeFeeder
from gatekeeper.ledger import Ledger
from gatekeeper.policies import v3_breadth40_med20 as v3


class StubFeeder:
    name = "stub"

    def __init__(self, anchor, feats):
        self.anchor, self.feats = anchor, feats

    def get_features(self, market, date=None):
        return self.anchor, self.feats


class StubPolicy:
    ver = "stub_v1"
    sha = "0123456789abcdef"

    def evaluate(self, feats):
        if feats.get("breadth") is None:
            return None, {"missing": ["breadth"]}
        return feats["breadth"] > 0.5, {"over_half": feats["breadth"] > 0.5}


def test_decision_fields(tmp_path):
    gate = EnvGate(
        StubPolicy(), StubFeeder("20261014", {"breadth": 0.6, "med20": 0.02})
    )
    dec = gate.decide("cn")
    assert isinstance(dec, GateDecision)
    assert dec.market == "cn" and dec.date == "20261014"
    assert dec.safe is True and dec.conditions == {"over_half": True}
    assert dec.features["anchor_date"] == "20261014"
    assert dec.policy_ver == "stub_v1" and dec.policy_sha == "0123456789abcdef"
    assert dec.feeder == "stub" and dec.ts
    row = dec.to_row()
    assert set(row) == {
        "market",
        "date",
        "safe",
        "conditions",
        "features",
        "policy_ver",
        "policy_sha",
        "feeder",
        "ts",
    }


def test_safe_none_passthrough():
    """缺维 → safe=None 原样透传（不误杀，不转 False）。"""
    gate = EnvGate(StubPolicy(), StubFeeder("20261014", {"breadth": None}))
    dec = gate.decide("cn")
    assert dec.safe is None and dec.conditions == {"missing": ["breadth"]}


def test_decide_appends_ledger(tmp_path):
    """传 ledger 则每次判定原子落账；不传只判不记。"""
    led = Ledger(str(tmp_path / "dec.jsonl"))
    gate = EnvGate(StubPolicy(), StubFeeder("20261014", {"breadth": 0.3}), ledger=led)
    gate.decide("cn")
    gate.decide("cn")
    assert led.count() == 2
    assert len(led.query("cn", "20261014")) == 2

    gate2 = EnvGate(StubPolicy(), StubFeeder("20261014", {"breadth": 0.3}))
    assert gate2.decide("cn") is not None
    assert led.count() == 2  # 无 ledger 不落账


def test_envgate_with_v3_and_lake(tmp_path):
    """整链：V3Policy + LakeFeeder（湖无 med20）→ safe=None 缺维降权。"""
    fp = str(tmp_path / "series.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"dates": ["20261013", "20261014"], "breadth": [0.35, 0.30]}, f)
    pol = v3.V3Policy(str(tmp_path / "policy.json"))
    gate = EnvGate(pol, LakeFeeder(fp))
    dec = gate.decide("cn")
    assert dec.safe is None  # med20 缺维
    assert dec.features["breadth"] == 0.30  # T-1 锚=最后有效值
    assert dec.features["anchor_semantics"] == "T-1"
    assert dec.policy_ver == "v3_breadth40_med20"
    assert dec.feeder == "lake"
