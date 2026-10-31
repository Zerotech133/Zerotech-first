# -*- coding: utf-8 -*-
"""INTEL_TO_N1 A/B 接线测试（WO-INTEL-N1AB / task-13）：make_context 层。

纪律：
  · flag 默认 off；off 时 make_context 输出与旧版逐字一致，且**不导入/不调用**
    intel_context（本文件用 monkeypatch 断言未被调用）；
  · flag on：reviewed JEV 可用 → news_event 换成 reviewed_jev 文本（带 id/来源）；
    无可用复核 → 回退原 price_proxy 文本（与 off 逐字一致），不伪造“已核验”；
  · 坏行不炸主链（fail-visible 计数打印）；
  · 全隔离：tmp news.db + 桩行情，不依赖生产 news.db/8091/8796。
"""
from __future__ import annotations

import json
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
ENGINE = REPO / "agent_platform" / "engine"
for p in (str(REPO), str(ENGINE)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_engine_tools():
    """按文件加载 agent_platform/engine/tools.py。

    不能 `import tools`：与 tools/tests 同跑时 sys.path 里的 `tools` 会解析成
    另一个 namespace 包，导致 monkeypatch 找不到 kline_daily（混合套件红）。
    """
    path = ENGINE / "tools.py"
    spec = importlib.util.spec_from_file_location("_intel_engine_tools", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


engine_tools = _load_engine_tools()
from agent_platform import hermes_bridge as hb  # noqa: E402
from agent_platform import intel_context as IC  # noqa: E402

# off 路径的 news_event 黄金文本（当前实现逐字；flag 不得改变 off 输出）
EXPECTED_SENTIMENT = (
    "情绪代理指标(v1，事件库jev接入v2): 近5日涨跌=0.0%，量比=1.0，"
    "现价距20日高点=0.0%，换手率=1.0%。"
    "解读提示：放量上涨偏暖、缩量下跌观望、高位放量警惕。")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jev_review_queue (
    id TEXT PRIMARY KEY, queued_at TEXT NOT NULL, conf REAL, bias TEXT,
    title TEXT, stocks TEXT, original_jev TEXT,
    status TEXT NOT NULL DEFAULT 'pending', reviewer TEXT, reviewed_at TEXT,
    decision TEXT, note TEXT)
"""


# ---------------------------------------------------------------- 夹具
def _stub_market(monkeypatch):
    """确定性行情桩（30 根同值 K 线 → chg5=0/量比=1/pos20=0）。"""
    monkeypatch.setattr(
        engine_tools, "kline_daily",
        lambda code, n=30: [["2026-10-30", 10.0, 10.5, 10.6, 9.9, 100]] * 30)
    monkeypatch.setattr(engine_tools, "quote_snapshot", lambda code: {
        "name": "测试标的", "price": 10.5, "change_pct": 1.0,
        "turnover_pct": 1.0, "pe": 10.0, "pb": 1.0, "mktcap_yi": 100.0})
    monkeypatch.setattr(hb, "context_text", lambda code=None: "")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in ("INTEL_TO_N1", "INTEL_N1_DB", "INTEL_N1_LIMIT",
                "INTEL_N1_STOCK_NAMES"):
        monkeypatch.delenv(key, raising=False)


def _insert(db: Path, nid, *, status="resolved", stocks=None,
            original_jev=None, decision="correct", title="600519 测试事件"):
    con = sqlite3.connect(str(db))
    try:
        con.execute(SCHEMA)
        jev = original_jev if original_jev is not None else json.dumps({
            "bias": "bull", "bias_cn": "看多", "conf": 0.62,
            "event_type": "earnings", "event_cn": "业绩",
            "companies": [], "reason": "复核摘要"}, ensure_ascii=False)
        con.execute(
            "INSERT INTO jev_review_queue (id, queued_at, conf, bias, title, "
            "stocks, original_jev, status, reviewer, reviewed_at, decision, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, "2026-10-30 17:00", 0.62, "bull", title,
             json.dumps(stocks if stocks is not None else ["600519.SH"],
                        ensure_ascii=False),
             jev, status, "N1", "2026-10-31 09:00", decision, ""))
        con.commit()
    finally:
        con.close()
    return db


# ---------------------------------------------------------------- off：逐字一致
def test_off_output_is_byte_identical_and_does_not_touch_intel(monkeypatch, capsys):
    _stub_market(monkeypatch)

    def _boom(*a, **k):  # off 时若被调用即红
        raise AssertionError("INTEL_TO_N1 off 时不得调用 reviewed_news_event")

    monkeypatch.setattr(IC, "reviewed_news_event", _boom)

    out_unset = engine_tools.make_context("600519")
    assert out_unset["news_event"] == EXPECTED_SENTIMENT
    assert out_unset["name"] == "测试标的"
    assert capsys.readouterr().out == ""       # off 无额外打印

    monkeypatch.setenv("INTEL_TO_N1", "0")
    out_zero = engine_tools.make_context("600519")
    monkeypatch.setenv("INTEL_TO_N1", "false")
    out_false = engine_tools.make_context("600519")
    monkeypatch.setenv("INTEL_TO_N1", "off")
    out_off = engine_tools.make_context("600519")
    assert out_unset == out_zero == out_false == out_off   # 整 dict 逐字一致
    assert out_unset["news_event"] == EXPECTED_SENTIMENT


def test_off_does_not_import_intel_context(monkeypatch):
    """off 时即使 intel_context 模块不可用也必须正常（零依赖）。"""
    _stub_market(monkeypatch)
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "agent_platform.intel_context", None)
    out = engine_tools.make_context("600519")
    assert out["news_event"] == EXPECTED_SENTIMENT


# ---------------------------------------------------------------- on：reviewed
def test_on_uses_reviewed_jev_text(monkeypatch, tmp_path, capsys):
    _stub_market(monkeypatch)
    db = _insert(tmp_path / "news.db", "sina:5182001")
    monkeypatch.setenv("INTEL_TO_N1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))

    out = engine_tools.make_context("600519")
    text = out["news_event"]
    assert text.startswith("[reviewed_jev]")
    assert "source=reviewed_jev" in text
    assert "sina:5182001" in text and "rv-" in text
    assert "看多" in text
    assert "情绪代理指标" not in text           # price_proxy 已被 reviewed 替换
    # 其余数据段不受影响
    assert out["name"] == "测试标的"
    assert out["final_decision"].startswith("基本面快照: ")
    printed = capsys.readouterr().out
    assert "source=reviewed_jev" in printed and "events=1" in printed


def test_on_reviewed_includes_hermes_bridge_suffix(monkeypatch, tmp_path):
    """reviewed 路径仍享受既有 Hermes 只读补充（在两段之后追加）。"""
    _stub_market(monkeypatch)
    monkeypatch.setattr(hb, "context_text", lambda code=None: "HERMES_X")
    db = _insert(tmp_path / "news.db", "ev:1")
    monkeypatch.setenv("INTEL_TO_N1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    out = engine_tools.make_context("600519")
    assert "reviewed_jev" in out["news_event"]
    assert "HERMES_X" in out["news_event"]
    assert "HERMES_X" in out["market_technical"]


# ---------------------------------------------------------------- on：fallback
def test_on_no_reviewed_falls_back_to_price_proxy(monkeypatch, tmp_path, capsys):
    _stub_market(monkeypatch)
    db = _insert(tmp_path / "news.db", "pending:1", status="pending")
    monkeypatch.setenv("INTEL_TO_N1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))

    out = engine_tools.make_context("600519")
    assert out["news_event"] == EXPECTED_SENTIMENT   # 与 off 逐字一致
    assert "已核验" not in out["news_event"]
    assert "source=reviewed_jev" not in out["news_event"]
    printed = capsys.readouterr().out
    assert "source=price_proxy" in printed
    assert "reason=no_reviewed_jev" in printed


def test_on_missing_db_falls_back_visible(monkeypatch, tmp_path, capsys):
    _stub_market(monkeypatch)
    monkeypatch.setenv("INTEL_TO_N1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(tmp_path / "missing.db"))
    out = engine_tools.make_context("600519")
    assert out["news_event"] == EXPECTED_SENTIMENT
    assert "reason=news_db_missing" in capsys.readouterr().out


# ---------------------------------------------------------------- on：坏行不炸
def test_on_bad_rows_do_not_break_chain(monkeypatch, tmp_path, capsys):
    _stub_market(monkeypatch)
    db = _insert(tmp_path / "news.db", "bad:jev", original_jev="{not-json")
    _insert(db, "ok:1")
    monkeypatch.setenv("INTEL_TO_N1", "1")
    monkeypatch.setenv("INTEL_N1_DB", str(db))
    out = engine_tools.make_context("600519")     # 不抛
    assert out["news_event"].startswith("[reviewed_jev]")
    assert "ok:1" in out["news_event"]
    assert "bad_rows=1" in capsys.readouterr().out


def test_on_intel_context_crash_keeps_old_chain(monkeypatch, tmp_path):
    """接线异常（含导入失败）不得打崩 make_context：保持 price_proxy。"""
    _stub_market(monkeypatch)
    monkeypatch.setenv("INTEL_TO_N1", "1")

    def _boom(*a, **k):
        raise RuntimeError("simulated import/query crash")

    monkeypatch.setattr(IC, "reviewed_news_event", _boom)
    out = engine_tools.make_context("600519")
    assert out["news_event"] == EXPECTED_SENTIMENT
    assert out["name"] == "测试标的"


# ---------------------------------------------------------------- 旧回归不被破坏
def test_off_existing_hermes_bridge_suffix_still_appended(monkeypatch):
    _stub_market(monkeypatch)
    monkeypatch.setattr(hb, "context_text", lambda code=None: "HERMES_OFF")
    out = engine_tools.make_context("600519")
    assert "HERMES_OFF" in out["market_technical"]
    assert "HERMES_OFF" in out["news_event"]
    assert out["news_event"].startswith(EXPECTED_SENTIMENT)
