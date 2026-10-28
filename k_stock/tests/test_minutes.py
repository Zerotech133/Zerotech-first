# -*- coding: utf-8 -*-
"""K-STOCK-137 S-c 会议纪要投递通道测试：site 默认投递/非法通道 Reject/trader 追加/
feishu 未配置 fail-closed/账本原子写可解析。全部 monkeypatch 临时账本，真库零触碰。"""

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / "paper"))

import minutes  # noqa: E402
from minutes import Reject, inbox, list_minutes, send  # noqa: E402
import logging


def _setup(tmp: Path):
    """monkeypatch 三个账本到临时区，并造一个经理档案（含 delivery_channels）。"""
    minutes.MINUTES_PATH = tmp / "minutes.json"
    minutes.INBOX_PATH = tmp / "minutes_inbox.json"
    minutes.TRADER_FEED_PATH = tmp / "minutes_trader_feed.json"
    mdir = tmp / "managers"
    mdir.mkdir()
    (mdir / "bull_desk.json").write_text(
        json.dumps(
            {
                "id": "bull_desk",
                "name": "多头台",
                "status": "live",
                "delivery_channels": ["site", "trader"],
            }
        ),
        encoding="utf-8",
    )
    minutes.MANAGERS_DIR = mdir


def test_site_default_and_inbox():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        rec = send("bull_desk", "pre_open", "盘前作战计划", body="关注量能")
        assert rec["status"] == "delivered" and rec["channel"] == "site"
        assert len(inbox()) == 1 and inbox()[0]["title"] == "盘前作战计划"
        assert inbox("bull_desk")[0]["mid"] == "bull_desk"
        assert inbox("ghost") == []  # 过滤无匹配不炸
    print("PASS test_site_default_and_inbox")


def test_invalid_channel_reject():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        for ch in ("bogus", "", "FEISHU"):
            try:
                send("bull_desk", "post_close", "复盘", channel=ch)
                raise AssertionError(f"通道 {ch!r} 应被拒")
            except Reject as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/tests/test_minutes.py", exc)
        # 档案声明通道之外也应拒（trader 在声明内，feishu 不在）
        try:
            send("bull_desk", "post_close", "复盘", channel="feishu")
            raise AssertionError("未声明通道应被拒")
        except Reject as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/tests/test_minutes.py", exc)
        assert list_minutes() == []  # 拒后不留账
    print("PASS test_invalid_channel_reject")


def test_trader_feed_append():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        send("bull_desk", "post_close", "复盘一", channel="trader")
        send("bull_desk", "post_close", "复盘二", channel="trader")
        d = json.loads(minutes.TRADER_FEED_PATH.read_text(encoding="utf-8"))
        assert len(d["items"]) == 2
        assert [r["title"] for r in d["items"]] == ["复盘一", "复盘二"]
    print("PASS test_trader_feed_append")


def test_feishu_fail_closed():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        (Path(tmp) / "managers" / "bull_desk.json").write_text(
            json.dumps(
                {
                    "id": "bull_desk",
                    "name": "多头台",
                    "status": "live",
                    "delivery_channels": ["feishu"],
                }
            ),
            encoding="utf-8",
        )
        import os

        old = os.environ.pop("FEISHU_WEBHOOK", None)
        try:
            rec = send("bull_desk", "pre_open", "飞书纪要", channel="feishu")
            assert rec["status"] == "failed"  # fail-closed：False 不抛
            assert "FEISHU_WEBHOOK" in rec["meta"]["error"]
        finally:
            if old is not None:
                os.environ["FEISHU_WEBHOOK"] = old
    print("PASS test_feishu_fail_closed")


def test_ledger_atomic_and_parseable():
    with tempfile.TemporaryDirectory() as tmp:
        _setup(Path(tmp))
        for i in range(3):
            send("bull_desk", "pre_open", f"纪要{i}")
        d = json.loads(minutes.MINUTES_PATH.read_text(encoding="utf-8"))
        assert len(d["items"]) == 3
        for r in d["items"]:
            assert r["id"] and r["mid"] == "bull_desk" and r["status"] == "delivered"
        # tmp 残留检查：原子写后无 .tmp. 文件
        assert not list(Path(tmp).glob("*.tmp.*"))
    print("PASS test_ledger_atomic_and_parseable")


if __name__ == "__main__":
    test_site_default_and_inbox()
    test_invalid_channel_reject()
    test_trader_feed_append()
    test_feishu_fail_closed()
    test_ledger_atomic_and_parseable()
    print("5/5 PASS")
