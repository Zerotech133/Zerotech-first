"""准备一个"未分组"的标定专用库：克隆当前库，清掉事件层。

为什么必须有这一步
------------------
临界带宽标定（``scripts/calibrate_borderline_margin.py``）要求候选配对是**全量**
事实两两比较的结果。而工作库跑过多轮聚类之后，已合并的配对不再进候选集：

    2026-11-05 实测：工作库 29 事件 / 42 成员 / 50 事实
    → 只有 8 条未分配事实参与比较 → 候选 1 对、真同事件 0 对
    → "missed 归零的最小带宽"算出 0.00（这是**没有样本**，不是"带宽可收窄到 0"）

所以在工作库上标定得到的结论不是"过时"，而是**不成立**。凡是"样本不足仍照算"
的脚本都会给出一个看起来正常的数字，这是本项目里最难发现的一类错误。

怎么做
------
不碰工作库：用 ``CREATE DATABASE ... TEMPLATE`` 克隆一份（结构与数据、含向量一并
复制），在克隆里 ``DELETE FROM osint_story_member; DELETE FROM osint_story;``。
向量不重算——这正是选模板克隆而不是重新灌语料的原因（``seed_verify_corpus.py``
不写向量，重灌还得再跑一遍 embedding）。

克隆出来的库只用于标定，**不是**生产库：任何人都不要把它接进服务。

用法
----
    python scripts/make_calibration_db.py                 # 克隆 osint → osint_calib
    python scripts/make_calibration_db.py --recreate      # 已存在则先删再克隆
    python scripts/make_calibration_db.py --dry-run       # 只报告，不建库

建好后这样标定（环境变量覆盖 ``.env`` 里的 PG_DB）::

    PG_DB=osint_calib python scripts/calibrate_borderline_margin.py

标定戳里的语料指纹是**内容哈希**，与库名无关，所以在外围库上标定得到的戳对
工作库同样有效——只要两边语料内容一致（模板克隆保证一致）。

退出码：0 成功；1 失败；3 库已存在且未加 ``--recreate``。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

from config.settings import get_settings  # noqa: E402

#: 事件层表：清掉这些即回到"未分组"。顺序按外键依赖，成员先于事件。
_GROUPING_TABLES: tuple[str, ...] = ("osint_story_member", "osint_story")

#: 标定只关心这几张表；克隆后逐张核对行数，避免"克隆成功但数据没跟过来"。
_VERIFY_TABLES: tuple[str, ...] = (
    "osint_knowledge_unit",
    "osint_story",
    "osint_story_member",
)


def _admin_url() -> str:
    """连到 ``postgres`` 维护库：``CREATE DATABASE`` 不能在目标库自身里执行。"""
    s = get_settings()
    return f"postgresql+psycopg://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/postgres"


def _db_url(name: str) -> str:
    s = get_settings()
    return f"postgresql+psycopg://{s.pg_user}:{s.pg_password}@{s.pg_host}:{s.pg_port}/{name}"


def _count(engine, table: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def _exists(engine, name: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar()
        )


def prepare(source: str, target: str, *, recreate: bool, dry_run: bool) -> int:
    if source == target:
        print("[ERROR] 源库与目标库同名，拒绝执行（会清掉工作库的事件层）", file=sys.stderr)
        return 1

    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT", future=True)
    try:
        exists = _exists(admin, target)
        if exists and not recreate:
            print(
                f"[ERROR] 目标库 {target} 已存在。加 --recreate 会先删再建——"
                "确认它只是标定库、里面没有需要保留的东西。",
                file=sys.stderr,
            )
            return 3
        if dry_run:
            print(f"[DRY] 将{'删除并重建' if exists else '创建'} {target}（源 {source}）")
            return 0
        if exists:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{target}"'))
        with admin.connect() as conn:
            # 模板克隆要求源库无活动连接；有常驻连接时会报 "being accessed by
            # other users"，这是环境问题而不是逻辑问题，原样报出即可。
            conn.execute(text(f'CREATE DATABASE "{target}" TEMPLATE "{source}"'))
    finally:
        admin.dispose()

    calib = create_engine(_db_url(target), future=True)
    try:
        before = {t: _count(calib, t) for t in _VERIFY_TABLES}
        with calib.begin() as conn:
            for table in _GROUPING_TABLES:
                conn.execute(text(f"DELETE FROM {table}"))
        after = {t: _count(calib, t) for t in _VERIFY_TABLES}
    finally:
        calib.dispose()

    print(f"[OK] {target} 就绪")
    for table in _VERIFY_TABLES:
        print(f"     {table}: {before[table]} → {after[table]}")
    if after["osint_knowledge_unit"] != before["osint_knowledge_unit"] or not after[
        "osint_knowledge_unit"
    ]:
        print(
            "[ERROR] 事实数在克隆里对不上（或为 0），标定会得到空结论。"
            "检查克隆是否真正完成。",
            file=sys.stderr,
        )
        return 1
    print(f"\n标定命令：PG_DB={target} python scripts/calibrate_borderline_margin.py")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="克隆一个未分组库用于标定")
    parser.add_argument("--source", default=settings.pg_db, help="源库（默认当前 PG_DB）")
    parser.add_argument("--target", default=f"{settings.pg_db}_calib", help="目标库名")
    parser.add_argument("--recreate", action="store_true", help="目标库已存在时先删再建")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不动库")
    args = parser.parse_args(argv)

    try:
        return prepare(
            args.source, args.target, recreate=args.recreate, dry_run=args.dry_run
        )
    except Exception as exc:  # 连不上库、权限不足、模板被占用
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
