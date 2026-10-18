"""报告 / 组合快照归档落库测试。

背景：V2 报告系统原为「现算不落库」，本套件覆盖新增的定时归档落库：
- memory 后端（conftest 默认）：验证 job 现算主路径不崩 + 后端守卫使单测不写真库（隔离）。
- 真库（pg 可用）：验证 _persist_report 幂等 upsert（隔离 market=ZZTEST，自清理）。

注意 get_session 恒连真 PG（与 persistence_backend 无关），故凡触真库的用例均 pg 守卫跳过。
"""
from __future__ import annotations

from datetime import date

import pytest


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


# ─── memory 后端行为契约 ──────────────────────────────────────────────────────


@pytest.mark.real_pg
def test_compute_and_persist_returns_payload_computed_on_memory():
    """内存后端：现算返回完整 payload，守卫使 source 保持 computed（不落真库）。"""
    if not _pg_available():
        pytest.skip("Postgres 不可用（reports 现算硬连真库），跳过")
    from zhice_os_v2.api.routers.reports import compute_and_persist

    p = compute_and_persist("CN", "morning")
    assert p["report_type"] == "morning"
    assert p["market"] == "CN"
    assert "facts" in p and "narrative" in p
    assert p["source"] == "computed"  # 守卫生效：memory 后端不落库


def test_snapshot_persist_skipped_on_memory_backend():
    """内存后端：快照落库守卫在连库前直接返回 0，保证单测不污染生产库。"""
    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    n = WorkflowService(get_db())._persist_portfolio_snapshots("CN")
    assert n == 0


# ─── 真库幂等 upsert（隔离 market，自清理）────────────────────────────────────


@pytest.mark.real_pg
def test_persist_report_idempotent_upsert():
    """同 market+type+date 重复落库只留一行，且字段被最新值覆盖（on_conflict_do_update）。"""
    if not _pg_available():
        pytest.skip("Postgres 不可用，跳过落库集成验证")
    from sqlalchemy import func

    from zhice_os_v2.api.routers.reports import _persist_report
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    mkt, d = "ZZTEST", date(2000, 1, 1)
    expected_id = "zztest_morning_2000-01-01"
    session = get_session()
    try:
        rid1 = _persist_report(session, mkt, "morning", d, {"k": 1}, "n1", "rule_template")
        rid2 = _persist_report(session, mkt, "morning", d, {"k": 2}, "n2", "ai_editor")
        assert rid1 == rid2 == expected_id
        cnt = (
            session.query(func.count(orm.DailyReportModel.id))
            .filter(orm.DailyReportModel.report_id == expected_id)
            .scalar()
        )
        assert cnt == 1  # 幂等：同 id 只一行
        row = (
            session.query(orm.DailyReportModel)
            .filter(orm.DailyReportModel.report_id == expected_id)
            .first()
        )
        assert row.model_used == "ai_editor"  # 第二次覆盖
        assert row.narrative == "n2"
        assert row.facts == {"k": 2}
    finally:
        session.query(orm.DailyReportModel).filter(
            orm.DailyReportModel.report_id == expected_id
        ).delete()
        session.commit()
        session.close()
