# -*- coding: utf-8 -*-
"""8091 问答 HTTP 限流/压测：mock 契约测试（不真打 8091、不调 LLM）。

覆盖：
  1. percentile 延迟分位；
  2. summarize_phase 统计（200/429/5xx、错误率、成功延迟分位）；
  3. build_conclusion 判定（go / conditional 限流未触发 / no-go 5xx 或 multi 失败）；
  4. 阶段函数（serial/concurrent/multi）与全流程 schema + JSON/MD 落盘 + CLI（mock fetch）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LT_PATH = REPO / "tools" / "kb_corpus" / "qa_http_loadtest.py"


def _load():
    spec = importlib.util.spec_from_file_location("qa_http_loadtest_under_test", LT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lt = _load()

POOL = ["问题A", "问题B", "问题C", "问题D"]


def mock_env(ok=True, rate_meta=None):
    return {"ok": ok, "error": None if ok else {"code": "RATE_LIMITED"},
            "data": {"answer": "x"}, "meta": {"cache_hit": False,
                                              "rate_limit": rate_meta or {}}}


def fetch_factory(script):
    """script: list of (status, ok) 按请求顺序消费；超出复用最后一个。"""
    box = {"i": 0}

    def fetch(url, timeout=30.0):
        idx = min(box["i"], len(script) - 1)
        box["i"] += 1
        status, ok = script[idx]
        return status, mock_env(ok), 0.05, {"limit": 60, "used": idx + 1}

    return fetch


def make_result(status, ok=True, elapsed=0.05):
    return {"status": status, "ok": ok, "error_code": None if ok else "RATE_LIMITED",
            "elapsed_seconds": elapsed, "rate_limit": {}, "cache_hit": False}


# ---------------------------------------------------------------------------
# 1. 百分位
# ---------------------------------------------------------------------------
def test_percentile():
    assert lt.percentile([], 50) == 0.0
    assert lt.percentile([1.0], 99) == 1.0
    assert lt.percentile([1, 2, 3, 4], 50) == 2.5
    assert lt.percentile([1, 2, 3, 4], 95) == 3.85
    assert lt.percentile([1, 2, 3, 4], 100) == 4.0


# ---------------------------------------------------------------------------
# 2. 阶段统计
# ---------------------------------------------------------------------------
def test_summarize_phase():
    results = [make_result(200) for _ in range(18)] + \
              [make_result(429, ok=False) for _ in range(2)] + [make_result(503, ok=False)]
    s = lt.summarize_phase("test", results)
    assert s["total"] == 21 and s["http_200"] == 18
    assert s["rate_limited_429"] == 2
    assert s["server_5xx"] == 1
    assert s["errors"] == 3
    assert s["error_rate"] == round(3 / 21, 4)
    assert s["latency_success"]["p50"] > 0
    assert s["status_counts"]["200"] == 18 and s["status_counts"]["429"] == 2


# ---------------------------------------------------------------------------
# 3. 结论判定
# ---------------------------------------------------------------------------
def test_build_conclusion_cases():
    def phase(name, total, ok=None, e429=0, e5xx=0):
        ok = total if ok is None else ok
        return {"name": name, "total": total, "http_200": ok,
                "rate_limited_429": e429, "server_5xx": e5xx,
                "errors": total - ok, "error_rate": round((total - ok) / total, 4),
                "latency_success": {"p50": 0.1, "p95": 0.2, "p99": 0.3, "avg": 0.15, "max": 0.4}}

    good = {"stability_50": phase("s", 50), "retrieval_serial_62": phase("serial", 62, 58, 4),
            "retrieval_concurrent": phase("conc", 200, 170, 30),
            "multi_concurrent": {**phase("multi", 6), "llm_calls": 6}}
    c = lt.build_conclusion(good)
    assert c["can_go_live"] == "go"
    assert c["limit_verified"] is True and c["stability_ok"] is True
    assert c["risks"]

    # 限流未触发 → conditional
    no_limit = {**good, "retrieval_serial_62": phase("serial", 62, 62)}
    assert lt.build_conclusion(no_limit)["can_go_live"] == "conditional"

    # 稳定性 5xx → no-go
    bad_stab = {**good, "stability_50": phase("s", 50, 49, 0, 1)}
    assert lt.build_conclusion(bad_stab)["can_go_live"] == "no-go"

    # multi 未全部成功 → no-go
    bad_multi = {**good, "multi_concurrent": {**phase("multi", 6, 4, 2), "llm_calls": 4}}
    assert lt.build_conclusion(bad_multi)["can_go_live"] == "no-go"


# ---------------------------------------------------------------------------
# 4. 阶段函数 + 全流程 + 落盘 + CLI（mock fetch）
# ---------------------------------------------------------------------------
def test_phase_functions_with_mock():
    serial = lt.phase_retrieval_serial(POOL, 5, fetch_fn=fetch_factory([(200, True)] * 5))
    assert serial["total"] == 5 and serial["http_200"] == 5
    conc = lt.phase_retrieval_concurrent(POOL, total=20, workers=5,
                                         fetch_fn=fetch_factory([(200, True)] * 20))
    assert conc["total"] == 20 and conc["http_200"] == 20
    multi = lt.phase_multi_concurrent(POOL, total=6, workers=2,
                                      fetch_fn=fetch_factory([(200, True)] * 6))
    assert multi["total"] == 6 and multi["llm_calls"] == 6


def test_run_loadtest_schema_with_mock(tmp_path):
    script = [(200, True)] * 50 + [(200, True)] * 60 + [(429, False)] * 2 + \
             [(200, True)] * 180 + [(429, False)] * 20 + [(200, True)] * 6
    payload = lt.run_loadtest(wait_between_phases=False, fetch_fn=fetch_factory(script),
                              question_pool=POOL, base_url="http://127.0.0.1:8091")
    assert payload["kind"] == "qa_http_loadtest"
    phases = payload["phases"]
    assert phases["stability_50"]["total"] == 50
    assert phases["retrieval_serial_62"]["rate_limited_429"] == 2
    assert phases["retrieval_concurrent"]["total"] == 200
    assert phases["multi_concurrent"]["http_200"] == 6
    assert payload["conclusion"]["can_go_live"] == "go"
    jp, mp = lt.write_outputs(payload, tmp_path / "lt.json", tmp_path / "lt.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "阶段结果", "限流行为", "延迟分布", "调参/风险"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["concurrent_total"] == 200
    proc = subprocess.run([sys.executable, "-X", "utf8", str(LT_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
