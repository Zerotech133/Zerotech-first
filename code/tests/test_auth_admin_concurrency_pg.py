"""单元 A Task A5：最后管理员保护的锁序验证。

分层（SQLite 忽略行锁，只能验证 SQL 结构与顺序；真并发只在 PostgreSQL 验证）：
- 结构测试（常驻）：断言 _mutate_access_protected 先按 id 升序锁全部 active ADMIN、
  再锁目标行——固定锁序是"两管理员并发互相降级"不死锁的前提；
- 真并发测试（@real_pg，需 REAL_PG=1 且真 PG 且已迁移到 0029）：两线程同时互相
  降级/停用，断言无死锁、恰一成功一失败(last_admin_protected)、始终保留一个 active ADMIN。
"""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.security.password import hash_password
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

# ── A5.1 结构测试：锁序（SQLite，常驻）──────────────────────────────────

def test_admin_lock_precedes_target_and_orders_by_id(monkeypatch):
    import zhice_os_v2.infrastructure.repositories.user_repository as repo_mod
    from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repo_mod, "get_session", lambda: Session())

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    repo = UserRepository()
    repo.create("a1", "a1", hash_password("p"), "ADMIN")
    repo.create("a2", "a2", hash_password("p"), "ADMIN")
    statements.clear()

    repo.update_role_protected("a1", "VIEWER", expected_auth_version=1)

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    admin_idx = next(
        (i for i, s in enumerate(selects) if "role" in s and "ORDER BY" in s.upper()),
        None,
    )
    target_idx = next(
        (i for i, s in enumerate(selects) if "user_id" in s and "ORDER BY" not in s.upper()),
        None,
    )
    assert admin_idx is not None, f"未见按序锁 ADMIN 的 SELECT：{selects}"
    assert target_idx is not None, f"未见锁目标行的 SELECT：{selects}"
    # 先锁全部 ADMIN，再锁目标（固定锁序 → 并发不死锁）
    assert admin_idx < target_idx
    # 管理员锁按主键 id 升序（稳定顺序 → 两事务在同一首行上串行化）
    admin_stmt = selects[admin_idx]
    assert "ORDER BY" in admin_stmt.upper()
    assert "v2_user.id" in admin_stmt.lower() and "ASC" in admin_stmt.upper()


# ── A5.2 真并发：两管理员互相降级/停用不死锁、不清零（PostgreSQL）──────────

def _pg_available() -> bool:
    try:
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session

        s = get_session()
        try:
            s.execute(text("select 1"))
            return True
        finally:
            s.close()
    except Exception:
        return False


@pytest.mark.real_pg
@pytest.mark.parametrize(
    ("left_action", "right_action"),
    [
        ("demote", "demote"),
        ("demote", "deactivate"),
        ("deactivate", "demote"),
        ("deactivate", "deactivate"),
    ],
)
def test_two_admins_cannot_deadlock_or_remove_every_admin(left_action, right_action):
    if not (os.getenv("REAL_PG") == "1" and _pg_available()):
        pytest.skip("需要 REAL_PG=1 且真 PostgreSQL 可用（已迁移到 0029）")

    from zhice_os_v2.infrastructure.db.session import get_session
    from zhice_os_v2.infrastructure.repositories.user_repository import UserRepository

    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    a1, a2 = f"zztest_adm_{sfx}_1", f"zztest_adm_{sfx}_2"
    now = datetime.now(UTC)

    setup = get_session()
    try:
        for uid in (a1, a2):
            setup.add(
                orm.UserModel(
                    user_id=uid, username=uid, password_hash="x", role="ADMIN",
                    is_active=True, locale="zh", created_at=now,
                )
            )
        setup.commit()
    finally:
        setup.close()

    repo = UserRepository()
    successes: list[str] = []
    failures: list[str] = []
    barrier = threading.Barrier(2)

    def worker(target_id: str, action: str) -> None:
        barrier.wait()
        try:
            if action == "demote":
                repo.update_role_protected(target_id, "VIEWER")
            else:
                repo.deactivate_protected(target_id)
            successes.append(target_id)
        except ValueError as exc:
            failures.append(str(exc))

    try:
        threads = [
            threading.Thread(target=worker, args=(a1, left_action)),
            threading.Thread(target=worker, args=(a2, right_action)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert all(not t.is_alive() for t in threads), "线程超时未结束（疑似死锁）"
        assert len(successes) == 1, f"应恰一个成功，实为 {successes}"
        assert len(failures) == 1, f"应恰一个失败，实为 {failures}"
        assert failures[0] == "auth.last_admin_protected"

        check = get_session()
        try:
            from sqlalchemy import text

            n = check.execute(
                text(
                    "SELECT count(*) FROM v2_user "
                    "WHERE role='ADMIN' AND is_active=true "
                    "AND user_id IN (:a1, :a2)"
                ),
                {"a1": a1, "a2": a2},
            ).scalar_one()
            assert n == 1, f"必须保留恰一个 active ADMIN，实为 {n}"
        finally:
            check.close()
    finally:
        cleanup = get_session()
        try:
            from sqlalchemy import text

            cleanup.execute(
                text("DELETE FROM v2_user WHERE user_id IN (:a1, :a2)"),
                {"a1": a1, "a2": a2},
            )
            cleanup.commit()
        finally:
            cleanup.close()
