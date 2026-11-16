# -*- coding: utf-8 -*-
"""ACAD1 → D1 桥的规则测试（2026-11-16 WO-ACAD1-D1-BRIDGE）。

只测纯函数（decide / judged / gid），绝不真发 HTTP、不写账本。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "acad1_pool_bridge_t", REPO / "tools" / "acad1_pool_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


br = _load()


def _item(**kw) -> dict:
    base = {"code": "600000.SH", "market": "CN", "signal": "CHAN,CHAN_V7",
            "selection_reason": "基础信号：买点；来源[v1-cn-strat-4]；评分100.0。",
            "risk_flags": [], "score": 100.0, "name": "测试"}
    base.update(kw)
    return base


def test_decide_buy_when_reason_says_buy_point():
    d = br.decide(_item())
    assert d["tier"] == "BUY"
    assert 0.45 <= d["confidence"] <= 0.70
    assert "买点" in d["why"]


def test_decide_hold_when_risk_flags():
    d = br.decide(_item(risk_flags=["ST"]))
    assert d["tier"] == "HOLD" and "风险标记" in d["why"]
    # 同 score 下 risk 版本置信度更低
    assert d["confidence"] < br.decide(_item())["confidence"]


def test_decide_sell_when_sell_signal():
    d = br.decide(_item(signal="卖点,顶部背离", selection_reason="基础信号：卖点"))
    assert d["tier"] == "SELL"


def test_decide_hold_when_no_buy_evidence():
    d = br.decide(_item(signal="未知信号", selection_reason="研究池候选，无方向"))
    assert d["tier"] == "HOLD" and "无可采信买点依据" in d["why"]


def test_confidence_scales_with_score():
    hi = br.decide(_item(score=100.0))["confidence"]
    mid = br.decide(_item(score=50.0))["confidence"]
    lo = br.decide(_item(score=0.0))["confidence"]
    assert hi >= mid >= lo
    assert hi == 0.70 and lo == 0.45  # 归一上下界（0.45 + 0.25×score/100）


def test_gid_maps_markets():
    assert br._gid("600000.SH") == "sh600000"
    assert br._gid("000001.SZ") == "sz000001"


def test_projection_without_market_refused(monkeypatch, tmp_path):
    """回退视图 projection 内没有 CN → 必须 REFUSED rc=2，不能报"无事可做"。"""
    monkeypatch.setattr(br, "LOG", tmp_path / "log.txt")
    monkeypatch.setattr(br, "fetch_pool", lambda timeout=20: {
        "pool_date": "2026-11-16", "source_mode": "projection",
        "review_status": "pool_reviewed", "count": 11,
        "items": [{"code": "02331", "market": "HK"}, {"code": "DOGE/USDT", "market": "CRYPTO"}],
    })
    assert br.main(["--dry-run"]) == 2
    text = (tmp_path / "log.txt").read_text(encoding="utf-8")
    assert "REFUSED 回退视图 projection 内没有 CN 标的" in text


def test_shadow_source_refused(monkeypatch, tmp_path):
    """shadow = 非官方回退源 → REFUSED，不拿退化数据裁决。"""
    monkeypatch.setattr(br, "LOG", tmp_path / "log.txt")
    monkeypatch.setattr(br, "fetch_pool", lambda timeout=20: {
        "pool_date": "2026-11-15", "source_mode": "shadow", "review_status": "shadow_only",
        "degraded_reason": "pg_disabled_by_env", "count": 14,
        "items": [{"code": "001326.SZ", "market": None}],
    })
    assert br.main(["--dry-run"]) == 2
    text = (tmp_path / "log.txt").read_text(encoding="utf-8")
    assert "池来源已退化" in text and "shadow" in text


def test_judged_reads_source_and_pool_date(tmp_path, monkeypatch):
    f = tmp_path / "d.jsonl"
    f.write_text("\n".join([
        json.dumps({"code": "600000.SH", "source": "acad1_pool_bridge",
                    "evidence": [{"kind": "acad1_pool", "value": "2026-11-16/x"}]}),
        json.dumps({"code": "000001.SZ", "source": "invest_committee_v2", "evidence": []}),
        json.dumps({"code": "300750.SZ", "source": "acad1_pool_bridge",
                    "evidence": [{"kind": "acad1_pool", "value": "2026-11-15/x"}]}),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(br, "DECISIONS", f)
    got = br.judged("2026-11-16")
    assert got == {"600000.SH"}  # 只算本池日 + 本来源
    assert br.judged("2026-11-15") == {"300750.SZ"}
