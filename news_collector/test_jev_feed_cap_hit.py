# -*- coding: utf-8 -*-
"""P0 RFC 方案①（修订版）单测：cap_hit 显式化（R5 信号）。

设计钉子（P0 RFC §4.1 修订）：
- 截断轮**只做信号**（detail.cap_hit + 留痕 + 飞书），不改水位策略——
  字面「不推水位」会回归 2026-10-16 活锁修复，水位修正归方案②。
- 告警失败必须静默，绝不影响采集退出码。
"""
import jev_feed as jf


def _hit(k=jf.CAP_HIT_ALERT_ENV, v=None):
    """便捷：清/设告警开关。"""
    import os

    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v


def setup_function(fn):
    _hit()


def teardown_function(fn):
    _hit()


def test_cap_hit_flags_summary_and_alerts(capsys, monkeypatch):
    sent = []
    monkeypatch.setattr(jf.news_ai, "push_message", lambda m: sent.append(m))
    summary = {"src": {"notice": {"inserted": 0, "fetched": 500}}}
    msg = jf._cap_hit_handle(summary, "notice", 0, 500, 500)
    assert msg is not None and "采集截断告警" in msg
    assert summary["src"]["notice"]["cap_hit"] is True
    assert len(sent) == 1 and "fetched=500" in sent[0]
    out = capsys.readouterr()
    assert "FEED CAP_HIT notice" in out.err  # stderr 留痕（err log 有信号）


def test_below_cap_is_silent(monkeypatch):
    sent = []
    monkeypatch.setattr(jf.news_ai, "push_message", lambda m: sent.append(m))
    summary = {"src": {"notice": {"inserted": 30, "fetched": 30}}}
    assert jf._cap_hit_handle(summary, "notice", 30, 30, 500) is None
    assert "cap_hit" not in summary["src"]["notice"]
    assert sent == []


def test_dryrun_never_signals(monkeypatch):
    sent = []
    monkeypatch.setattr(jf.news_ai, "push_message", lambda m: sent.append(m))
    summary = {"src": {}}
    assert jf._cap_hit_handle(summary, "notice", 0, 500, 500, dryrun=True) is None
    assert sent == []


def test_alert_failure_is_silent(monkeypatch):
    def boom(_):
        raise RuntimeError("feishu down")

    monkeypatch.setattr(jf.news_ai, "push_message", boom)
    summary = {"src": {"notice": {}}}
    msg = jf._cap_hit_handle(summary, "notice", 0, 500, 500)
    assert msg is not None  # 告警炸了，信号本身不受影响
    assert summary["src"]["notice"]["cap_hit"] is True


def test_alert_env_off(monkeypatch):
    sent = []
    monkeypatch.setattr(jf.news_ai, "push_message", lambda m: sent.append(m))
    _hit(v="0")
    summary = {"src": {"notice": {}}}
    msg = jf._cap_hit_handle(summary, "notice", 0, 500, 500)
    assert msg is not None
    assert sent == []  # 开关关：只落 detail 与留痕，不推送


def test_flag_merges_without_clobbering_existing_keys(monkeypatch):
    monkeypatch.setattr(jf.news_ai, "push_message", lambda m: None)
    summary = {"src": {"notice": {"inserted": 3, "fetched": 500, "pref_skipped": 7}}}
    jf._cap_hit_handle(summary, "notice", 3, 500, 500)
    d = summary["src"]["notice"]
    assert d["cap_hit"] is True and d["pref_skipped"] == 7 and d["inserted"] == 3
