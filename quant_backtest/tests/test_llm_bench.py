# -*- coding: utf-8 -*-
"""rate-opt 工位单测（2026-10-18）：LLM 速率旋钮三态 + bench 纯逻辑。

覆盖：
  ① XQ_LLM_NUM_CTX / XQ_LLM_NUM_PREDICT 三态：未设置=默认 / 设置=覆盖 / 非法=回落+stderr
  ② bench 抽样（均匀百分位取样）与 agreement 计算——假 client，不真调 LLM
跑法：pytest quant_backtest/tests/test_llm_bench.py -q
"""
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import xq_classify as xq
import llm_bench as bench


# ── ① 旋钮三态 ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    monkeypatch.delenv("XQ_LLM_NUM_CTX", raising=False)
    monkeypatch.delenv("XQ_LLM_NUM_PREDICT", raising=False)


def test_knob_unset_is_status_quo():
    # 红线：未设置时与现状完全一致（2048/256）
    assert xq.build_llm_options() == {"temperature": 0.1, "num_ctx": 2048,
                                      "num_predict": 256}


def test_knob_set_overrides(monkeypatch):
    monkeypatch.setenv("XQ_LLM_NUM_CTX", "1024")
    monkeypatch.setenv("XQ_LLM_NUM_PREDICT", "128")
    opts = xq.build_llm_options()
    assert opts["num_ctx"] == 1024 and opts["num_predict"] == 128
    assert opts["temperature"] == 0.1


def test_knob_invalid_falls_back_with_stderr(monkeypatch):
    monkeypatch.setenv("XQ_LLM_NUM_CTX", "abc")
    monkeypatch.setenv("XQ_LLM_NUM_PREDICT", "0")
    err = io.StringIO()
    with redirect_stderr(err):
        opts = xq.build_llm_options()
    assert opts["num_ctx"] == 2048 and opts["num_predict"] == 256
    out = err.getvalue()
    assert "XQ_LLM_NUM_CTX" in out and "WARN" in out
    assert "XQ_LLM_NUM_PREDICT" in out


def test_knob_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("XQ_LLM_NUM_CTX", "1024")
    assert xq.build_llm_options(num_ctx=4096)["num_ctx"] == 4096
    # 显式传 None = 未指定，交回环境变量
    assert xq.build_llm_options(num_ctx=None)["num_ctx"] == 1024


def test_knob_negative_and_float_invalid(monkeypatch):
    monkeypatch.setenv("XQ_LLM_NUM_CTX", "-5")
    monkeypatch.setenv("XQ_LLM_NUM_PREDICT", "12.5")
    err = io.StringIO()
    with redirect_stderr(err):
        opts = xq.build_llm_options()
    assert opts["num_ctx"] == 2048 and opts["num_predict"] == 256


# ── ② bench 纯逻辑（假 client） ─────────────────────────────

def test_pick_evenly_covers_short_medium_long():
    items = sorted(range(1, 101))          # 100 条按长度排好
    picked = bench.pick_evenly(items, 8)
    assert len(picked) == 8
    assert len(set(picked)) == 8           # 无重复
    assert picked[0] == min(items) and picked[-1] == max(items)  # 短/长两端覆盖


def test_pick_evenly_fewer_than_n():
    assert bench.pick_evenly([3, 1, 2], 8) == [1, 2, 3]  # 不够 8 条时全取（排序后）


def _fake_verdict(content):
    return {"event_type": content, "sentiment": "neutral",
            "content_type": "community", "parse_ok": 1}


def test_run_bench_fake_client_call_counts():
    # 2 条样本 × 2 配置 = 4 次调用，假 client 不真调 LLM
    calls = []

    def fake_sender(url, payload, timeout):
        calls.append(payload)
        ctx = payload["options"]["num_ctx"]
        return {"message": {"content": "行情观点" if ctx == 2048 else "个股研判"},
                "eval_duration": 1_000_000_000, "eval_count": 100}

    samples = [{"status_id": "s1", "body": "abc", "chars": 3},
               {"status_id": "s2", "body": "d" * 50, "chars": 50}]
    records = bench.run_bench(samples, fake_sender)
    assert len(calls) == 4 and len(records) == 4
    assert sorted(r["config"] for r in records) == ["A", "A", "B", "B"]
    # 配置A=现状默认，配置B=1024/128
    a = next(r for r in records if r["config"] == "A")
    b = next(r for r in records if r["config"] == "B")
    assert a["options"]["num_ctx"] == 2048 and a["options"]["num_predict"] == 256
    assert b["options"]["num_ctx"] == 1024 and b["options"]["num_predict"] == 128
    # eval 口径换算（ns→s）
    assert a["eval_duration_s"] == 1.0 and a["eval_count"] == 100


def test_compute_agreement_mixed():
    recs = [
        {"sample_id": 0, "config": "A", "verdict": _fake_verdict("行情观点")},
        {"sample_id": 0, "config": "B", "verdict": _fake_verdict("行情观点")},  # 一致
        {"sample_id": 1, "config": "A", "verdict": _fake_verdict("行情观点")},
        {"sample_id": 1, "config": "B", "verdict": _fake_verdict("个股研判")},  # 分歧
        {"sample_id": 2, "config": "A", "verdict": {"event_type": None, "sentiment": None,
                                                    "content_type": None, "parse_ok": 0}},
        {"sample_id": 2, "config": "B", "verdict": _fake_verdict("闲聊杂谈")},  # A 解析失败不算一致
    ]
    agg = bench.compute_agreement(recs)
    assert agg["n"] == 3 and agg["agree"] == 1
    assert agg["agreement_pct"] == 33.3


def test_compute_agreement_perfect():
    recs = []
    for i in range(4):
        for tag in ("A", "B"):
            recs.append({"sample_id": i, "config": tag,
                         "verdict": _fake_verdict("基金理财")})
    agg = bench.compute_agreement(recs)
    assert agg == {"n": 4, "agree": 4, "agreement_pct": 100.0}


def test_bench_one_records_error_without_raise():
    def broken_sender(url, payload, timeout):
        raise TimeoutError("timed out")

    rec = bench.bench_one(broken_sender, "http://x", "m", "sys", "body",
                          {"num_ctx": 1024}, 240)
    assert rec["error"] is not None and "timed out" in rec["error"]
    assert rec.get("eval_count") is None


def test_parse_verdict_contract():
    good = bench.parse_verdict('{"event_type":"广告推广","content_type":"ad",'
                               '"sentiment":"neutral","confidence":0.9}')
    assert good["event_type"] == "广告推广" and good["parse_ok"] == 1
    bad = bench.parse_verdict("不是 JSON")
    assert bad["parse_ok"] == 0
