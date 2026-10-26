# -*- coding: utf-8 -*-
"""agent.daily 端口证据接线单测（K-ORG-20261026-AGENTHEALTH）。

覆盖：
  · _port_open 对监听/未监听端口的判定（本地 socket，不依赖外部网络）
  · check_port 的 DONE / MISSED 语义
  · daily_work 目录里 INV-01 / RUN-01 的证据类型已切换（防回退到旧口径）
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import agent_daily as ad  # noqa: E402


def _listen() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


def test_port_open_true_for_listening():
    srv, port = _listen()
    try:
        assert ad._port_open(port) is True
    finally:
        srv.close()


def test_port_open_false_for_closed():
    srv, port = _listen()
    srv.close()  # 关闭后端口必然拒连
    assert ad._port_open(port) is False
    assert ad._port_open(0) is False


def test_check_port_done_when_all_listening():
    srv, port = _listen()
    try:
        status, evidence = ad.check_port({"ports": [port]})
        assert status == ad.DONE, evidence
        assert str(port) in evidence
    finally:
        srv.close()


def test_check_port_missed_lists_closed_ports():
    srv, port = _listen()
    srv.close()
    status, evidence = ad.check_port({"ports": [port]})
    assert status == ad.MISSED
    assert str(port) in evidence


def test_check_port_empty_ports_is_missed():
    status, evidence = ad.check_port({})
    assert status == ad.MISSED
    assert "ports" in evidence


def test_catalog_evidence_switched():
    catalog = json.loads(
        (REPO / "agent_platform" / "registry" / "daily_work.json").read_text(
            encoding="utf-8"))
    items = catalog if isinstance(catalog, list) else catalog.get("items") or []
    by_id = {item.get("id"): item for item in items if isinstance(item, dict)}
    inv = (by_id["INV-01"].get("evidence") or {})
    assert inv.get("type") == "file"
    assert inv.get("path", "").endswith("chanlun_pool_eval_cache.json"), inv
    run = (by_id["RUN-01"].get("evidence") or {})
    assert run.get("type") == "port"
    assert [int(p) for p in run.get("ports") or []] == [8734, 8091]
