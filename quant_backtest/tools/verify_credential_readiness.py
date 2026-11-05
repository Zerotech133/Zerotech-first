"""N5/N6 凭证就绪自检器（只读，绝不打印任何真实值）。

用途：老田在 N6 密钥 ceremony 后把真实值填入 .env.local，本脚本校验
「8 类数据源密钥是否齐备」，仅报 present / missing / decoupled / alias，
绝不输出值本身，也不把值注入 os.environ。

执行 N5「现有源先接通」补充口径（2026-10-21）：
  .env.local 中已存在部分真实键，但键名与 N5 规范键名不同，需做别名桥接；
  其中雪球(XQ_A_TOKEN)按 WO-085 归情报板块、与量化解耦，不直接武装 N5，
  须改由情报供给层消费，故单独标 decoupled 不计入量化 N5 就绪。

武装纪律：本脚本仅声明「现有源是否已具备候选凭证」，绝不翻转
DATA_SOURCE_REAL_AUTH_ENABLED；真实武装仍须经 N6 ceremony + 老田五要素批准。

运行：
    python tools/verify_credential_readiness.py
退出码：
    0 = 8 类密钥齐备（可进入 N5 real_auth 武装）
    2 = 量化 N5 仍有缺失（列出缺哪些，但不泄露任何值）
    3 = .env.local 不存在（连模板都未填）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 把 tools/ 加入导入路径（与既有测试约定一致）
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from secret_store import load_dotenv_file, SecretStore  # noqa: E402

# 8 类数据源 ↔ .env.example 规范键名（仅名字，无真实值）
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

# 别名桥接：.env.local 中实际存在的键名 ↔ N5 规范键名。
# 仅用于「现有源先接通」声明某类已有候选凭证，绝不代表已武装 real_auth。
# 武装仍须经 N6 ceremony + 老田五要素批准（DATA_SOURCE_REAL_AUTH_ENABLED 保持 False）。
KEY_ALIASES = {
    "XTCLOUD_KEY": "XT_DATA_KEY",  # xtcloud 迅投云：.env.local 实际键名
}

# 脱钩数据源：键名存在于 .env.local，但按 WO-085 归情报板块、与量化解耦，
# 量化 N5 不得直持其凭证，须改由情报供给层消费 → 不计入量化 N5 就绪。
DECOUPLED_SOURCES = {
    "Snowball": "XQ_A_TOKEN",  # 雪球：情报部门持有，量化经情报供给层读，不直接武装
}

# 可逐步丰富的数据源（老田裁定）：路径保留、凭证后续增量补齐，
# 现阶段缺凭证属预期态，不算异常。
ENRICHABLE_SOURCES = ("东财", "股吧")

# 待裁数据源（老田裁定）：飞书性质待裁——FEISHU_APP_SECRET 是飞书开放平台
# 应用密钥，用途为消息/审批卡/回执（通知链路），并非行情数据源；是否保留在
# N5 数据源清单内待老田裁决。裁决前不计入量化 N5 缺项、单独标 [待裁]。
PENDING_RULING_SOURCES = ("飞书",)

ENV_LOCAL = Path(os.getcwd()) / ".env.local"


def _alias_present(store: SecretStore, canonical: str) -> bool:
    """规范键缺失时，检查其别名键是否已配置。"""
    alias = KEY_ALIASES.get(canonical)
    return bool(alias) and store.is_configured(alias)


def main() -> int:
    print("=== N5/N6 凭证就绪自检（仅报在/缺/脱钩/别名，不打印值）===")
    if not ENV_LOCAL.exists():
        print(f"[缺失] .env.local 不存在：{ENV_LOCAL}")
        print("        请先复制模板：cp .env.example .env.local 并在 N6 授权后填值")
        return 3

    env = load_dotenv_file(str(ENV_LOCAL))  # 仅解析，不自动注入
    store = SecretStore(env=env)

    missing: list[str] = []
    for src, key in REQUIRED_KEYS.items():
        if store.is_configured(key):
            print(f"[  在] {src:<10} {key}")
            continue
        # 脱钩数据源：存在但不计入量化 N5 就绪
        if src in DECOUPLED_SOURCES:
            actual = DECOUPLED_SOURCES[src]
            if store.is_configured(actual):
                print(
                    f"[脱钩] {src:<10} {key} ← {actual}（WO-085 归情报，量化不直接武装，须经情报供给层）"
                )
            else:
                print(f"[缺 ] {src:<10} {key}")
                missing.append(key)
            continue
        # 别名桥接：现有实际键名满足规范键名（候选，待 N6 武装）
        if _alias_present(store, key):
            alias = KEY_ALIASES[key]
            print(f"[在-别名] {src:<10} {key} ← {alias}（现有源先接通，待 N6 武装）")
            continue
        # 待裁数据源：不计入量化缺项，单独标注等裁决
        if src in PENDING_RULING_SOURCES:
            print(f"[待裁] {src:<10} {key}（飞书性质待裁：通知链路而非行情源，不计缺项）")
            continue
        # 缺失：区分「可不断丰富」（路径保留、增量补齐）与普通缺
        if src in ENRICHABLE_SOURCES:
            print(f"[缺-丰富] {src:<8} {key}（路径保留，凭证可后续增量补齐）")
        else:
            print(f"[缺 ] {src:<10} {key}")
        missing.append(key)

    print("-" * 56)
    if not missing:
        print(
            f"量化 N5 全部 {len(REQUIRED_KEYS)} 类密钥齐备 → 可进入 N5 real_auth 武装（仍须 N6 ceremony 批准）。"
        )
        return 0
    print(f"量化 N5 仍缺 {len(missing)} 类：{', '.join(missing)}")
    print("（以上仅为键名，未打印任何真实值；[在-别名]/[脱钩] 不计入缺项但均尚未武装）")
    return 2


if __name__ == "__main__":
    sys.exit(main())
