# -*- coding: utf-8 -*-
"""077w M2 后半：客户端侧 LLM 成本计量测试。
monkeypatch llm_cost 模块级 COST_DIR 隔离，不写真库。"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # k_stock 包根
sys.path.insert(0, str(HERE.parent / "kstock"))

from kstock.llm import LLMClient              # noqa: E402
from k_stock.paper import llm_cost            # noqa: E402


def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_cost, "COST_DIR", tmp_path / "llm_cost")
    return tmp_path


def test_meter_records_openai_usage(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    c = LLMClient(mock=True, account="orchestrator")
    d = {"usage": {"prompt_tokens": 1000, "completion_tokens": 500}}
    c._meter(c.primary, "glm-4-flash", d)      # glm primary → zhipu/glm-4-flash ¥1/M
    f = (tmp_path / "llm_cost").glob("*.jsonl")
    lines = [json.loads(x) for x in next(iter(f)).read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    r = lines[0]
    assert r["account"] == "orchestrator" and r["binding"] == "zhipu/glm-4-flash"
    assert r["prompt_tokens"] == 1000 and r["completion_tokens"] == 500
    assert r["cost"] == round(1500 * 1.0 / 1_000_000, 4)


def test_meter_skips_when_no_account(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    c = LLMClient(mock=True)                   # account=None → 不记账
    c._meter(c.primary, "glm-4-flash", {"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    assert not (tmp_path / "llm_cost").exists()


def test_meter_skips_when_usage_missing(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    c = LLMClient(mock=True, account="orchestrator")
    c._meter(c.primary, "glm-4-flash", {})     # 无 usage → 静默跳过
    assert not any((tmp_path / "llm_cost").glob("*.jsonl")) if (tmp_path / "llm_cost").exists() else True


def test_meter_ollama_binding_and_meter_failure_silent(tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    c = LLMClient(mock=True, account="orchestrator", model_fallback="qwen3.5:9b")
    c._meter(c.fallback, "qwen3.5:9b", {"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    f = next(iter((tmp_path / "llm_cost").glob("*.jsonl")))
    r = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    assert r["binding"] == "ollama/qwen3.5:9b" and r["cost"] == 0.0
    # 异常静默：坏 usage 类型不抛
    c._meter(c.primary, "glm-4-flash", {"usage": {"prompt_tokens": "x", "completion_tokens": "y"}})
