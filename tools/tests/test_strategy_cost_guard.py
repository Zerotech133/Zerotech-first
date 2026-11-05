"""60 日主基线成本阈值告警测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

from factor_portfolio import (  # noqa: E402
    COST_ALERT_THRESHOLD_BP,
    COST_BREAKEVEN_BP,
    assess_cost,
)
import strategy_cost_guard as guard  # noqa: E402


def test_constants_are_research_baseline():
    assert COST_ALERT_THRESHOLD_BP == 100.0
    assert COST_BREAKEVEN_BP == 198.0


def test_ok_at_and_below_threshold():
    for cost in (0.0, 30.0, 99.9, 100.0):
        r = assess_cost(cost)
        assert r["status"] == "ok"
        assert r["capacity_review_required"] is False
        assert r["headroom_bp"] == pytest.approx(198.0 - cost, abs=0.01)


def test_warning_between_threshold_and_breakeven():
    r = assess_cost(120.0)
    assert r["status"] == "warning"
    assert r["capacity_review_required"] is True
    assert r["headroom_bp"] == 78.0
    # 100bp 1.1353% 与 150bp 0.5537% 之间线性插值
    assert r["estimated_ann_return"] == pytest.approx(0.009027, abs=1e-5)
    assert "重评容量" in r["message"]


def test_boundary_breakeven_and_critical():
    assert assess_cost(198.0)["status"] == "warning"
    r = assess_cost(199.0)
    assert r["status"] == "critical"
    assert "盈亏平衡" in r["message"]
    assert "无经济性" in r["message"]


def test_outside_tested_curve():
    r = assess_cost(300.0)
    assert r["status"] == "critical"
    assert r["estimated_ann_return"] is None
    assert r["estimated_ann_return_note"] == "outside_tested_curve_0_250bp"


def test_invalid_inputs():
    with pytest.raises(ValueError):
        assess_cost(-1.0)
    with pytest.raises(ValueError):
        assess_cost(120.0, threshold_bp=150.0, breakeven_bp=150.0)


def test_alert_text_contains_key_fields():
    payload = guard.build_payload(type("A", (), {
        "cost_bp": 120.0, "from_file": None, "threshold_bp": 100.0,
        "breakeven_bp": 198.0,
    })())
    text = guard.build_alert_text(payload)
    assert "120.0bp" in text
    assert "100bp" in text
    assert "198bp" in text
    assert "重评容量" in text


def test_cli_ok_warning_critical_codes(tmp_path):
    assert guard.main(["--cost-bp", "80", "--no-write"]) == 0
    assert guard.main(["--cost-bp", "120", "--no-write"]) == 2
    assert guard.main(["--cost-bp", "220", "--no-write"]) == 3
    assert guard.main(["--cost-bp", "120", "--no-write", "--no-fail"]) == 0


def test_cli_from_file_json(tmp_path):
    p = tmp_path / "live_cost.json"
    p.write_text(json.dumps({"live_cost_bp": 130}), encoding="utf-8")
    assert guard.main(["--from-file", str(p), "--no-write"]) == 2
    assert guard.main(["--from-file", str(p), "--json", "--no-write"]) == 2


def test_cli_from_file_utf8_bom(tmp_path):
    p = tmp_path / "live_cost_bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"live_cost_bp": 130}).encode("utf-8"))
    assert guard.main(["--from-file", str(p), "--no-write"]) == 2


def test_cli_missing_input_returns_4(monkeypatch):
    monkeypatch.delenv("STRATEGY_COST_BP", raising=False)
    assert guard.main(["--no-write"]) == 4


def test_alert_card_uses_project_v3_style():
    payload = guard.build_payload(
        type("A", (), {
            "cost_bp": 120.0, "from_file": None, "threshold_bp": 100.0,
            "breakeven_bp": 198.0,
        })()
    )
    card = guard.build_alert_card(payload)
    assert card is not None
    assert card["schema"] == "2.0"
    assert card["header"]["template"] == "orange"
    assert "WARNING" in card["header"]["title"]["content"]
    body = json.dumps(card["body"], ensure_ascii=False)
    assert "120.0bp" in body
    assert "100bp" in body and "198bp" in body
    assert "#cost_alert" in body


def test_alert_card_critical_uses_red_header():
    payload = guard.build_payload(
        type("A", (), {
            "cost_bp": 220.0, "from_file": None, "threshold_bp": 100.0,
            "breakeven_bp": 198.0,
        })()
    )
    card = guard.build_alert_card(payload)
    assert card is not None
    assert card["header"]["template"] == "red"
    assert "CRITICAL" in card["header"]["title"]["content"]


def test_send_feishu_only_on_warning(monkeypatch):
    calls = []

    def fake_send_card(content, msg_type):
        calls.append((content, msg_type))
        return True

    def fake_send_text(text):
        calls.append((text, "text"))
        return True

    fake = type(sys)("send_feishu")
    fake.USER_ID = "ou_test"
    fake.send_content = fake_send_card
    fake.send_text = fake_send_text
    monkeypatch.setitem(sys.modules, "send_feishu", fake)

    assert guard.main(["--cost-bp", "120", "--send", "--no-write"]) == 2
    assert len(calls) == 1
    assert calls[0][1] == "interactive"
    card = json.loads(calls[0][0])
    assert "WARNING" in card["header"]["title"]["content"]
    assert "120.0bp" in json.dumps(card["body"], ensure_ascii=False)

    assert guard.main(["--cost-bp", "80", "--send", "--no-write"]) == 0
    assert len(calls) == 1


def test_send_feishu_card_falls_back_to_text(monkeypatch):
    calls = []

    def fake_send_card(content, msg_type):
        return False

    def fake_send_text(text):
        calls.append(text)
        return True

    fake = type(sys)("send_feishu")
    fake.USER_ID = "ou_test"
    fake.send_content = fake_send_card
    fake.send_text = fake_send_text
    monkeypatch.setitem(sys.modules, "send_feishu", fake)

    result = guard._send_feishu("fallback text", {"schema": "2.0"})
    assert result["ok"] is True
    assert result["mode"] == "text"
    assert calls == ["fallback text"]
    assert result["card_fallback"]["ok"] is False
