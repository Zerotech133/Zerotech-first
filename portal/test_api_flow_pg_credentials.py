# -*- coding: utf-8 -*-
"""P1-02 回归：PG 凭据单一来源 + 缺凭据 fail-closed。

覆盖：
- jev_common 无 env/file 时不回落默认口令；
- api_flow 只从 jev_common.DB_DSN 取口令，缺失时抛 RuntimeError；
- 源码中不再出现共享默认口令字面量或旧常量。
"""

from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import api_flow  # noqa: E402


class _FakeJc:
    def __init__(self, password=""):
        self.DB_DSN = {
            "host": "127.0.0.1",
            "port": 5446,
            "user": "zhice",
            "password": password,
            "dbname": "zhice_os_v2",
        }


def test_missing_password_fails_closed(monkeypatch):
    monkeypatch.setattr(api_flow, "_jev_common", lambda: _FakeJc(""))
    with pytest.raises(RuntimeError, match="PG 密码未配置"):
        api_flow._pg_connect_kwargs()


def test_missing_jev_common_fails_closed(monkeypatch):
    monkeypatch.setattr(api_flow, "_jev_common", lambda: None)
    with pytest.raises(RuntimeError, match="jev_common 导入失败"):
        api_flow._pg_connect_kwargs()


def test_password_comes_from_jev_common(monkeypatch):
    monkeypatch.setattr(api_flow, "_jev_common", lambda: _FakeJc("from-jev-common"))
    kw = api_flow._pg_connect_kwargs()
    assert kw["password"] == "from-jev-common"
    assert kw["host"] == "127.0.0.1"
    assert kw["dbname"] == "zhice_os_v2"


def test_source_has_no_default_password():
    root = Path(api_flow.__file__).resolve().parents[1]
    for path in (
        Path(api_flow.__file__),
        root / "jev-eval" / "jev_common.py",
    ):
        src = path.read_text(encoding="utf-8")
        assert "zhice_pass" not in src, "%s 仍含默认口令" % path
        assert "_PG_DEFAULT_PASSWORD" not in src, "%s 仍含旧默认常量" % path
