"""N5/N6 凭证就绪自检器（只读，绝不打印任何真实值）。

用途：老田在 N6 密钥 ceremony 后把真实值填入 .env.local，本脚本校验
「8 类数据源密钥是否齐备」，仅报 present / missing，绝不输出值本身，
也不把值注入 os.environ。

运行：
    python tools/verify_credential_readiness.py
退出码：
    0 = 8 类密钥齐备（可进入 N5 real_auth 武装）
    2 = 仍有缺失（列出缺哪些，但不泄露任何值）
    3 = .env.local 不存在（连模板都未填）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 tools/ 加入导入路径（与既有测试约定一致）
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from secret_store import load_dotenv_file, SecretStore, MissingSecretError  # noqa: E402

# 8 类数据源 ↔ .env.example 键名（仅名字，无真实值）
REQUIRED_KEYS = {
    "Tushare": "TUSHARE_TOKEN",
    "xtcloud": "XTCLOUD_KEY",
    "Snowball": "SNOWBALL_COOKIE",
    "东财": "EASTMONEY_TOKEN",
    "股吧": "GUBA_COOKIE",
    "akshare": "AKSHARE_TOKEN",
    "crypto": "CRYPTO_API_KEY",
    "飞书": "FEISHU_APP_SECRET",
}

ENV_LOCAL = Path(os.getcwd()) / ".env.local"


def main() -> int:
    print("=== N5/N6 凭证就绪自检（仅报在/缺，不打印值）===")
    if not ENV_LOCAL.exists():
        print(f"[缺失] .env.local 不存在：{ENV_LOCAL}")
        print(f"        请先复制模板：cp .env.example .env.local 并在 N6 授权后填值")
        return 3

    env = load_dotenv_file(str(ENV_LOCAL))  # 仅解析，不自动注入
    store = SecretStore(env=env)

    missing: list[str] = []
    for src, key in REQUIRED_KEYS.items():
        try:
            store.require(key)  # 缺失/为空会抛 MissingSecretError（仅曝名）
            print(f"[  在] {src:<10} {key}")
        except MissingSecretError:
            print(f"[缺 ] {src:<10} {key}")
            missing.append(key)

    print("-" * 40)
    if not missing:
        print(f"全部 {len(REQUIRED_KEYS)} 类密钥齐备 → 可进入 N5 real_auth 武装（仍须 N6 ceremony 批准）。")
        return 0
    print(f"仍缺 {len(missing)} 类：{', '.join(missing)}")
    print("（以上仅为键名，未打印任何真实值）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
