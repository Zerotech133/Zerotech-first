"""数据源适配器单元测试（工位 #373，门 N5 真实通道回签）。

验证三态行为：trial 返回空数据、proxy/real_auth 仅占位、real_auth 需凭证、
默认开关关闭、KNOWN_DATA_SOURCES 含 8 类名字。绝不涉及真实凭证或网络请求。
"""
import sys
from pathlib import Path

QBT = Path(__file__).resolve().parents[1]
for _p in (str(QBT), str(QBT / "strategy_layer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from strategy_layer.data_source_adapter import (  # noqa: E402
    DataSourceAdapter,
    build_adapter,
    DataSourceState,
    DATA_SOURCE_REAL_AUTH_ENABLED,
    KNOWN_DATA_SOURCES,
    CREDENTIAL_PLACEHOLDER,
)


def test_datasource_state_constants():
    """三态常量固定为 trial/proxy/real_auth。"""
    assert DataSourceState == ("trial", "proxy", "real_auth")


def test_trial_state_fetch_empty():
    """trial 态 fetch 返回空 data 且 state=='trial'。"""
    a = DataSourceAdapter("Tushare", state="trial")
    assert a.get_state() == "trial"
    r = a.fetch()
    assert r["state"] == "trial"
    assert r["data"] == []
    assert "未接真实通道" in r["note"]
    assert a.requires_credentials() is False


def test_proxy_state_fetch_placeholder():
    """proxy 态 state=='proxy' 且 data 为 None（占位 stub）。"""
    a = DataSourceAdapter("Snowball", state="proxy")
    assert a.get_state() == "proxy"
    r = a.fetch()
    assert r["state"] == "proxy"
    assert r["data"] is None
    assert "代收代理" in r["note"]
    assert a.requires_credentials() is False


def test_real_auth_state_no_real_data():
    """real_auth 态 requires_credentials()==True 且 fetch 不持有真实数据。"""
    a = DataSourceAdapter("东财", state="real_auth")
    assert a.get_state() == "real_auth"
    assert a.requires_credentials() is True
    r = a.fetch()
    assert r["state"] == "real_auth"
    assert r["data"] is None
    assert "需真实授权凭证" in r["note"]
    assert "N5/N6" in r["note"]


def test_illegal_state_rejected():
    """非法状态应抛 ValueError。"""
    import pytest
    with pytest.raises(ValueError):
        DataSourceAdapter("xtcloud", state="bogus")


def test_real_auth_enabled_default_false():
    """默认真实授权开关为 False（休眠默认）。"""
    assert DATA_SOURCE_REAL_AUTH_ENABLED is False


def test_known_data_sources_eight():
    """KNOWN_DATA_SOURCES 含 8 类数据源名字。"""
    assert len(KNOWN_DATA_SOURCES) == 8
    for name in ("Tushare", "Snowball", "xtcloud", "东财", "股吧", "akshare", "crypto", "飞书"):
        assert name in KNOWN_DATA_SOURCES


def test_credential_placeholder_only():
    """凭证占位符固定，绝不含真实值。"""
    assert CREDENTIAL_PLACEHOLDER == "***REDACTED***"


def test_build_adapter_default_trial():
    """工厂默认构建 trial 态适配器。"""
    a = build_adapter("akshare")
    assert a.get_state() == "trial"
    assert a.fetch()["data"] == []


def test_build_adapter_real_auth_downgraded_when_disabled():
    """默认开关关闭时，即便请求 real_auth 也被降级为 trial（不武装真实通道）。"""
    a = build_adapter("飞书", state="real_auth")
    assert a.get_state() == "trial"


def test_build_adapter_proxy_passthrough():
    """proxy 请求透传为 proxy 占位态。"""
    a = build_adapter("crypto", state="proxy")
    assert a.get_state() == "proxy"
    assert a.fetch()["data"] is None
