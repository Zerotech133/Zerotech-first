# -*- coding: utf-8 -*-
"""SecretStore / load_dotenv_file 单元测试（工位 #371 · 门 N6）。

约定：把 tools/ 目录注入 sys.path，直接 `import secret_store`。
(QBT = parents[1] 即 tools/ 目录；test 位于 tools/tests/ 下。)
"""

import os
import sys
import tempfile

from pathlib import Path

# --- sys.path 注入（参考 tools/tests 约定） ---
QBT = Path(__file__).resolve().parents[1]  # tools/tests -> tools/
if str(QBT) not in sys.path:
    sys.path.insert(0, str(QBT))

from secret_store import (  # noqa: E402
    SecretStore,
    MissingSecretError,
    load_dotenv_file,
)


def test_load_dotenv_file_parses():
    content = (
        "TUSHARE_TOKEN=tok_abc123\n"
        "XTCLOUD_KEY=key_xyz\n"
        "# this is a comment\n"
        "SNOWBALL_COOKIE='cookie_with_quotes'\n"
        "EMPTY=\n"
        "\n"
    )
    fd, path = tempfile.mkstemp(suffix=".env")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        d = load_dotenv_file(path)
        assert d["TUSHARE_TOKEN"] == "tok_abc123"
        assert d["XTCLOUD_KEY"] == "key_xyz"
        assert d["SNOWBALL_COOKIE"] == "cookie_with_quotes"  # 去引号
        assert "EMPTY" in d and d["EMPTY"] == ""  # 空值保留键
        assert "# this is a comment" not in d  # 注释跳过
    finally:
        os.unlink(path)


def test_get_require_is_configured():
    env = {"TUSHARE_TOKEN": "tok_secret_value_12345"}
    store = SecretStore(env=env)
    assert store.get("TUSHARE_TOKEN") == "tok_secret_value_12345"
    assert store.get("MISSING") is None
    assert store.is_configured("TUSHARE_TOKEN") is True
    assert store.is_configured("MISSING") is False
    assert store.require("TUSHARE_TOKEN") == "tok_secret_value_12345"


def test_require_missing_raises():
    store = SecretStore(env={})
    raised = False
    try:
        store.require("NOPE")
    except MissingSecretError as exc:
        raised = True
        assert exc.name == "NOPE"
        assert "NOPE" in str(exc)
    assert raised, "require 缺失应抛 MissingSecretError"


def test_redacted_no_leak():
    secret = "supersecret_token_9988"  # 长度 > 4，末 4 位不足以还原
    store = SecretStore(env={"MY_TOKEN": secret})
    r = store.redacted("MY_TOKEN")
    # 关键红线：返回值不得包含完整测试密钥串。
    assert secret not in r
    # 且必须包含脱敏标记。
    assert "***" in r or "<unset>" in r


def test_redacted_unset():
    store = SecretStore(env={})
    assert store.redacted("UNSET_THING") == "<unset>"


def test_repr_no_leak():
    secret = "another_super_secret_5555"
    store = SecretStore(env={"TUSHARE_TOKEN": secret})
    r = repr(store)
    assert secret not in r  # __repr__ 绝不泄露真实值
    assert "TUSHARE_TOKEN" in r  # 仅暴露名
