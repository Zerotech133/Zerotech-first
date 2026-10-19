# -*- coding: utf-8 -*-
"""市场准入判定（admission）单测：F1 判定矩阵全覆盖 + F2 端点采集降级。

真源：量化大屏 v3 方案 §二（判定矩阵定稿）+ docs/dashboard_contracts.md §9。
纪律：缺源 → unknown（禁 fail-open）；关门一票否决优先；反证显式列出。
今日真值回归（20261016 快照实况）：risk_off / overall=-2 / prob=44 /
影子闸拦截 / 三门 0/3 → closed。
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

_QB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _QB not in sys.path:
    sys.path.insert(0, _QB)

import admission  # noqa: E402
from strategy_layer import dashboard_api as api  # noqa: E402

TH = admission.THRESHOLDS
TZ8 = timezone(timedelta(hours=8))


# ══ F1 引擎：判定矩阵 ══════════════════════════════════════════════
def test_closed_by_regime_veto():
    r = admission.evaluate_admission("risk_off", 5, 70, 3, False, False)
    assert r["verdict"] == "closed"
    assert r["verdict_cn"] == "关门"
    assert any("risk_off" in x for x in r["reasons"])


def test_closed_by_hard_block():
    r = admission.evaluate_admission("neutral", 5, 70, 3, False, True)
    assert r["verdict"] == "closed"
    assert any("硬拦" in x for x in r["reasons"])


def test_closed_double_veto_notes_alignment():
    r = admission.evaluate_admission("risk_off", -2, 44, 0, True, True)
    assert r["verdict"] == "closed"
    assert any("双判据同向" in x for x in r["reasons"])


def test_closed_veto_beats_missing_inputs():
    """risk_off 一票否决优先于缺源（禁 fail-open，也禁缺源掩盖关门）。"""
    r = admission.evaluate_admission("risk_off", None, None, None, None, None)
    assert r["verdict"] == "closed"


def test_closed_counter_evidence_wide_level_inputs():
    """关门但 overall/prob 已达宽门线 → 反证显式列出，不掩盖分歧。"""
    r = admission.evaluate_admission("risk_off", 5, 70, 3, False, False)
    assert any("宽门线" in x for x in r["counter_evidence"])
    # 今日真值（-2/44）不达宽门线 → 无该反证
    r2 = admission.evaluate_admission("risk_off", -2, 44, 0, True, False)
    assert not any("宽门线" in x for x in r2["counter_evidence"])


def test_narrow_neutral_overall_negative():
    r = admission.evaluate_admission("neutral", -1, 60, 3, False, False)
    assert r["verdict"] == "narrow"
    assert any("overall" in x for x in r["reasons"])


def test_narrow_neutral_prob_below_50():
    r = admission.evaluate_admission("neutral", 1, TH["narrow_prob_lt"] - 1,
                                     3, False, False)
    assert r["verdict"] == "narrow"
    assert any("prob" in x for x in r["reasons"])


def test_narrow_neutral_shadow_block():
    r = admission.evaluate_admission("neutral", 1, 60, 3, True, False)
    assert r["verdict"] == "narrow"
    assert any("影子闸" in x for x in r["reasons"])
    assert any("影子" in x for x in r["counter_evidence"])


def test_narrow_neutral_no_trigger_fallback():
    """neutral 未达任何窄门触发、也到不了宽门（宽门要求 risk_on）→ 兜底窄门。"""
    r = admission.evaluate_admission("neutral", 1, 60, 3, False, False)
    assert r["verdict"] == "narrow"
    assert any("兜底" in x for x in r["reasons"])


def test_narrow_risk_on_gates_short():
    r = admission.evaluate_admission("risk_on", 1, 70,
                                     TH["wide_gates_min"] - 1, False, False)
    assert r["verdict"] == "narrow"
    assert any("宽门条件不全" in x for x in r["reasons"])
    assert any("regime=risk_on" in x for x in r["counter_evidence"])


def test_narrow_risk_on_overall_not_positive():
    r = admission.evaluate_admission("risk_on", 0, 70, 3, False, False)
    assert r["verdict"] == "narrow"


def test_narrow_risk_on_prob_below_wide():
    r = admission.evaluate_admission("risk_on", 1, TH["wide_prob_min"] - 1,
                                     3, False, False)
    assert r["verdict"] == "narrow"


def test_wide_minimum_boundary():
    """宽门下限恰好压线：三门=2、overall>0、prob=55 → wide。"""
    r = admission.evaluate_admission("risk_on", 1, TH["wide_prob_min"],
                                     TH["wide_gates_min"], False, False)
    assert r["verdict"] == "wide"
    assert r["verdict_cn"] == "宽门"


def test_wide_comfortable():
    r = admission.evaluate_admission("risk_on", 3, 70, 3, False, False)
    assert r["verdict"] == "wide"
    assert r["counter_evidence"] == []


def test_wide_margin_counter_evidence():
    """宽门但 prob 逼近下限（不足 margin=5）→ 边际反证。"""
    r = admission.evaluate_admission("risk_on", 1,
                                     TH["wide_prob_min"] + 1,
                                     TH["wide_gates_min"], False, False)
    assert r["verdict"] == "wide"
    assert any("逼近下限" in x for x in r["counter_evidence"])
    assert any("刚好压线" in x for x in r["counter_evidence"])


def test_wide_blocked_by_hard_block_even_if_conditions_met():
    """风控硬拦一票否决：条件再好也关门。"""
    r = admission.evaluate_admission("risk_on", 3, 70, 3, False, True)
    assert r["verdict"] == "closed"


# ── F1 引擎：unknown（缺源不猜，禁 fail-open）───────────────────────
def test_unknown_missing_regime():
    r = admission.evaluate_admission(None, 1, 55, 2, False, False)
    assert r["verdict"] == "unknown"
    assert "regime" in r["missing"]


@pytest.mark.parametrize("kwargs,missing_key", [
    ({"overall": None}, "overall"),
    ({"prob": None}, "prob"),
    ({"gates_passed": None}, "gates_passed"),
    ({"hard_block": None}, "hard_block"),
])
def test_unknown_missing_each_input(kwargs, missing_key):
    base = {"regime": "risk_on", "overall": 1, "prob": 60, "gates_passed": 3,
            "shadow_block": False, "hard_block": False}
    base.update(kwargs)
    r = admission.evaluate_admission(**base)
    assert r["verdict"] == "unknown"
    assert missing_key in r["missing"]
    assert any("缺源" in x for x in r["reasons"])


def test_unknown_inputs_echoed_verbatim():
    """inputs 回显原始输入（不做值修饰），gates_passed 数字化后回显。"""
    r = admission.evaluate_admission("risk_on", "1", 60, "2", False, False)
    assert r["inputs"]["overall"] == "1"
    assert r["inputs"]["gates_passed"] == 2


# ── F1 引擎：输出形状（契约 §9）─────────────────────────────────────
def test_output_shape_contract():
    r = admission.evaluate_admission("risk_on", 3, 70, 3, False, False)
    for key in ("verdict", "verdict_cn", "reasons", "counter_evidence",
                "inputs", "thresholds", "missing", "computed_at"):
        assert key in r, key
    assert r["thresholds"] == TH


def test_computed_at_iso_with_timezone_aware_now():
    now = datetime(2026, 10, 19, 10, 0, 0, tzinfo=TZ8)
    r = admission.evaluate_admission("risk_on", 3, 70, 3, False, False,
                                     now=now)
    assert r["computed_at"] == "2026-10-19T10:00:00+08:00"


def test_computed_at_naive_now_gets_local_tz():
    r = admission.evaluate_admission("risk_on", 3, 70, 3, False, False,
                                     now=datetime(2026, 10, 19, 10, 0, 0))
    assert re.search(r"[+-]\d{2}:\d{2}$", r["computed_at"])


def test_computed_at_default_has_timezone():
    r = admission.evaluate_admission("risk_on", 3, 70, 3, False, False)
    assert re.search(r"[+-]\d{2}:\d{2}$", r["computed_at"])


# ── F1 引擎：今日真值回归（snapshot_20261016 实况）──────────────────
def test_today_truth_20261016_closed():
    """20261016 实况：risk_off · 三门 0/3 · overall=-2 · prob=44 · 影子闸
    → 判定 = 关门（方案 §2.2 第一个真值）。"""
    r = admission.evaluate_admission(
        regime="risk_off", overall=-2, prob=44, gates_passed=0,
        shadow_block=True, hard_block=False,
        now=datetime(2026, 10, 19, 10, 0, 0, tzinfo=TZ8))
    assert r["verdict"] == "closed"
    assert r["verdict_cn"] == "关门"
    assert any("risk_off" in x for x in r["reasons"])
    assert r["inputs"]["shadow_block"] is True


# ══ F2 端点：采集 + 降级（输入 helper 打桩，零真实目录依赖）════════
def _stub(monkeypatch, regime="risk_off", overall=-2, prob=44,
          gates_passed=0, up_ratio=0.608, trade_date="20261016",
          snapshot=True, shadow_block=True, hard_block=False,
          premarket_ok=True, risk_ok=True):
    monkeypatch.setattr(api, "_admission_regime_inputs", lambda: {
        "regime": regime, "gates_passed": gates_passed,
        "up_ratio": up_ratio, "trade_date": trade_date,
        "snapshot": snapshot})
    monkeypatch.setattr(api, "_admission_premarket_inputs", lambda: {
        "overall": overall if premarket_ok else None,
        "prob": prob if premarket_ok else None})
    monkeypatch.setattr(api, "_admission_risk_gate_inputs", lambda: {
        "shadow_block": shadow_block if risk_ok else None,
        "hard_block": hard_block if risk_ok else None,
        "score": 0.2246 if risk_ok else None,
        "reason": "stub" if risk_ok else None})


def test_endpoint_today_truth_closed(monkeypatch):
    _stub(monkeypatch)  # 默认即 20261016 实况
    p = api.admission_payload()
    assert p["ok"] is True and "ts" in p
    a = p["admission"]
    assert a["verdict"] == "closed"
    assert a["verdict_cn"] == "关门"
    assert a["inputs"] == {"regime": "risk_off", "overall": -2, "prob": 44,
                           "gates_passed": 0, "shadow_block": True,
                           "hard_block": False}
    assert a["sources"] == {"regime_snapshot": "ok", "premarket": "ok",
                            "risk_gate": "ok"}
    assert a["source_missing"] == []
    assert a["trade_date"] == "20261016"
    assert re.search(r"[+-]\d{2}:\d{2}$", a["computed_at"])
    # 反证同屏：risk_off 收缩判定 vs 广度 60.8% 偏多
    assert any("广度" in x and "60.8%" in x for x in a["counter_evidence"])
    assert a["elapsed_ms"] >= 0


def test_endpoint_missing_snapshot_unknown(monkeypatch):
    _stub(monkeypatch, regime=None, snapshot=False, gates_passed=None,
          shadow_block=False, hard_block=False, trade_date=None)
    a = api.admission_payload()["admission"]
    assert a["verdict"] == "unknown"
    assert "regime_snapshot" in a["source_missing"]
    assert a["sources"]["regime_snapshot"] == "missing"
    assert a["trade_date"] is None


def test_endpoint_missing_premarket_still_closed_not_fail_open(monkeypatch):
    """premarket 缺源但 regime=risk_off → 关门否决优先，缺源如实列出。"""
    _stub(monkeypatch, premarket_ok=False)
    a = api.admission_payload()["admission"]
    assert a["verdict"] == "closed"
    assert "premarket" in a["source_missing"]
    assert a["sources"]["premarket"] == "missing"


def test_endpoint_missing_premarket_neutral_unknown(monkeypatch):
    """neutral 且 premarket 缺源 → unknown（绝不按宽门放行）。"""
    _stub(monkeypatch, regime="neutral", overall=None, prob=None,
          gates_passed=3, shadow_block=False, hard_block=False)
    a = api.admission_payload()["admission"]
    assert a["verdict"] == "unknown"
    assert "premarket" in a["source_missing"]


def test_endpoint_missing_risk_gate_neutral_unknown(monkeypatch):
    """neutral 全绿但风控闸不可达 → unknown（禁 fail-open 假宽门）。"""
    _stub(monkeypatch, regime="neutral", overall=1, prob=60, gates_passed=3,
          shadow_block=None, hard_block=None, risk_ok=False)
    a = api.admission_payload()["admission"]
    assert a["verdict"] == "unknown"
    assert "risk_gate" in a["source_missing"]


def test_endpoint_wide_path(monkeypatch):
    _stub(monkeypatch, regime="risk_on", overall=3, prob=70, gates_passed=3,
          shadow_block=False, hard_block=False)
    a = api.admission_payload()["admission"]
    assert a["verdict"] == "wide"
    assert a["counter_evidence"] == []  # 广度反证只对 closed/narrow 叠加


# ══ F2 端点：真实采集器（tmp 目录，不绑真实路径）═══════════════════
SNAPSHOT_20261016 = {
    "breadth": {"amount_total_yi": 15690.6, "available": True,
                "down_count": 2128, "trade_date": "20261016",
                "up_count": 3301, "up_ratio": 0.608},
    "gates": {"dif_positive": False, "ma60_above": False,
              "slope_up": False},
    "index": {"available": True, "chg_pct": 0.24, "close": 3716.2842,
              "code": "000001.SH", "ma60": 3882.2469,
              "macd_dif": -46.8847, "name": "上证指数",
              "trade_date": "20261016"},
    "regime": "risk_off",
    "risk_off_reasons": ["close_below_ma60_2d(streak=11)",
                         "ma60_slope_down", "dif_and_dea_both_negative"],
    "trade_date": "20261016",
    "version": "regime_center-1.0.0",
}


def test_collector_premarket_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "ADMISSION_PREMARKET_PATH",
                        str(tmp_path / "nope.json"))
    assert api._admission_premarket_inputs() == {"overall": None,
                                                 "prob": None}


def test_collector_premarket_real_shape(tmp_path, monkeypatch):
    f = tmp_path / "premarket_latest.json"
    f.write_text(json.dumps({"ts": "2026-10-14 18:13", "overall": -2,
                             "prob": 44,
                             "verdict": "中性（按个股信号执行）"}),
                 encoding="utf-8")
    monkeypatch.setattr(api, "ADMISSION_PREMARKET_PATH", str(f))
    assert api._admission_premarket_inputs() == {"overall": -2, "prob": 44}


def test_collector_regime_snapshot(tmp_path, monkeypatch):
    d = tmp_path / "regime_center"
    d.mkdir()
    (d / "snapshot_20261016.json").write_text(
        json.dumps(SNAPSHOT_20261016, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(api, "REGIME_DIR", str(d))
    out = api._admission_regime_inputs()
    assert out["regime"] == "risk_off"
    assert out["gates_passed"] == 0          # 三门全关
    assert out["up_ratio"] == 0.608
    assert out["trade_date"] == "20261016"
    assert out["snapshot"] is True


def test_collector_regime_dir_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "REGIME_DIR", str(tmp_path))
    out = api._admission_regime_inputs()
    assert out == {"regime": None, "gates_passed": None, "up_ratio": None,
                   "trade_date": None, "snapshot": False}


def test_collector_risk_gate_action_mapping():
    """action 枚举映射：shadow_block→(True,False)；block→(False,True)；
    pass→(False,False)。真源 risk_bridge（实探 20261016=shadow_block）。"""
    def _ctx(action, available=True):
        class _M:
            @staticmethod
            def risk_context():
                return {"available": available,
                        "market_gate": {"action": action,
                                        "score": 0.2246, "reason": "r"}}
        return _M

    import strategy_layer.risk_bridge as rb
    orig = rb.risk_context
    try:
        rb.risk_context = _ctx("shadow_block").risk_context
        out = api._admission_risk_gate_inputs()
        assert (out["shadow_block"], out["hard_block"]) == (True, False)
        rb.risk_context = _ctx("block").risk_context
        out = api._admission_risk_gate_inputs()
        assert (out["shadow_block"], out["hard_block"]) == (False, True)
        rb.risk_context = _ctx("pass").risk_context
        out = api._admission_risk_gate_inputs()
        assert (out["shadow_block"], out["hard_block"]) == (False, False)
        rb.risk_context = _ctx("x", available=False).risk_context
        out = api._admission_risk_gate_inputs()
        assert out["shadow_block"] is None and out["hard_block"] is None
    finally:
        rb.risk_context = orig


# ── F2 端点：HTTP 路由冒烟（register_routes 包装层）─────────────────
def test_route_registered_via_http_handler(tmp_path, monkeypatch):
    """/api/admission 走 do_GET 分发 + _send_json（CORS/no-store 头）。"""
    _stub(monkeypatch)

    class FakeHandler:
        path = "/api/admission"

        def __init__(self):
            self.wfile = io.BytesIO()
            self.headers = {}

        def send_response(self, code):
            self.code = code

        def send_header(self, k, v):
            self.headers[k] = v

        def end_headers(self):
            pass

    api.register_routes(FakeHandler)
    h = FakeHandler()
    FakeHandler.do_GET(h)
    body = json.loads(h.wfile.getvalue().decode("utf-8"))
    assert h.code == 200
    assert body["ok"] is True
    assert body["admission"]["verdict"] == "closed"
    assert h.headers["Access-Control-Allow-Origin"] == "*"
    assert h.headers["Content-Type"].startswith("application/json")
