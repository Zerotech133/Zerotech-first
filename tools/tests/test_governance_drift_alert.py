# -*- coding: utf-8 -*-
"""知识治理漂移告警（W0-4）契约测试 —— 离线，不真发飞书。

覆盖：
  1. 阈值触发：核验率降>5pp / 重复率升>5pp / 多源降>10 / 冲突>5 → 四条告警；
  2. 未触发：健康指标 → 无告警、告警文本为「正常」；
  3. dry-run：默认不发飞书（send 未调用），但有漂移时告警文本含 code；
  4. --send：注入 mock send_fn → 记录调用/文本；发送异常降级不抛出；
  5. 实跑：读最新治理报告/基线 → 输出 governance_drift_alert_<date>.json schema。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "kb_corpus" / "governance_drift_alert.py"
GOV_LATEST = sorted((REPO / "research" / "reports").glob("knowledge_governance_*.json"))
BASE_LATEST = sorted((REPO / "research" / "reports").glob("governance_baseline_*.json"))


def _load():
    spec = importlib.util.spec_from_file_location("governance_drift_alert_under_test", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load()


def gov_fixture(metrics=None, delta=None, baseline=None):
    metrics = metrics or {"units": 3800, "verified": 3700, "verified_pct": 97.4, "unverifiable": 100,
                          "exact_dup_units": 300, "near_dup_pairs": 3, "dup_ratio_pct": 13.2,
                          "multi_source_concepts": 2, "conflicts": 9}
    baseline = baseline or {"units": 3800, "verified": 3700, "verified_pct": 100.0,
                            "unverifiable": 0, "exact_dup_units": 218, "near_dup_pairs": 1,
                            "dup_ratio_pct": 5.7, "multi_source_concepts": 20, "conflicts": 2}
    delta = delta if delta is not None else {"verified_pct": -6.0, "dup_ratio_pct": 7.5,
                                             "multi_source_concepts": -18, "conflicts": 7}
    return {"generated_at": "2026-11-03T06:00:00+08:00", "project": "test",
            "metrics": metrics, "drift": {"baseline": baseline, "delta": delta}}


def test_evaluate_drift_triggers_all():
    current = gov_fixture()["metrics"]
    baseline = gov_fixture()["drift"]["baseline"]
    delta = {"verified_pct": -6.0, "dup_ratio_pct": 9.0,
             "multi_source_concepts": -12, "conflicts": 8}
    drifts = m.evaluate_drift(current, baseline, delta)
    codes = {d["code"] for d in drifts}
    assert codes == {"VERIFIED_DROP", "DUP_RISE", "MULTI_SOURCE_DROP", "CONFLICT_HIGH"}
    assert all(d["level"] == "warn" for d in drifts)
    assert all("message" in d and "delta" in d for d in drifts)


def test_evaluate_drift_no_trigger():
    current = {"verified_pct": 100.0, "dup_ratio_pct": 5.7,
               "multi_source_concepts": 0, "conflicts": 0}
    baseline = {"verified_pct": 100.0, "dup_ratio_pct": 5.7,
                "multi_source_concepts": 0, "conflicts": 0}
    delta = {"verified_pct": 0.0, "dup_ratio_pct": 0.0,
             "multi_source_concepts": 0, "conflicts": 0}
    assert m.evaluate_drift(current, baseline, delta) == []
    text = m.format_alert_text("2026-11-03", [], current, baseline)
    assert "正常" in text and "告警" not in text


def test_dry_run_does_not_send():
    calls = []

    def spy(text):
        calls.append(text)
        return {"sent": True, "error": None}

    # 有漂移但 dry-run（send=False）→ 不调用 send，文本含触发 code
    payload = m.build_payload(gov_fixture(), None, send=False, send_fn=spy,
                              report_path="x.json", baseline_path="y.json")
    assert payload["dry_run"] is True
    assert payload["send"] == {"attempted": False, "sent": False, "error": None}
    assert calls == []
    assert payload["alert_count"] == 4
    for code in ("VERIFIED_DROP", "DUP_RISE", "MULTI_SOURCE_DROP", "CONFLICT_HIGH"):
        assert code in payload["alert_text"]
    # 健康指标 dry-run → 文本正常
    healthy = gov_fixture(metrics={"verified_pct": 100.0, "dup_ratio_pct": 5.7,
                                   "multi_source_concepts": 0, "conflicts": 0},
                          delta={"verified_pct": 0.0, "dup_ratio_pct": 0.0,
                                 "multi_source_concepts": 0, "conflicts": 0},
                          baseline={"verified_pct": 100.0, "dup_ratio_pct": 5.7,
                                    "multi_source_concepts": 0, "conflicts": 0})
    payload2 = m.build_payload(healthy, None, send=False, send_fn=spy)
    assert payload2["alert_count"] == 0 and "正常" in payload2["alert_text"]
    assert calls == []


def test_send_mock_records_and_failure_degrades():
    calls = []

    def spy(text):
        calls.append(text)
        return {"sent": True, "error": None}

    payload = m.build_payload(gov_fixture(), None, send=True, send_fn=spy)
    assert payload["send"]["attempted"] is True
    assert payload["send"]["sent"] is True
    assert len(calls) == 1 and "VERIFIED_DROP" in calls[0]

    def boom(text):
        raise RuntimeError("send_feishu 不可用")

    payload2 = m.build_payload(gov_fixture(), None, send=True, send_fn=boom)
    assert payload2["send"]["attempted"] is True
    assert payload2["send"]["sent"] is False
    assert "send_feishu 不可用" in str(payload2["send"]["error"])


def test_run_real_files_schema(tmp_path):
    assert GOV_LATEST, "缺少 knowledge_governance 报告"
    assert BASE_LATEST, "缺少 governance_baseline 报告"
    payload = m.run(out_dir=tmp_path)
    assert payload["kind"] == "governance_drift_alert"
    assert payload["dry_run"] is True
    assert payload["date"]
    assert set(payload["thresholds"]) == {"verified_drop_pp", "dup_rise_pp",
                                          "multi_source_drop", "conflicts_max"}
    assert payload["thresholds"]["verified_drop_pp"] == 5.0
    assert payload["thresholds"]["dup_rise_pp"] == 5.0
    assert payload["thresholds"]["multi_source_drop"] == 10
    assert payload["thresholds"]["conflicts_max"] == 5
    assert payload["out_json"].endswith(f"governance_drift_alert_{payload['date'].replace('-', '')}.json")
    out = tmp_path / f"governance_drift_alert_{payload['date'].replace('-', '')}.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    for key in ("current", "baseline", "delta", "drifts", "alert_text", "send", "sources"):
        assert key in data, key
    assert data["current"].get("verified_pct") is not None
