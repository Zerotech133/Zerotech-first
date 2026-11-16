# -*- coding: utf-8 -*-
"""ingest_hk_from_tdx 的 API Key 鉴权测试（2026-11-16 强制鉴权后）。

背景：TDX 服务 2026-11-16 起 `auth_mode=enforce`，无凭证 `GET /api/*` 一律 401。
本脚本原为免鉴权调用，必须补 `X-API-Key`。

三条：
  A  环境变量 TDX_API_KEY 优先
  B  缺 Key 时立刻 SystemExit（而不是让每个请求静默 401 后被当成「当天没数据」）
  C  _fetch_day 确实把 Key 放进请求头；401 抛明确错误而非 raise_for_status 的泛化异常
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
SCRIPT = CODE / "scripts" / "ingest_hk_from_tdx.py"


def _load():
    spec = importlib.util.spec_from_file_location("_hk_tdx_auth", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    sys.modules["_hk_tdx_auth"] = m
    spec.loader.exec_module(m)
    return m


def test_api_key_from_env(monkeypatch):
    """A：环境变量优先。"""
    monkeypatch.setenv("TDX_API_KEY", "tdx_TESTKEY_000")
    m = _load()
    assert m._api_key() == "tdx_TESTKEY_000"


def test_api_key_missing_raises(monkeypatch, tmp_path):
    """B：缺 Key 必须立刻失败，不能静默继续。"""
    monkeypatch.delenv("TDX_API_KEY", raising=False)
    m = _load()

    # 把 _api_key 里的 .env.local 查找路径指到空目录（模拟仓库里没配）
    monkeypatch.setattr(m.Path, "is_file", lambda self: False, raising=False)

    with pytest.raises(SystemExit, match="TDX_API_KEY"):
        m._api_key()


def test_fetch_day_sends_key_header(monkeypatch):
    """C1：_fetch_day 必须把 X-API-Key 放进请求头。"""
    monkeypatch.setenv("TDX_API_KEY", "tdx_TESTKEY_111")
    m = _load()

    seen = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"columns": ["ts_code", "trade_date"], "rows": [["00001.HK", "20261116"]],
                    "total": 1}

    def _fake_get(url, params=None, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        return _Resp()

    import requests as _rq
    monkeypatch.setattr(_rq, "get", _fake_get)

    rows = m._fetch_day("20261116")
    assert seen["headers"] == {"X-API-Key": "tdx_TESTKEY_111"}, seen["headers"]
    assert len(rows) == 1


def test_fetch_day_401_raises_clear_error(monkeypatch):
    """C2：401 要抛明确错误（说明是 Key 问题），不能只给 raise_for_status 的泛化异常。"""
    monkeypatch.setenv("TDX_API_KEY", "tdx_WRONG")
    m = _load()

    class _Resp401:
        status_code = 401

        def raise_for_status(self):
            raise AssertionError("不应走到 raise_for_status —— 401 应被提前拦截")

        def json(self):
            return {}

    import requests as _rq
    monkeypatch.setattr(_rq, "get", lambda *a, **k: _Resp401())

    with pytest.raises(RuntimeError, match="401 unauthorized"):
        m._fetch_day("20261116")


def test_fetch_day_403_raises_scope_error(monkeypatch):
    """C3：403 提示权限不足（read scope）。"""
    monkeypatch.setenv("TDX_API_KEY", "tdx_NOSCOPE")
    m = _load()

    class _Resp403:
        status_code = 403

        def raise_for_status(self):
            raise AssertionError("不应走到 raise_for_status")

        def json(self):
            return {}

    import requests as _rq
    monkeypatch.setattr(_rq, "get", lambda *a, **k: _Resp403())

    with pytest.raises(RuntimeError, match="403 forbidden"):
        m._fetch_day("20261116")
