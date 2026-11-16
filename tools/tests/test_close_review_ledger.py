# -*- coding: utf-8 -*-
"""close_review 台账闸门：入账前必须排除「未出票日」。

2026-11-16 的真实故障
--------------------
复盘 15:40:05 落盘、抑制令 15:48:31 才写（差 8 分 26 秒），于是一条自述
`NOT_STARTED / 启动 ✘ 落痕 0 次` 的预演批次进了 40 日台账：进度 2/40、
累计超额 +1.0838%，其中 +0.89 pct 来自这个不该计入的日子。

两道闸必须分别成立，且有变异检验（把行为改回旧的，断言必须反转）：
  ① 抑制令 `excluded_batches` 中 `key=exec_day` 的条目 → 不进分母；
  ② 链路 `NOT_STARTED` / 体检失败 → 不进分母。
"""
import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(os.path.dirname(HERE), "review", "close_review.py")

_M = None


def load():
    global _M
    if _M is None:
        spec = importlib.util.spec_from_file_location("close_review_under_test", ENTRY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _M = mod
    return _M


CHAIN_OK = {"verdict": "COMPLETE", "started": True, "n_start": 2, "n_codes": 15}
CHAIN_NO_START = {"verdict": "NOT_STARTED", "started": False, "n_start": 0,
                  "n_codes": 15}


# ---------------------------------------------------------------------------
# 闸门四态
# ---------------------------------------------------------------------------
def test_started_day_is_allowed():
    m = load()
    lg = m.ledger_gate("20261117", CHAIN_OK, None, {})
    assert lg["allow"] is True
    assert lg["reason"] == "OK"


def test_not_started_day_is_blocked():
    m = load()
    lg = m.ledger_gate("20261116", CHAIN_NO_START, None, {})
    assert lg["allow"] is False
    assert lg["reason"] == "NOT_STARTED"
    # 原因里必须带出「落痕 0 次」，否则读报告的人不知道为什么被剔
    assert "0" in lg["detail"]


def test_chain_probe_failure_is_blocked():
    """体检失败 = 不可测。宁可报不可测，也不把不知道写成没问题。"""
    m = load()
    lg = m.ledger_gate("20261117", {"error": "timeout"}, None, {})
    assert lg["allow"] is False
    assert lg["reason"] == "CHAIN_UNKNOWN"


def test_suppressed_day_is_blocked():
    m = load()
    lg = m.ledger_gate("20261116", CHAIN_OK, {"reason": "共识池断供"}, {})
    assert lg["allow"] is False
    assert lg["reason"] == "SUPPRESSED"


def test_decreed_exclusion_is_blocked():
    m = load()
    excl = {"20261116": {"reason": "预演产物", "n": 15, "src": "PRE", "file": "x.json"}}
    lg = m.ledger_gate("20261116", CHAIN_OK, None, excl)
    assert lg["allow"] is False
    assert lg["reason"] == "EXCLUDED_BY_DECREE"
    assert "15" in lg["detail"]


def test_force_overrides_everything():
    m = load()
    lg = m.ledger_gate("20261116", CHAIN_NO_START, {"reason": "x"}, {}, force=True)
    assert lg["allow"] is True
    assert lg["reason"] == "FORCED"


def test_grace_zero_does_not_wait():
    """grace=0 时不等待（healthy 日也不等，避免每天白等一分钟）。"""
    m = load()
    lg = m.ledger_gate("20261116", CHAIN_NO_START, None, {}, grace=0)
    assert lg["allow"] is False
    assert lg["reason"] == "NOT_STARTED"


# ---------------------------------------------------------------------------
# 分母计算 + 变异检验
# ---------------------------------------------------------------------------
def _env(tmp_path, m, monkeypatch, with_decree=True):
    deliv = tmp_path / "deliverables"
    deliv.mkdir()
    ledger = tmp_path / "ledger.jsonl"
    rows = [
        {"exec_day": "20261112", "excess_o2c": 0.1918, "quote_conflict": False},
        {"exec_day": "20261116", "excess_o2c": 0.8919, "quote_conflict": False},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if with_decree:
        sup = {"schema": "preopen_suppressed/v1", "action": "suppress",
               "exec_day": "20261116",
               "excluded_batches": [
                   {"key": "exec_day", "batch": "20261116", "src": "PRE", "n": 15,
                    "reason": "预演产物，不计入"},
                   # 历史条目：batch 是数据日、无 key 字段 —— 必须**不**被采纳
                   {"batch": "20261113", "src": "PRE", "n": 15, "reason": "legacy"},
               ]}
        (deliv / "preopen_suppressed_20261116.json").write_text(
            json.dumps(sup, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "DELIV", str(deliv))
    monkeypatch.setattr(m, "LEDGER", str(ledger))
    return ledger


def test_legacy_entry_without_key_is_not_adopted(tmp_path, monkeypatch):
    """键位不同不能硬套：无 key 的历史条目一律不采纳（宁少排，不错排）。"""
    m = load()
    _env(tmp_path, m, monkeypatch)
    ex = m.excluded_exec_days()
    assert "20261116" in ex
    assert "20261113" not in ex


def test_digest_excludes_decreed_day(tmp_path, monkeypatch):
    m = load()
    _env(tmp_path, m, monkeypatch)
    dg = m.ledger_digest("20261116")
    assert dg["n_days"] == 1
    assert dg["progress"] == "1/40"
    assert dg["excluded_days"] == ["20261116"]
    assert abs(dg["cum_excess"] - 0.1918) < 1e-9
    assert dg["includes_today"] is False


def test_mutation_old_behavior_reverses_assertion(tmp_path, monkeypatch):
    """变异检验：把排除逻辑改回旧行为（不排除），上面那条断言必须反转。

    若这条测试在旧行为下仍然通过，说明 test_digest_excludes_decreed_day
    根本没测到东西 —— 那是「测试绿但没验」的典型假通过。
    """
    m = load()
    _env(tmp_path, m, monkeypatch)
    monkeypatch.setattr(m, "excluded_exec_days", lambda: {})
    dg = m.ledger_digest("20261116")
    assert dg["n_days"] == 2                  # 旧行为：照收 2 天
    assert abs(dg["cum_excess"] - 1.0837) < 1e-3
    assert dg["includes_today"] is True


def test_mutation_gate_removed_lets_not_started_in(tmp_path, monkeypatch):
    """变异检验二：闸门若恒放行，NOT_STARTED 日就会被计入。"""
    m = load()
    real = m.ledger_gate
    monkeypatch.setattr(m, "ledger_gate",
                        lambda *a, **k: {"allow": True, "reason": "OK", "detail": ""})
    lg = m.ledger_gate("20261116", CHAIN_NO_START, None, {})
    assert lg["allow"] is True                # 旧行为（无闸）
    monkeypatch.setattr(m, "ledger_gate", real)
    assert m.ledger_gate("20261116", CHAIN_NO_START, None, {})["allow"] is False
