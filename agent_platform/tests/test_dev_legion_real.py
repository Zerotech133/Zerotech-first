# -*- coding: utf-8 -*-
"""v1.1 真实量化聚合测试（LV-02/03/04）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

NOW = time.mktime(time.strptime("2026-10-28 23:00:00", "%Y-%m-%d %H:%M:%S"))
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import dev_legion_quant_real as Q  # noqa: E402
import dev_legion_risk_real as R  # noqa: E402
import api_dev_legion as A  # noqa: E402


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _mkroot(tmp_path):
    root = tmp_path
    q = root / "agent_platform/data/strategy/strategy_runs.jsonl"
    _write_jsonl(q, [
        {"strategy_id": "s1", "symbol": "600519", "rationale": "秘密理由",
         "recommendation": "buy", "recorded_at": "2026-10-28T10:00:00", "run_id": "r1"},
        {"strategy_id": "s1", "symbol": "000001", "rationale": "秘密理由",
         "recommendation": "sell", "recorded_at": "2026-10-28T11:00:00", "run_id": "r2"},
        {"strategy_id": "s2", "symbol": "600000", "rationale": "秘密理由",
         "recommendation": "hold", "recorded_at": "2026-10-28T12:00:00", "run_id": "r3"},
        {"strategy_id": "s1", "symbol": "600519", "rationale": "秘密理由",
         "recommendation": "buy", "recorded_at": "2026-10-27T10:00:00", "run_id": "r4"},
    ])
    _write_jsonl(root / "agent_platform/data/shadow/shadow_ledger.jsonl", [
        {"date": "2026-10-28", "symbol": "600519", "name": "茅台",
         "price_at_decision": 1255.0, "target_range": [1240, 1280],
         "status": "done", "degraded": False, "latency_s": 10.0},
        {"date": "2026-10-28", "symbol": "000001", "name": "平安",
         "price_at_decision": 12.0, "target_range": [11, 13],
         "status": "done", "degraded": True, "latency_s": 20.0},
        {"date": "2026-10-27", "status": "failed", "degraded": True, "latency_s": 30.0},
    ])
    _write_jsonl(root / "agent_platform/data/academy/backtests.jsonl", [
        {"created": "2026-10-28T09:00", "status": "done", "go_no_go": "go",
         "strategy": "LCS-1", "symbol": "600337", "owner": "BACK1"},
        {"created": "2026-10-28T10:00", "status": "queued", "go_no_go": "pending",
         "strategy": "LCS-1", "symbol": "600337", "owner": "BACK1"},
        {"created": "2026-10-27T09:00", "status": "done", "go_no_go": "no_go",
         "strategy": "LCS-2", "symbol": "000001", "owner": "BACK1"},
    ])
    return root


def test_quant_real_aggregate_only(tmp_path):
    root = _mkroot(tmp_path)
    d = Q.build_quant_real(root, now=NOW)
    assert d["metrics"]["runs_window"] == 4
    assert d["metrics"]["last_run_at"] == "2026-10-28T12:00:00"
    blob = json.dumps(d, ensure_ascii=False)
    assert "600519" not in blob and "秘密理由" not in blob and "symbol" not in blob
    # 分桶 n<5 → 掩码
    assert d["metrics"]["stance"]["buy"] == "<5"
    assert d["metrics"]["strategies"] == "<5"


def test_risk_real_aggregate_only(tmp_path):
    root = _mkroot(tmp_path)
    d = R.build_risk_real(root)
    m = d["metrics"]
    assert m["shadow"]["rows"] == 3
    assert m["shadow"]["last_date"] == "2026-10-28"
    assert m["shadow"]["degraded_rate"] == 0.6667
    assert m["shadow"]["latency_p50_s"] == 20.0
    assert m["backtests"]["total"] == 3
    assert m["backtests"]["go_no_go"]["go"] == "<5"
    blob = json.dumps(d, ensure_ascii=False)
    for bad in ("600519", "茅台", "1255", "target_range", "BACK1"):
        assert bad not in blob


def test_missing_sources_none(tmp_path):
    assert Q.build_quant_real(tmp_path) is None
    assert R.build_risk_real(tmp_path) is None


def test_api_real_optional_and_compatible():
    p = A.build_payload(A.ROOT)
    # v1 旧字段不变
    assert p["schema"] == "legion_screen_contract/v1"
    for k in ("seats", "quant_cards", "risk_cards", "metrics", "redlines"):
        assert k in p
    src = A.ROOT / "agent_platform/data/strategy/strategy_runs.jsonl"
    if not src.is_file():
        pytest.skip("无真源（worktree）")
    assert "v1.1_quant_real" in p.get("extensions", [])
    assert p["quant_cards"][0].get("real")
    blob = json.dumps(p, ensure_ascii=False)
    for bad in ("symbol", "rationale", "target_range", "price_at_decision"):
        assert ('"%s"' % bad) not in blob


def test_page_has_real_renderer():
    html = (ROOT / "portal" / "dev_legion.html").read_text(encoding="utf-8")
    assert "realText" in html and "真实 ·" in html


# 日报 purpose 降级（原 test_seat_chat_fallback_purpose.py，归入 gate 覆盖文件）
sys.path.insert(0, str(ROOT / "agent_platform"))
from legion_command import serve as srv  # noqa: E402

def test_report_purpose_allows_non_glm_fallback():
    calls = []

    def chat(binding, system, user):
        calls.append(binding)
        if binding == "deepseek/deepseek-v4-pro":
            return {"ok": False, "error": {"message": "finish=length"}}
        return {"ok": True, "data": {"text": "glm fallback", "binding_resolved": binding}}

    env, attempts = srv._seat_chat(chat, "deepseek/deepseek-v4-pro", ["ollama/qwen3.5:9b"],
                                   "s", "u", allow_non_glm_fallback=True)
    assert env["ok"] and env["degraded"] is True
    assert env["fallback_from"] == "deepseek/deepseek-v4-pro"
    assert attempts[0] == "deepseek/deepseek-v4-pro" and "glm/glm-5.3" in attempts


def test_meeting_default_does_not_fallback_non_glm():
    calls = []

    def chat(binding, system, user):
        calls.append(binding)
        return {"ok": False, "error": {"message": "down"}}

    env, attempts = srv._seat_chat(chat, "deepseek/deepseek-v4-pro", ["ollama/qwen3.5:9b"], "s", "u")
    assert env["ok"] is False and attempts == ["deepseek/deepseek-v4-pro"]


def test_five_role_persona():
    sys.path.insert(0, str(ROOT / "agent_platform"))
    from legion_command import serve as srv
    cases = {
        "A0": "幕府参谋长", "AI1": "军师", "ZHAOYUN": "治理官",
        "GUANYU": "整治官", "HUANGZHONG": "验收官",
        "DATA1": "数据资源主官（兰台令）",
    }
    for aid, pos in cases.items():
        text = srv.five_role_persona(aid)
        assert text and pos in text and aid in text
    assert srv.five_role_persona("DEV1") == ""
