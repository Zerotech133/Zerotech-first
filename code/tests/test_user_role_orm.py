"""ORM/迁移一致性：create_all 建的 v2_user 角色约束必须与迁移 0016 一致（含 USER）。

Phase A 遗漏回归：ORM UserModel 仍是五角色 CHECK，sqlite create_all 下插 role='USER' 撞约束。
"""
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base


def _mk_user(user_id: str, role: str) -> orm.UserModel:
    return orm.UserModel(
        user_id=user_id, username=user_id, password_hash="x", role=role,
        is_active=True, created_at=datetime.now(UTC),
    )


def test_orm_check_constraint_includes_user_role():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(_mk_user("u-1", "USER"))
        s.commit()  # 五角色旧约束在此抛 IntegrityError（RED）
        row = s.query(orm.UserModel).filter_by(user_id="u-1").one()
        assert row.role == "USER"
    finally:
        s.close()


def test_orm_check_constraint_still_accepts_legacy_roles():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        for i, role in enumerate(["ADMIN", "TRADER", "VIEWER"]):
            s.add(_mk_user(f"u-{i}", role))
        s.commit()
        assert s.query(orm.UserModel).count() == 3
    finally:
        s.close()


def test_user_auth_version_defaults_to_one():
    """A1：会话吊销代数 auth_version 默认 1（新建用户签发/校验的初始基线）。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    try:
        s.add(_mk_user("u-version", "USER"))
        s.commit()
        row = s.query(orm.UserModel).filter_by(user_id="u-version").one()
        assert row.auth_version == 1
    finally:
        s.close()
