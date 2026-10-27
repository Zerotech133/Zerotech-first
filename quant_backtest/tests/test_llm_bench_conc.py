# -*- coding: utf-8 -*-
"""conc-bench 工位单测（2026-10-18）：并发调度与汇总逻辑——假 sender，不真调 LLM。

覆盖：
  ① 并发调度：run_level 在 workers>1 时真并行（聚合墙钟 < 单条墙钟之和）
  ② 汇总口径：items_per_min / 聚合 tok/s / 单条墙钟统计 / 错误计数
  ③ /api/generate 响应字段提取（response 而非 message）与异常 fail-记数
  ④ 抽样与调用预算红线
跑法：pytest quant_backtest/tests/test_llm_bench_conc.py -q
"""
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import llm_bench_conc as conc


def _fake_resp(text='{"event_type":"行情观点","sentiment":"neutral",'
                   '"content_type":"news"}',
               eval_count=18, eval_duration_ns=1_000_000_000):
    return {"response": text, "done": True,
            "eval_count": eval_count, "eval_duration": eval_duration_ns,
            "prompt_eval_count": 300}


def _mk_sender(delay_s=0.2, fail_ids=()):
    """假 sender：按延迟模拟推理，fail_ids 中的 prompt 前缀触发异常。"""
    state = {"calls": 0}

    def sender(url, payload, timeout):
        state["calls"] += 1
        for fid in fail_ids:
            if payload.get("prompt", "").startswith(fid):
                raise RuntimeError("boom:%s" % fid)
        time.sleep(delay_s)
        return _fake_resp()

    sender.state = state
    return sender


SAMPLES = [{"status_id": str(i), "body": "帖子%d " % i * 3, "chars": 30 + i}
           for i in range(4)]


# ── ① 并发调度 ─────────────────────────────────────────────

def test_run_level_executes_all_samples():
    s = _mk_sender(delay_s=0.01)
    out = conc.run_level(SAMPLES, workers=2, sender=s, url="http://x/api/generate",
                         model="m", system="sys", options={}, timeout=5)
    assert s.state["calls"] == 4
    assert len(out["records"]) == 4
    assert out["workers"] == 2
    assert all(r["error"] is None for r in out["records"])
    assert out["calls_ok"] == 4 and out["errors"] == 0


def test_run_level_true_parallelism():
    # 4 条 × 0.3s：串行 ≈1.2s，2 并发 ≈0.6s；断言聚合墙钟明显小于单条之和
    s = _mk_sender(delay_s=0.3)
    out = conc.run_level(SAMPLES, workers=2, sender=s, url="u", model="m",
                         system="sys", options={}, timeout=10)
    walls_sum = sum(r["wall_s"] for r in out["records"])
    assert out["agg_wall_s"] < walls_sum * 0.8, \
        "聚合墙钟 %s 应显著小于单条墙钟之和 %s" % (out["agg_wall_s"], walls_sum)


# ── ② 汇总口径 ─────────────────────────────────────────────

def test_summarize_level_metrics():
    s = _mk_sender(delay_s=0.05, fail_ids=("帖子1",))
    out = conc.run_level(SAMPLES, workers=1, sender=s, url="u", model="m",
                         system="sys", options={}, timeout=5)
    sm = conc.summarize_level(out, 4.0)   # 假设聚合墙钟 4s
    assert sm["calls_total"] == 4 and sm["calls_ok"] == 3 and sm["errors"] == 1
    # 条/分钟 = 3 ok / 4s * 60 = 45
    assert sm["items_per_min"] == round(3 / 4.0 * 60, 2)
    # 聚合 eval tok/s = sum(eval_count)/sum(eval_duration) = 3*18/3.0 = 18.0
    assert sm["eval_tok_per_s"] == 18.0
    # 墙钟口径 tok/s = 54 tok / 4s = 13.5
    assert sm["wall_tok_per_s"] == round(3 * 18 / 4.0, 2)
    assert sm["item_wall_s"]["min"] is not None
    assert sm["item_wall_s"]["count"] == 3


def test_summarize_empty_level_returns_none_metrics():
    s = _mk_sender(delay_s=0.001, fail_ids=("帖子",))
    out = conc.run_level(SAMPLES, workers=1, sender=s, url="u", model="m",
                         system="sys", options={}, timeout=5)
    sm = conc.summarize_level(out, 1.0)
    assert sm["calls_ok"] == 0 and sm["items_per_min"] == 0.0
    assert sm["eval_tok_per_s"] is None and sm["item_wall_s"] is None


# ── ③ /api/generate 响应提取 ───────────────────────────────

def test_call_one_parses_generate_response():
    s = _mk_sender(delay_s=0)
    rec = conc.call_one(s, "u", "m", "sys", "正文", {}, timeout=5)
    assert rec["error"] is None
    assert rec["eval_count"] == 18
    assert rec["eval_duration_s"] == 1.0
    assert rec["verdict"]["parse_ok"] == 1
    assert rec["verdict"]["event_type"] == "行情观点"
    assert rec["wall_s"] >= 0


def test_call_one_records_error_without_raise():
    def bad_sender(url, payload, timeout):
        raise TimeoutError("timed out")
    rec = conc.call_one(bad_sender, "u", "m", "sys", "正文", {}, timeout=5)
    assert rec["error"] is not None and "timed out" in rec["error"]
    assert rec["wall_s"] >= 0


# ── ④ 抽样与预算红线 ───────────────────────────────────────

def test_pick_evenly_n4_short_mid_mix():
    pool = [{"chars": c} for c in [20, 50, 100, 150, 200, 250, 300, 360]]
    out = conc.pick_evenly(pool, 4, key=lambda p: p["chars"])
    assert len(out) == 4
    assert out[0]["chars"] == 20 and out[-1]["chars"] == 360   # 两端覆盖


def test_call_budget_within_redline():
    # 4 样本 × (1+2+4) = 28 ≤ 32 红线
    assert conc.SAMPLE_N == 4
    assert set(conc.CONC_LEVELS) == {1, 2, 4}
    assert conc.SAMPLE_N * (sum(conc.CONC_LEVELS)) <= conc.MAX_CALLS == 32


def test_verdict_parse_via_json_regex():
    v = conc.parse_verdict('前缀噪声 {"event_type":"个股研判","sentiment":"bullish",'
                           '"content_type":"tech"} 后缀')
    assert v["parse_ok"] == 1 and v["sentiment"] == "bullish"
