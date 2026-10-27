# -*- coding: utf-8 -*-
"""移动端公网受限门单测（白名单/限流）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import m_public_gate as g  # noqa: E402


def test_allow_pages_and_assets():
    assert g.match_rule("/m/") is False
    assert g.match_rule("/m/picks") is False
    assert g.match_rule("/m/chan") is False
    assert g.match_rule("/m/assets/m.css") is False
    assert g.match_rule("/static/tokens.css") is False


def test_allow_api():
    assert g.match_rule("/api/m/health") is False
    assert g.match_rule("/api/m/data") is False
    assert g.match_rule("/api/m/beacon") is True  # 唯一允许 POST
    assert g.match_rule("/api/m/link") is None  # 签发入口不进公网
    assert g.match_rule("/api/m/share") is None
    # 订阅免登端点（2026-10-25）：start/check GET、exchange POST
    assert g.match_rule("/api/m/auth/start") is False
    assert g.match_rule("/api/m/auth/check") is False
    assert g.match_rule("/api/m/auth/exchange") is True
    # 工单催办/回复（2026-10-25）：GET/POST 均放行
    assert g.match_rule("/api/m/ticket/nudge") is True
    assert g.match_rule("/api/m/ticket/reply") is True


def test_allow_share():
    assert g.match_rule("/s/abc.def-123_XYZ") is False


def test_deny_private_pages_and_internal_api():
    assert g.match_rule("/m/ops") is None
    assert g.match_rule("/m/ops.html") is None
    assert g.match_rule("/m/subscription") is False  # 对外可看（空态/脱敏）
    assert g.match_rule("/api/paper_status") is None
    assert g.match_rule("/api/subscription/status") is None
    assert g.match_rule("/") is None
    assert g.match_rule("/picks.html") is None


def test_rate_limiter():
    lim = g._Limiter(limit=3, window=60)
    assert lim.allow("1.2.3.4")
    assert lim.allow("1.2.3.4")
    assert lim.allow("1.2.3.4")
    assert not lim.allow("1.2.3.4")
    assert lim.allow("5.6.7.8")  # 其他 IP 不受影响
