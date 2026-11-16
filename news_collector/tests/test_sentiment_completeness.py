# -*- coding: utf-8 -*-
"""雪球当日覆盖完整性判据（CR-20261114-02 选项 C）。

核心纪律：None（不知道）不得冒充 False（不完整）。队列或账本读不到时返回 unknown，
否则每天都会报假警，真断供被淹没——这与「不适用被读成故障」是同一类错误。
"""
import json
import os
import sys
from datetime import datetime

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import run_sentiment_snapshot as S  # noqa: E402
import xq_stock_timeline as X  # noqa: E402

TODAY = datetime(2026, 11, 16, 8, 0, 0)
YDAY = datetime(2026, 11, 15, 7, 30, 0)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / "data" / "xq_timeline").mkdir(parents=True)
    monkeypatch.setattr(S, "HERE", str(tmp_path))
    monkeypatch.setattr(S, "COMPLETE_F",
                        str(tmp_path / "data" / "sentiment_completeness.json"))
    return tmp_path


def _set_queue(monkeypatch, codes):
    monkeypatch.setattr(X, "load_queue",
                        lambda scope="all", sources=None: list(codes))


def _set_ledger(tmp, mapping):
    p = tmp / "data" / "xq_timeline" / "_attempt.json"
    p.write_text(json.dumps({"v": 1, "stocks": mapping}), encoding="utf-8")


def test_missing_ledger_returns_unknown(sandbox, monkeypatch):
    _set_queue(monkeypatch, ["600000", "600001"])
    info = S.xq_completeness(now=TODAY)
    assert info["xq_complete"] is None, "账本缺失应判 unknown，不得判 False"
    assert "账本不存在" in info["reason"]
    assert info["xq_queue"] == 2


def test_empty_queue_returns_unknown(sandbox, monkeypatch):
    _set_queue(monkeypatch, [])
    _set_ledger(sandbox, {})
    info = S.xq_completeness(now=TODAY)
    assert info["xq_complete"] is None
    assert info["reason"] == "队列为空"


def test_full_coverage_is_complete(sandbox, monkeypatch):
    _set_queue(monkeypatch, ["600000", "600001"])
    _set_ledger(sandbox, {"600000": TODAY.timestamp(), "600001": TODAY.timestamp()})
    info = S.xq_completeness(now=TODAY)
    assert info["xq_complete"] is True
    assert info["xq_attempted_today"] == 2
    assert info["xq_coverage"] == 1.0


def test_partial_coverage_is_incomplete(sandbox, monkeypatch):
    """1/2 = 50% < 95% → INCOMPLETE，且 reason 带具体数字。"""
    _set_queue(monkeypatch, ["600000", "600001"])
    _set_ledger(sandbox, {"600000": TODAY.timestamp(), "600001": YDAY.timestamp()})
    info = S.xq_completeness(now=TODAY)
    assert info["xq_complete"] is False
    assert info["xq_attempted_today"] == 1
    assert "1/2" in info["reason"]


def test_yesterday_timestamp_not_counted(sandbox, monkeypatch):
    """昨日尝试时间不算今日覆盖——否则永远「已跑完」。"""
    _set_queue(monkeypatch, ["600000", "600001", "600002"])
    _set_ledger(sandbox, {"600000": YDAY.timestamp(),
                          "600001": YDAY.timestamp(),
                          "600002": YDAY.timestamp()})
    info = S.xq_completeness(now=TODAY)
    assert info["xq_attempted_today"] == 0
    assert info["xq_complete"] is False


def test_missing_code_in_ledger_not_counted(sandbox, monkeypatch):
    """队列里有、账本里没有的票 = 从未尝试，不计入覆盖。"""
    _set_queue(monkeypatch, ["600000", "600001"])
    _set_ledger(sandbox, {"600000": TODAY.timestamp()})
    info = S.xq_completeness(now=TODAY)
    assert info["xq_attempted_today"] == 1
    assert info["xq_complete"] is False


def test_bad_timestamp_ignored_not_crash(sandbox, monkeypatch):
    """账本里混入异常值不得整段崩。"""
    _set_queue(monkeypatch, ["600000", "600001"])
    _set_ledger(sandbox, {"600000": TODAY.timestamp(), "600001": "not-a-time"})
    info = S.xq_completeness(now=TODAY)
    assert info["xq_attempted_today"] == 1
    assert info["xq_complete"] is False


def test_threshold_boundary(sandbox, monkeypatch):
    """恰好 95% 判 complete（>= 阈值），94% 判 incomplete。"""
    codes = ["6000%02d" % i for i in range(100)]
    _set_queue(monkeypatch, codes)
    hit = {c: TODAY.timestamp() for c in codes[:95]}
    _set_ledger(sandbox, hit)
    assert S.xq_completeness(now=TODAY)["xq_complete"] is True

    hit94 = {c: TODAY.timestamp() for c in codes[:94]}
    _set_ledger(sandbox, hit94)
    assert S.xq_completeness(now=TODAY)["xq_complete"] is False


# ---------------------------------------------------------------- 格式化与落盘
def test_fmt_unknown():
    assert S._fmt_comp({"xq_complete": None, "reason": "无账本"}).startswith("unknown")


def test_fmt_complete():
    txt = S._fmt_comp({"xq_complete": True, "xq_attempted_today": 700,
                       "xq_queue": 700, "xq_coverage": 1.0})
    assert "700/700" in txt and "complete" in txt


def test_fmt_incomplete_says_incomplete():
    txt = S._fmt_comp({"xq_complete": False, "xq_attempted_today": 649,
                       "xq_queue": 700, "xq_coverage": 0.9271})
    assert "INCOMPLETE" in txt and "649/700" in txt


def test_save_writes_atomically(sandbox):
    info = {"asof": "2026-11-16 08:00:00", "xq_complete": False}
    S._save_completeness(info)
    p = sandbox / "data" / "sentiment_completeness.json"
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8"))["xq_complete"] is False
    assert not (sandbox / "data" / "sentiment_completeness.json.tmp").exists()


def test_save_failure_does_not_raise(monkeypatch):
    """标注失败绝不能阻断快照本身。"""
    monkeypatch.setattr(S, "COMPLETE_F", "Z:/nonexistent_dir_xyz/x.json")
    S._save_completeness({"xq_complete": None})   # 不应抛异常
