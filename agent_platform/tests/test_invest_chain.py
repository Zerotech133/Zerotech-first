# -*- coding: utf-8 -*-
"""量化交易部联调测试（2026-10-30 WO-INV-CHAIN）。

覆盖：
1. ST1 策略旁路桥：最新周记渲染、过期不回退、无文件空串；
2. WA1→QS1 受理/ACK 链：self-test（ACK/幂等/冲突拒绝/台账1条）；
3. QS1 探针识别受理 ACK → active；
4. run_shadow 已接入策略桥（源码级）。
"""
import importlib.util
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "agent_platform"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bridge = _load("strategy_alpha_bridge_t", REPO / "agent_platform" / "strategy_alpha_bridge.py")
accept = _load("qs1_accept_t", REPO / "tools" / "qs1_accept_upstream.py")
probe = _load("qs1_probe_t", REPO / "tools" / "qs1_runtime_probe.py")


def test_bridge_render_latest(tmp_path):
    d = tmp_path / "thesis"
    d.mkdir()
    (d / "ST1_2026-W44.md").write_text("# 结构观察\n中枢上移，等回踩", encoding="utf-8")
    text = bridge.render_strategy_context(thesis_dir=d)
    assert "[ST1策略旁路" in text and "中枢上移" in text and "draft≠adopt" in text


def test_bridge_empty_and_expired(tmp_path):
    d = tmp_path / "thesis"
    d.mkdir()
    assert bridge.render_strategy_context(thesis_dir=d) == ""
    old = d / "ST1_2026-W30.md"
    old.write_text("旧周记", encoding="utf-8")
    past = (datetime.now() - timedelta(days=30)).timestamp()
    import os
    os.utime(old, (past, past))
    assert bridge.render_strategy_context(thesis_dir=d, max_chars=10) == ""


def test_accept_self_test():
    res = accept.self_test()
    assert res["accepted"]["status"] == "accepted"
    assert res["duplicate"]["duplicate"] is True
    assert res["conflict"] == "RECEIPT_CONFLICT"
    assert res["ledger_lines"] == 1


def test_probe_ack_makes_active(tmp_path):
    acked = tmp_path / "acked"
    acked.mkdir()
    (acked / "WA1-1.json").write_text("{}", encoding="utf-8")
    r = probe.probe([], {}, now=datetime.now(), days=7, acked_dir=str(acked))
    assert r["status"] == "active" and r["recent_acks"] == 1
    r2 = probe.probe([], {}, now=datetime.now(), days=7, acked_dir=str(tmp_path / "none"))
    assert r2["status"] == "standby" and r2["recent_acks"] == 0


def test_run_shadow_wires_bridge():
    src = io.open(REPO / "agent_platform" / "run_shadow.py", encoding="utf-8").read()
    assert "render_strategy_context" in src
    assert "final_decision" in src
