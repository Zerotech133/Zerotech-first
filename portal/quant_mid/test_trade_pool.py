# -*- coding: utf-8 -*-
"""K-QUANT-001: trade_pool 单元测试 (mock psycopg2).

覆盖: 签名校验 / SQL 正确 / 状态机约束 / push_secrets.json 供钥链.
真 DB 集成验证已在 smoke_trade_pool.py 完成, 此文件只 mock.
"""

from __future__ import annotations
import json
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from portal.quant_mid import trade_pool as tp


# ── fixtures ──────────────────────────────────────────────
@pytest.fixture
def _mock_conn(monkeypatch):
    """替换 psycopg2.connect → MagicMock context manager."""
    fake_conn = MagicMock()
    fake_cur = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.__exit__.return_value = False
    fake_conn.cursor.return_value = fake_cur

    with patch("portal.quant_mid.trade_pool.psycopg2") as m:
        m.connect.return_value = fake_conn
        yield fake_conn, fake_cur


@pytest.fixture(autouse=True)
def _clear_secrets_cache():
    """每次前清空 _SECRETS_CACHE, 避免跨测试污染."""
    tp._SECRETS_CACHE.clear()
    tp._SECRETS_CACHE_TS = 0
    # 清空 env DB vars 兜底
    for k in list(os.environ):
        if k.startswith("QUANT_DB_"):
            del os.environ[k]
    yield


# ── push_to_pool 签名校验 ────────────────────────────────
def test_push_to_pool_rejects_bad_signal():
    with pytest.raises(ValueError, match="signal"):
        tp.push_to_pool("600000.SH", "SOMETHING", 0.8, "agent")


def test_push_to_pool_rejects_confidence_out_of_range():
    with pytest.raises(ValueError):
        tp.push_to_pool("600000.SH", "BUY", 1.5, "agent")
    with pytest.raises(ValueError):
        tp.push_to_pool("600000.SH", "BUY", -0.1, "agent")


def test_push_to_pool_rejects_empty_code():
    with pytest.raises(ValueError):
        tp.push_to_pool("", "BUY", 0.5, "agent")
    with pytest.raises(ValueError):
        tp.push_to_pool("600000.SH", "BUY", 0.5, "")


# ── push_to_pool SQL 正确 ──────────────────────────────────
def test_push_to_pool_executes_insert(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.fetchone.return_value = (42,)

    rid = tp.push_to_pool("000001.SZ", "buy", 0.9, "quant_a4", "理由", "平安银行")
    assert rid == 42

    fake_cur.execute.assert_called_once()
    sql = fake_cur.execute.call_args[0][0]
    assert "INSERT INTO trade_pool" in sql
    assert "'pending_research'" in sql or "'pending_research'" in sql.replace("\n", "")
    fake_conn.commit.assert_called_once()


# ── research_decide 状态流转 ──────────────────────────────
def test_research_decide_approve(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 1
    tp.research_decide(1, approve=True, comment="OK")
    sql, args = fake_cur.execute.call_args[0]
    assert "UPDATE trade_pool" in sql
    assert "approved" in args


def test_research_decide_not_found_raises(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 0
    with pytest.raises(ValueError, match="not found"):
        tp.research_decide(999, approve=True)
    # rollback 被调两次: trade_pool 内部自己 rollback 一次 + _get_conn except 兜底一次
    fake_conn.rollback.assert_called()


# ── set_position 状态约束 ──────────────────────────────────
def test_set_position_invalid_state_raises(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 0  # row 在 pending_research (没先 approve)
    with pytest.raises(ValueError, match="approved/building_position"):
        tp.set_position(1, 10.0, 9.0, 12.0, 5.0)


def test_set_position_updates_active_watch(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.rowcount = 1
    tp.set_position(1, 10.5, 9.8, 12.0, 5.0, entry_date="2026-10-19")
    sql, args = fake_cur.execute.call_args[0]
    assert "'active_watch'" in sql


# ── close_position 闭环 ────────────────────────────────────
def test_close_position_validates_final_status():
    with pytest.raises(ValueError, match="closed_"):
        tp.close_position(1, 10.0, final_status="active_watch")


# ── push_secrets.json 供钥链 ────────────────────────────────
def test_db_config_prefers_env():
    os.environ["QUANT_DB_HOST"] = "env-host.local"
    os.environ["QUANT_DB_USER"] = "env_user"
    cfg = tp._db_config()
    assert cfg["host"] == "env-host.local"
    assert cfg["user"] == "env_user"


def test_db_config_falls_back_to_secrets_json(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        secrets_path = os.path.join(td, "push_secrets.json")
        with open(secrets_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "quant_db_host": "json-host.local",
                    "quant_db_port": 5499,
                    "quant_db_user": "json_user",
                },
                f,
            )
        monkeypatch.setattr(tp, "_SECRETS_PATH", secrets_path)
        tp._SECRETS_CACHE.clear()
        cfg = tp._db_config()
        assert cfg["host"] == "json-host.local"
        assert cfg["port"] == 5499
        assert cfg["user"] == "json_user"


def test_db_config_uses_defaults_when_nothing_configured():
    tp._SECRETS_CACHE.clear()
    cfg = tp._db_config()
    assert cfg["host"] == "localhost"
    assert cfg["port"] == 5442


# ── health_check ───────────────────────────────────────────
def test_health_check_ok(_mock_conn):
    fake_conn, fake_cur = _mock_conn
    fake_cur.fetchone.return_value = ("trade_pool",)
    h = tp.health_check()
    assert h["ok"] is True
    assert h["trade_pool_exists"] is True


def test_health_check_fails_on_exception():
    with patch("portal.quant_mid.trade_pool.psycopg2") as m:
        m.connect.side_effect = ConnectionError("boom")
        h = tp.health_check()
        assert h["ok"] is False
        assert "boom" in h["error"]
