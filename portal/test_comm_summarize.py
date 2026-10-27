# -*- coding: utf-8 -*-
"""阚泽部出向智能摘要单测（K5.2）。

覆盖：正常生成、数字编造守卫、超长截断、模型失败降级模板、
开关关闭、缓存命中、输出过短降级。
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import comm_summarize as cs  # noqa: E402


class _Stub:
    def __init__(self, text, status="normal"):
        self.text = text
        self.status = status
        self.calls = 0

    def chat(self, system, user, model=None):
        self.calls += 1
        return self.text, self.status


class _Boom:
    def chat(self, system, user, model=None):
        raise RuntimeError("ollama down")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("COMM_SUMMARY_ENABLED", raising=False)
    import tempfile
    import os as _os

    monkeypatch.setenv(
        "COMM_FLAGS_PATH", _os.path.join(tempfile.mkdtemp(), "no_flags.json")
    )
    monkeypatch.setenv("COMM_SUMMARY_MAX_CHARS", "220")
    cs.clear_cache()
    yield
    cs.clear_cache()


ITEMS = [
    "沪深两市成交额 1.82 万亿，较昨日放量 12%",
    "北向资金净流入 43 亿",
    "涨停 58 家，跌停 3 家",
]


def test_01_normal_generation():
    llm = _Stub("今日两市成交 1.82 万亿，北向净流入 43 亿，涨停 58 家。")
    r = cs.humanize("收盘速览", ITEMS, llm=llm, use_cache=False)
    assert r["llm_ok"] is True
    assert r["tier"] == "fast"
    assert "1.82" in r["text"]


def test_02_number_fabrication_blocked():
    llm = _Stub("今日成交 9.99 万亿，涨停 58 家。")  # 9.99 输入里没有
    r = cs.humanize("收盘速览", ITEMS, llm=llm, use_cache=False)
    assert r["llm_ok"] is False
    assert r["error"] == "number_fabrication_guard"
    assert "9.99" not in r["text"], "编造数字不得进入卡片"


def test_03_llm_down_falls_back_template():
    r = cs.humanize("收盘速览", ITEMS, llm=_Boom(), use_cache=False)
    assert r["llm_ok"] is False
    assert r["tier"] == "template"
    assert "1.82" in r["text"], "模板兜底须保留原始事实"
    assert r["error"].startswith("RuntimeError")


def test_04_too_short_output_falls_back():
    llm = _Stub("好")
    r = cs.humanize("收盘速览", ITEMS, llm=llm, use_cache=False)
    assert r["llm_ok"] is False and r["error"] == "output_too_short"


def test_05_long_output_trimmed():
    long_txt = "市场今日表现分化，" * 40
    llm = _Stub(long_txt)
    r = cs.humanize("速览", ["涨停 58 家"], llm=llm, max_chars=60, use_cache=False)
    assert r["llm_ok"] is True
    assert len(r["text"]) <= 60


def test_06_disabled_returns_template(monkeypatch):
    monkeypatch.setenv("COMM_SUMMARY_ENABLED", "0")
    llm = _Stub("不该被调用")
    r = cs.humanize("速览", ITEMS, llm=llm)
    assert r["llm_ok"] is False and r["error"] == "disabled"
    assert llm.calls == 0
    assert "1.82" in r["text"]


def test_07_cache_hit_avoids_second_call():
    llm = _Stub("成交 1.82 万亿，涨停 58 家，北向流入 43 亿。")
    a = cs.humanize("速览", ITEMS, llm=llm)
    b = cs.humanize("速览", ITEMS, llm=llm)
    assert a["llm_ok"] and b["llm_ok"]
    assert llm.calls == 1, "同内容应命中缓存"
    assert b["cached"] is True


def test_08_cache_skip_flag():
    llm = _Stub("成交 1.82 万亿，涨停 58 家，北向流入 43 亿。")
    cs.humanize("速览", ITEMS, llm=llm, use_cache=False)
    cs.humanize("速览", ITEMS, llm=llm, use_cache=False)
    assert llm.calls == 2


def test_09_empty_items_safe():
    llm = _Stub("今日无数据。")
    r = cs.humanize("速览", [], llm=llm, use_cache=False)
    assert r["text"]


def test_10_degraded_status_marked():
    llm = _Stub("成交 1.82 万亿，涨停 58 家。", status="degraded")
    r = cs.humanize("速览", ITEMS, llm=llm, use_cache=False)
    assert r["tier"] == "degraded"
    assert r["llm_ok"] is True  # 文本可用，但 tier 如实标降级
    assert r["error"] == "llm_status=degraded"
