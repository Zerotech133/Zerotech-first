"""可插拔数据源适配器（工位 #373，门 N5 真实通道回签）。

休眠默认策略：本模块只定义「三态」数据源接口，绝不注入真实凭证，
绝不在任何状态下发起真实网络请求（socket/HTTP）。

三态语义：
- trial     : 试用/合成数据，默认态；永不接真实通道。
- proxy     : 代收代理占位 stub；需外部配置代收代理，本态不持有任何凭证。
- real_auth : 真实授权态；需真实凭证，凭证注入须经老田在 N5 门回签 +
              N6 密钥 ceremony 批准；本类绝不持有/硬编码/读取任何真实 token/key。

门 N5 清单需摸底的 8 类数据源（名称仅作文档化常量，凭证一律以
CREDENTIAL_PLACEHOLDER 占位）：
    Tushare / Snowball(雪球) / xtcloud / 东财 / 股吧 / akshare / crypto / 飞书

本适配器默认仅 trial：真实通道授权与凭证注入须经老田在 N5 门回签 +
N6 密钥 ceremony 批准，不在本工位拍板。

依赖：仅 Python 标准库。绝不包含 pandas / numpy / requests / socket。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 常量 / 枚举
# ---------------------------------------------------------------------------

# 数据源三态：trial=试用/合成，proxy=代收代理，real_auth=真实授权
DataSourceState = ("trial", "proxy", "real_auth")

# 门 N5 清单需摸底的数据源名称（仅名字，无真实值、无凭证）
KNOWN_DATA_SOURCES = (
    "Tushare",   # 通联/Tushare
    "Snowball",  # 雪球
    "xtcloud",   # 迅投云/xtcloud
    "东财",       # 东方财富
    "股吧",       # 股吧
    "akshare",   # akshare
    "crypto",    # 加密货币行情
    "飞书",       # 飞书
)

# 凭证占位符：清单中所有真实凭证一律以该值占位，本模块绝不持有真实值。
CREDENTIAL_PLACEHOLDER = "***REDACTED***"

# 默认开关：真实通道授权与凭证注入须经老田在 N5 门回签 + N6 密钥 ceremony 批准；
# 本适配器默认仅 trial，绝不默认武装 real_auth。
DATA_SOURCE_REAL_AUTH_ENABLED = False

_DEFAULT_STATE = "trial"


# ---------------------------------------------------------------------------
# 适配器基类
# ---------------------------------------------------------------------------

class DataSourceAdapter:
    """可插拔数据源适配器基类。

    实现三态 fetch 行为，所有状态均不发起真实网络请求、不持有真实凭证。
    """

    def __init__(self, name: str, state: str = "trial") -> None:
        if state not in DataSourceState:
            raise ValueError(
                f"非法数据源状态: {state!r}，必须是 DataSourceState 之一: {DataSourceState}"
            )
        self.name = name
        self._state = state

    def get_state(self) -> str:
        """返回当前数据源状态（trial / proxy / real_auth）。"""
        return self._state

    def requires_credentials(self) -> bool:
        """是否需要真实授权凭证；仅 real_auth 态为 True。"""
        return self._state == "real_auth"

    def fetch(self, *args, **kwargs) -> dict:
        """按当前状态返回合成/占位结果，绝不发起真实请求、绝不持有真实数据。

        返回 dict：
        - trial    : {"state": "trial", "data": [], "note": "合成/空数据，未接真实通道"}
        - proxy    : {"state": "proxy", "data": None, "note": "需配置代收代理，当前为占位 stub"}
        - real_auth: {"state": "real_auth", "data": None,
                      "note": "需真实授权凭证，凭证注入须经 N5/N6 批准，本类不持有任何真实凭证"}
        """
        if self._state == "trial":
            return {
                "state": "trial",
                "data": [],
                "note": "合成/空数据，未接真实通道",
            }
        if self._state == "proxy":
            return {
                "state": "proxy",
                "data": None,
                "note": "需配置代收代理，当前为占位 stub",
            }
        # real_auth：仅占位，绝不读取/硬编码任何真实 token/key
        return {
            "state": "real_auth",
            "data": None,
            "note": "需真实授权凭证，凭证注入须经 N5/N6 批准，本类不持有任何真实凭证",
        }


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def build_adapter(name: str, state: str = "trial") -> DataSourceAdapter:
    """构造一个数据源适配器实例。

    默认 trial 态；real_auth 仅作接口占位，凭证注入须经 N5/N6 批准，
    本工厂不在此处读取任何真实凭证。
    """
    if state == "real_auth" and not DATA_SOURCE_REAL_AUTH_ENABLED:
        # 休眠默认：即便调用方传入 real_auth，默认开关关闭时也仅以占位态构建，
        # 不会真正接入真实通道。真实武装留待老田在 N5/N6 拍板。
        return DataSourceAdapter(name=name, state="trial")
    return DataSourceAdapter(name=name, state=state)
