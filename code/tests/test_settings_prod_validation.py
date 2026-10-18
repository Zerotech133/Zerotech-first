"""生产环境 fail-fast 校验回归（validate_production_secrets 扩展项）。

上线前审计（就绪+安全子代理）新增的三条硬断言：memory 后端丢数据、DB 默认弱口令、
启用支付时空 token 可伪造回调。这些只在 app_env=prod 触发，非 prod 零影响。
"""
from __future__ import annotations

import pytest

from zhice_os_v2.config.settings import AppSettings

_OK = dict(
    app_env="prod",
    secret_key="X" * 32,
    admin_password="StrongPass9",
    persistence_backend="sql",
    db_main_url="postgresql://u:strongpw@h:5432/d",
)


def _mk(**over):
    kw = dict(_OK)
    kw.update(over)
    return AppSettings(**kw)


def test_prod_rejects_memory_backend():
    with pytest.raises(ValueError, match="PERSISTENCE_BACKEND"):
        _mk(persistence_backend="memory")


def test_prod_rejects_default_db_password():
    with pytest.raises(ValueError, match="DB_MAIN_URL"):
        _mk(db_main_url="postgresql://zhice:zhice_pass@localhost:5432/zhice_os_v2")


def test_prod_rejects_payment_enabled_without_token():
    with pytest.raises(ValueError, match="BEPUSDT_API_TOKEN"):
        _mk(bepusdt_base_url="http://127.0.0.1:8197", bepusdt_api_token="")


def test_prod_rejects_non_https_notify_when_payment_enabled():
    with pytest.raises(ValueError, match="https"):
        _mk(
            bepusdt_base_url="http://127.0.0.1:8197",
            bepusdt_api_token="tok_abcdefgh",
            bepusdt_notify_base_url="http://insecure.example/notify",
        )


def test_prod_allows_internal_http_notify():
    # 内网目标（docker 服务名）允许明文回调——与 BEpusdt 同容器网络。
    s = _mk(
        bepusdt_base_url="http://bepusdt:8080",
        bepusdt_api_token="tok_abcdefgh",
        bepusdt_notify_base_url="http://api:8002",
    )
    assert s.bepusdt_notify_base_url == "http://api:8002"


def test_prod_compliant_config_passes():
    s = _mk()
    assert s.app_env == "prod"
    # 支付合规组合也应通过
    s2 = _mk(
        bepusdt_base_url="http://127.0.0.1:8197",
        bepusdt_api_token="tok_abcdefgh",
        bepusdt_notify_base_url="https://pay.example/notify",
    )
    assert s2.bepusdt_api_token == "tok_abcdefgh"


def test_non_prod_ignores_new_assertions():
    # local-dev 下 memory + 默认弱口令 + 空支付 token 均不拦截。
    s = AppSettings(app_env="local-dev", persistence_backend="memory")
    assert s.persistence_backend == "memory"
