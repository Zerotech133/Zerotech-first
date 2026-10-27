# -*- coding: utf-8 -*-
"""P2 单测：W41 死信补达 + W43 策略工单环 + 投喂补强（抽取/L3 标注）。"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import dead_letter  # noqa: E402
import feed_extract  # noqa: E402
import redeliver  # noqa: E402
import strategy_ring as sr  # noqa: E402


# ── W41 死信补达 ────────────────────────────────────────
def test_redeliver_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(dead_letter, "DEAD_PATH", str(tmp_path / "dead.jsonl"))
    monkeypatch.setattr(redeliver, "STATE", str(tmp_path / "state.json"))

    good = dead_letter.register(
        "hub", "openapi", "oc_1", "timeout", payload='{"title":"x"}'
    )
    legacy = dead_letter.register("hub", "openapi", "oc_2", "timeout")
    assert good and legacy

    _now = redeliver.time.time() + 3600
    items = redeliver.pending(now=_now)
    by = {i["dead_id"]: i for i in items}
    assert by[good]["reason"] == "ready"
    assert by[legacy]["reason"] == "legacy_no_payload"

    sent = []

    def sender(payload, item):
        sent.append((payload, item["dead_id"]))
        return True, "om_redeliver_1", ""

    r = redeliver.run_once(sender, now=_now)
    assert r["tried"] == 1 and r["ok"] == 1
    assert sent and sent[0][0] == '{"title":"x"}'

    # 补达成功后不再出现 ready
    items2 = redeliver.pending(now=_now)
    assert all(i["dead_id"] != good for i in items2)
    s = redeliver.summary()
    assert s["cap"] == 3 and "by_reason" in s


def test_redeliver_backoff_and_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(dead_letter, "DEAD_PATH", str(tmp_path / "dead.jsonl"))
    monkeypatch.setattr(redeliver, "STATE", str(tmp_path / "state.json"))
    d = dead_letter.register("hub", "openapi", "oc_3", "x", payload="p")
    # 模拟 1 次失败补达；用「未来 1 小时」作为 now，确保第一次退避 60s 已过
    dead_letter.mark_redelivered(d, False, None, "fail1")
    _now = redeliver.time.time() + 3600
    items = redeliver.pending(now=_now)
    it = next(i for i in items if i["dead_id"] == d)
    assert it["reason"] in ("ready", "backoff")
    # 再叠加 2 次失败 → exhausted
    dead_letter.mark_redelivered(d, False, None, "fail2")
    dead_letter.mark_redelivered(d, False, None, "fail3")
    items2 = redeliver.pending(now=_now + 99999.0)
    it2 = next(i for i in items2 if i["dead_id"] == d)
    assert it2["reason"] == "exhausted"


# ── W43 策略工单环 ──────────────────────────────────────
def test_strategy_pipeline(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(sr, "SHADOW", str(tmp_path / "shadow.jsonl"))

    def _fake_bt(order_id, execute=False, d0=None, d1=None):
        row = sr._last_order(order_id)
        res = {
            "status": "done",
            "job_id": "fake",
            "result": {"summary": {"total_return_pct": 12.3}},
            "evidence": "B",
        }
        row = sr._update(
            order_id,
            status="backtested",
            backtest=res,
            backtest_plan={
                "window": ["20250101", "20251231"],
                "pit": {"hist_st": True, "pit_st": True},
            },
        )
        return {"ok": True, "order": row, "backtest": res}

    monkeypatch.setattr(sr, "backtest", _fake_bt)
    r = sr.create_order("均线多头排列，放量突破，RPS>90，排除ST")
    assert r["ok"] is True
    oid = r["order"]["id"]
    p = sr.pipeline(oid)
    assert p["ok"] is True, p
    o = p["order"]
    assert o["status"] == "shadowing"
    assert (o.get("lookahead") or {}).get("pass") is True
    assert (o.get("backtest") or {}).get("status") == "done"
    sh = sr.shadow_status(oid)
    assert sh["ok"] is True and sh["shadow"]["days"] == 30
    assert sh["days_left"] is not None


def test_strategy_lookahead_rejects_future_leak(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(sr, "SHADOW", str(tmp_path / "shadow.jsonl"))
    oid = sr.create_order("次日收盘后买入未来涨幅最高的股票")["order"]["id"]
    sr.draft(oid)
    r = sr.lookahead_check(oid)
    assert r["ok"] is False and r["lookahead"]["leaks"]


def test_strategy_no_trading_imports():
    src = open(sr.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module.split(".")[0])
    banned = {"quant_backtest", "trade", "order", "position", "k_stock"}
    assert not (mods & banned), mods & banned


# ── 投喂补强 ───────────────────────────────────────────
def test_extract_uses_trafilatura_when_available(monkeypatch):
    """trafilatura 不可用时走 lxml 启发式（诚实标注 extractor）。"""
    r = feed_extract.extract_main(
        "<html><body><article><p>这是一段正文，用于测试抽取器选择逻辑。</p>"
        "</article></body></html>",
        "https://e.com",
    )
    assert r["extractor"] in ("trafilatura", "lxml-heuristic", "text-fallback")
    assert "正文" in r["text"]


def test_extract_size_guard():
    big = (
        "<html><body><article>"
        + "<p>内容段落。</p>" * 5000
        + "</article></body></html>"
    )
    r = feed_extract.extract_main(big)
    assert len(r["text"]) <= feed_extract.TEXT_MAX


def test_shadow_requires_done_backtest(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(sr, "SHADOW", str(tmp_path / "shadow.jsonl"))
    oid = sr.create_order("均线多头排列，放量突破，RPS>90")["order"]["id"]
    sr.draft(oid)
    sr.backtest(oid)  # 默认 planned（不执行）
    r = sr.shadow_start(oid)
    assert r["ok"] is False and "回测未完成" in r["error"]


def test_shadow_rejects_negative_backtest(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "ORDERS", str(tmp_path / "orders.jsonl"))
    monkeypatch.setattr(sr, "SHADOW", str(tmp_path / "shadow.jsonl"))
    oid = sr.create_order("均线多头排列，放量突破，RPS>90")["order"]["id"]
    sr.draft(oid)

    def _neg_bt(order_id_arg, execute=False, d0=None, d1=None):
        res = {
            "status": "done",
            "result": {"summary": {"total_return_pct": -21.9}},
            "evidence": "B",
        }
        row = sr._update(order_id_arg, status="backtested", backtest=res)
        return {"ok": True, "order": row, "backtest": res}

    orig = sr.backtest
    sr.backtest = _neg_bt
    try:
        sr.backtest(oid)
        r = sr.shadow_start(oid)
    finally:
        sr.backtest = orig
    assert r["ok"] is False and "拒绝进入影子期" in r["error"]


def test_infer_buy_filter():
    assert sr.infer_buy_filter("缠论二买，全市场，top10") == "2"
    assert sr.infer_buy_filter("三买信号") == "3"
    assert sr.infer_buy_filter("二三买混合") == "23"
    assert sr.infer_buy_filter("均线多头") == ""
