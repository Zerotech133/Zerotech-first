# -*- coding: utf-8 -*-
"""D1 证据/规则门测试（2026-10-30 WO-D1-EVIDENCE）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))

import decision_policy as DP  # noqa: E402
import evidence_bundle as EB  # noqa: E402


def _ctx(l4="L4=定性成立", include_fund=True):
    ctx = {
        "market_technical": "K线 8 日数据 ...",
        "news_event": "情绪代理指标 ...",
        "final_decision": l4,
        "name": "测试股",
    }
    if include_fund:
        ctx["fundamental"] = "PE=20 PB=3 ..."
    return ctx


def test_bundle_sufficient_and_l4():
    b = EB.EvidenceBundle.from_context("600519", "贵州茅台", _ctx())
    assert b.is_sufficient()
    assert b.l4_status == "established"
    assert len(b.refs) == 4
    assert all(r.hash and r.as_of for r in b.refs)


def test_bundle_missing_fundamental_blocks():
    b = EB.EvidenceBundle.from_context("600519", "贵州茅台", _ctx(include_fund=False))
    assert not b.is_sufficient()
    assert "missing:fundamental" in b.input_degraded


def test_policy_l4_missing_forces_watch():
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx(l4=""))
    out = DP.apply_policy({"recommendation": "BUY", "confidence": 0.8}, b,
                          run_id="r1", date="2026-10-30")
    assert out["decision"]["recommendation"] == "WATCH"
    assert out["watch_reason"] == "upstream_l4_missing"
    assert out["blocked"] is True


def test_policy_l4_unresolved_forces_watch():
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx(l4="L4=存疑观察"))
    out = DP.apply_policy({"recommendation": "SELL", "confidence": 0.7}, b,
                          run_id="r1", date="2026-10-30")
    assert out["watch_reason"] == "l4_unresolved"
    assert out["decision"]["recommendation"] == "WATCH"


def test_policy_confidence_floor_downgrades():
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx())
    out = DP.apply_policy({"recommendation": "BUY", "confidence": 0.3}, b,
                          run_id="r1", date="2026-10-30")
    assert out["decision"]["recommendation"] == "HOLD"
    assert out["watch_reason"] == "confidence_floor"
    assert out["decision"]["confidence_raw"] == 0.3


def test_policy_no_evidence_blocks():
    out = DP.apply_policy({"recommendation": "BUY", "confidence": 0.9}, None,
                          run_id="r1", date="2026-10-30")
    assert out["decision"]["recommendation"] == "WATCH"
    assert out["watch_reason"] == "no_evidence"


def test_decision_id_deterministic():
    a = DP.decision_id("600519", "2026-10-30", "run1")
    b = DP.decision_id("600519", "2026-10-30", "run1")
    assert a == b and a.startswith("D1-20261030-")


def test_ref_serializable():
    import json
    b = EB.EvidenceBundle.from_context("600519", "贵州茅台", _ctx())
    json.dumps([r.to_dict() for r in b.refs])  # 不抛=可落账
    assert b.refs[0].to_dict()["source"] in ("market_technical", "fundamental")


def test_model_watch_gets_reason():
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx(l4="L4=存疑观察"))
    out = DP.apply_policy({"recommendation": "WATCH", "confidence": 0.55}, b,
                          run_id="r1", date="2026-10-31")
    assert out["watch_reason"] == "l4_unresolved"

    b2 = EB.EvidenceBundle.from_context("600519", "x", _ctx())
    out2 = DP.apply_policy({"recommendation": "WATCH", "confidence": 0.55}, b2,
                           run_id="r1", date="2026-10-31")
    assert out2["watch_reason"] == "model_watch"


def test_established_allows_direction():
    """只有 L4=established 且证据充分，BUY/SELL 才不被规则门压掉。"""
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx())
    out = DP.apply_policy({"recommendation": "BUY", "confidence": 0.72}, b,
                          run_id="r1", date="2026-10-31")
    assert out["decision"]["recommendation"] == "BUY"
    assert out["watch_reason"] is None


# ── 2026-10-31 WO-D1-B123-20261031（B1）：无 artifact 的 established 不得放行 ──
def _fake_established(**overrides):
    rec = {"status": "established", "source": "xsec", "as_of": "2026-10-30",
           "artifact_ref": ""}
    rec.update(overrides)
    return rec


def test_policy_blocks_buy_and_sell_on_no_research_artifact():
    """上游 dict 声称 established 但无 artifact_ref → 降级 unresolved，禁 BUY/SELL。"""
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx(),
                                       l4=_fake_established())
    assert b.l4_status == "unresolved"
    assert b.l4_reason == "no_research_artifact"
    assert "no_research_artifact" in b.l4_source
    for raw in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL"):
        out = DP.apply_policy({"recommendation": raw, "confidence": 0.9}, b,
                              run_id="r1", date="2026-10-31")
        assert out["decision"]["recommendation"] == "WATCH", raw
        assert out["blocked"] is True, raw
        assert out["watch_reason"] == "l4_unresolved", raw


def test_valid_artifact_established_still_allows_buy(tmp_path):
    """对照：带可核验 artifact 的 established 仍按原语义允许方向（不误伤）。"""
    art = tmp_path / "RA-ok.json"
    art.write_text(
        '{"schema": "research_artifact/v1", "artifact_id": "RA-1",'
        ' "created": "2026-10-31T04:45:00+08:00",'
        ' "data_version": "tdx-parquet@2026-10-28 sha256=' + "d" * 64 + '"}',
        encoding="utf-8")
    b = EB.EvidenceBundle.from_context(
        "600519", "x", _ctx(),
        l4=_fake_established(artifact_ref="RA-ok.json"), artifact_root=tmp_path)
    assert b.l4_status == "established"
    assert b.l4_reason == ""
    out = DP.apply_policy({"recommendation": "BUY", "confidence": 0.72}, b,
                          run_id="r1", date="2026-10-31")
    assert out["decision"]["recommendation"] == "BUY"
    assert out["watch_reason"] is None


def test_bundle_to_dict_carries_l4_reason():
    """机读原因必须可落账（日报/账本消费 to_dict）。"""
    b = EB.EvidenceBundle.from_context("600519", "x", _ctx(),
                                       l4=_fake_established())
    d = b.to_dict()
    assert d["l4_status"] == "unresolved"
    assert d["l4_reason"] == "no_research_artifact"
