# -*- coding: utf-8 -*-
"""WA1 feed consumer 离线测试（WO-WATCH-FEED）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import watch_feed_consumer as feed  # noqa: E402


def _files(tmp_path):
    sig = tmp_path / "signals.jsonl"
    sig.write_text(json.dumps({
        "symbol": "601888.SH", "score": 99.9, "source": "xunyou_pool",
        "trade_date": "20261021",
        "meta": {"tier": "T1", "action": "buy"},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    chan = tmp_path / "chanlun.jsonl"
    chan.write_text(json.dumps({
        "market": "CN", "code": "601888.SH", "price": 80.0, "entry_px": 81.5,
        "detect_date": "2026-10-21", "type": "3买",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(sig), str(chan)


def test_candidates_join_price_and_date(tmp_path):
    sig, chan = _files(tmp_path)
    ok, skipped = feed.candidates(sig, chan, day="2026-10-21", max_age_days=0)
    assert len(ok) == 1 and not skipped
    assert ok[0]["signal"]["symbol"] == "601888.SH"
    assert ok[0]["signal"]["action"] == "BUY"
    assert ok[0]["signal"]["price"] == 81.5
    assert ok[0]["signal"]["report_id"].startswith("WA1-2026-10-21-601888.SH")


def test_stale_and_missing_price_skipped(tmp_path):
    sig, chan = _files(tmp_path)
    ok, skipped = feed.candidates(sig, chan, day="2026-10-30", max_age_days=1)
    assert not ok
    assert skipped and skipped[0]["reason"] == "stale"
    # 去掉价格源 → no_price
    chan2 = tmp_path / "empty.jsonl"
    chan2.write_text("", encoding="utf-8")
    ok2, skipped2 = feed.candidates(sig, str(chan2), day="2026-10-21")
    assert not ok2 and skipped2[0]["reason"] == "no_price"


def test_run_creates_pending_only_and_idempotent(tmp_path):
    sig, chan = _files(tmp_path)
    bot = str(tmp_path / "bot")
    r1 = feed.run(sig, chan, day="2026-10-21", data_dir=bot)
    assert r1["candidates"] == 1
    assert r1["created"][0]["status"] == "pending"
    files = list((tmp_path / "bot" / "confirmations").glob("*.json"))
    assert len(files) == 1
    # 第二次同 report_id → 仍只有 1 条 pending
    r2 = feed.run(sig, chan, day="2026-10-21", data_dir=bot)
    assert r2["created"][0]["confirmation_id"] == r1["created"][0]["confirmation_id"]
    assert len(list((tmp_path / "bot" / "confirmations").glob("*.json"))) == 1
    # 无 paper 执行
    assert not (tmp_path / "ledger.jsonl").exists()


def test_run_auto_internal_executes_paper(tmp_path):
    sig, chan = _files(tmp_path)
    bot = str(tmp_path / "bot")
    res = feed.run(sig, chan, day="2026-10-21", data_dir=bot, auto=True,
                   paper_state_path=str(tmp_path / "state.json"),
                   paper_ledger_path=str(tmp_path / "ledger.jsonl"))
    assert res["created"][0]["status"] == "executed"
    assert (tmp_path / "ledger.jsonl").exists()
