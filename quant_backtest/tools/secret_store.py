# -*- coding: utf-8 -*-
"""受控密钥抽象（工位 #371 · 门 N6 生产密钥 ceremony）。

设计原则 —— 休眠默认（dormant by default）：
  * 本模块绝不硬编码、绝不打印任何真实密钥或 token 的真实值。
  * 任何 __repr__ / 日志方法只输出脱敏信息，绝不泄露明文。
  * load_dotenv_file 仅解析、返回 dict，绝不打印值、绝不自动写入
    os.environ、绝不在模块导入时自动调用。

真实密钥注入路径（须经 N6 密钥 ceremony + 老田五要素批准）：
  由用户在 N6 门通过后，显式调用 load_dotenv_file(受控路径) 取得 dict，
  再将 dict 注入 SecretStore(env=...) 使用。本工位不替用户注入任何真实密钥，
  激活开关（显式调用、实际注入）留待老田在 N6 拍板。

依赖：仅 stdlib（os / typing）。绝不 import pandas / numpy。
"""

from __future__ import annotations

import os
from typing import Dict, Optional


# 文档化密钥清单：仅名字，无值。来自 N6 密钥登记册。
KNOWN_SECRET_KEYS = (
    "TUSHARE_TOKEN",
    "XTCLOUD_KEY",
    "SNOWBALL_COOKIE",
    # 占位：其余生产密钥名按 N6 登记册在此追加（仅名字）。
)


class MissingSecretError(Exception):
    """请求一个未配置（缺失）的密钥时抛出。不含真实密钥值。"""

    def __init__(self, name: str):
        self.name = name
        # 错误信息同样只暴露密钥名，绝不暴露值。
        super().__init__(f"required secret not configured: {name}")


def _redact_value(value: str) -> str:
    """对单个真实值做脱敏，仅保留末 4 位。绝不返回完整值。"""
    if value == "":
        return "<unset>"
    tail = value[-4:]
    return f"***{tail}"


class SecretStore:
    """受控密钥读取抽象。

    默认从 os.environ 读取；允许注入一个 dict 作为 env，便于测试，
    且不会触碰真实环境。
    """

    def __init__(self, env: Optional[Dict[str, str]] = None):
        # 注入优先；否则回退到真实环境 os.environ。
        # 注意：本类只『读』环境，绝不『写』环境、绝不打印值。
        self._env: Dict[str, str] = dict(env) if env is not None else dict(os.environ)

    def get(self, name: str) -> Optional[str]:
        """返回密钥或 None。绝不泄露值本身以外的信息。"""
        val = self._env.get(name)
        return val if val is not None else None

    def require(self, name: str) -> str:
        """缺失时抛 MissingSecretError（自定义异常，仅含密钥名）。"""
        val = self._env.get(name)
        if val is None:
            raise MissingSecretError(name)
        return val

    def is_configured(self, name: str) -> bool:
        """是否已配置（存在且非空）。"""
        val = self._env.get(name)
        return val is not None and val != ""

    def redacted(self, name: str) -> str:
        """返回脱敏串，如 "***abcd"（仅保留末 4 位）或 "<unset>"。

        严禁返回真实完整值。
        """
        val = self._env.get(name)
        if val is None or val == "":
            return "<unset>"
        # 仅保留末 4 位，前缀一律统一为 ***，绝不暴露密钥名派生以外的内容。
        return _redact_value(val)

    def __repr__(self) -> str:
        """只读名、不读值。列出已配置密钥名（脱敏状态），绝不输出值。"""
        configured = [
            k for k in self._env if k in KNOWN_SECRET_KEYS and self.is_configured(k)
        ]
        return (
            f"SecretStore(configured_known_keys={configured!r}, "
            f"known_keys={list(KNOWN_SECRET_KEYS)})"
        )

    # 日志友好方法：同样只输出脱敏信息。
    def describe(self, name: str) -> str:
        """用于日志：只说明是否配置、脱敏尾段，绝不泄露完整值。"""
        if not self.is_configured(name):
            return f"{name}=<unset>"
        return f"{name}={self.redacted(name)}"


def load_dotenv_file(path: str) -> Dict[str, str]:
    """读取 .env 风格文件（每行 KEY=VALUE），返回 {key: value}。

    休眠默认：
      * 仅解析、返回 dict；
      * 绝不打印任何值；
      * 绝不自动写入 os.environ；
      * 绝不在模块导入时自动调用。

    真实密钥注入须经 N6 密钥 ceremony + 老田五要素批准：由用户显式调用本函数，
    并传入受控路径（如由老田在 N6 门提供的 .env 文件），拿到 dict 后再注入
    SecretStore(env=...)。本工位不提供自动注入/激活。

    解析规则：跳过空行与 # 注释行；按首个 '=' 分割；去除首尾空白；
    去除成对引号（' 或 "）。
    """
    result: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
                value = value[1:-1]
            result[key] = value
    return result
