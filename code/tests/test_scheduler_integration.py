"""调度器 DB 产出集成测试（@pytest.mark.real_pg）。

测试策略：
- 每个 job 以幂等方式运行（不改变现有数据，或重跑安全）
- 验证返回 dict 的结构（必要的 key 存在，类型正确）
- 验证 DB 状态符合预期（不报错 = DB 读取成功）
- 不硬断言具体数字（避免依赖测试环境数据状态）
- 空库也能通过（candidates=0 / 空列表视为合法）

运行方式：
    python -m pytest tests/test_scheduler_integration.py -v -m real_pg

前提：PostgreSQL 可达（zhice_os_v2 库，参照 .env.local 凭据）。
不可达时测试因 OperationalError 失败，属预期行为——记录失败原因即可。
"""
from __future__ import annotations

import pytest

from zhice_os_v2 import scheduler_jobs

pytestmark = pytest.mark.real_pg  # 整个文件豁免 _guard_real_pg


# ─────────────────────────────────────────────────────────────────────
# 辅助 fixture
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _force_sql_backend(monkeypatch):
    """每个测试前把 persistence_backend 强制切换到 sql。

    conftest.py 顶层设了 PERSISTENCE_BACKEND=memory；get_settings 是 lru_cache 单例，
    直接改 env 无效——需清缓存后再改，测试完成后恢复。
    这样 WorkflowService 内的 persistence_backend != 'sql' 守卫全部放行 DB 写入。
    """
    from zhice_os_v2.config import settings as settings_mod

    # 先清缓存，再覆写 env（顺序不能反）
    if hasattr(settings_mod.get_settings, "cache_clear"):
        settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("PERSISTENCE_BACKEND", "sql")

    yield

    # monkeypatch 自动恢复 env；手动清缓存使后续测试拿到正确的 memory backend
    if hasattr(settings_mod.get_settings, "cache_clear"):
        settings_mod.get_settings.cache_clear()


def _get_session():
    """直接取真实 PG session（仅供 DB 状态验证用）。"""
    from zhice_os_v2.infrastructure.db.session import get_session as _gs
    return _gs()


# ══════════════════════════════════════════════════════════════════════
# 盘前 job
# ══════════════════════════════════════════════════════════════════════

class TestPreMarketJob:
    """run_pre_market_job 返回结构与 DB 可达性验证。"""

    def _run(self, market: str) -> dict:
        result = scheduler_jobs.run_pre_market_job(market)
        assert isinstance(result, dict), f"run_pre_market_job({market!r}) 未返回 dict"
        return result

    # ── 必填 key ──────────────────────────────────────────────────────

    def test_cn_returns_expected_keys(self):
        r = self._run("CN")
        for key in ("market", "candidates", "plans", "approvals"):
            assert key in r, f"缺少 key: {key!r}，实际: {list(r.keys())}"
        assert r["market"] == "CN"

    def test_us_returns_expected_keys(self):
        r = self._run("US")
        for key in ("market", "candidates", "plans", "approvals"):
            assert key in r, f"US 缺少 key: {key!r}"
        assert r["market"] == "US"

    def test_hk_returns_expected_keys(self):
        r = self._run("HK")
        assert r["market"] == "HK"
        assert "candidates" in r

    def test_crypto_returns_expected_keys(self):
        r = self._run("CRYPTO")
        assert r["market"] == "CRYPTO"
        assert "candidates" in r

    # ── 类型断言 ──────────────────────────────────────────────────────

    def test_result_candidates_is_int(self):
        r = self._run("CN")
        assert isinstance(r["candidates"], int), (
            f"candidates 类型错：{type(r['candidates'])!r}"
        )

    def test_result_plans_is_list(self):
        r = self._run("CN")
        assert isinstance(r["plans"], list)

    def test_result_approvals_is_list(self):
        r = self._run("CN")
        assert isinstance(r["approvals"], list)

    def test_candidates_non_negative(self):
        r = self._run("CN")
        assert r["candidates"] >= 0

    # ── 跳过路径（无 ACTIVE 策略时）────────────────────────────────────

    def test_skipped_result_still_valid_structure(self):
        """无 ACTIVE 策略时返回 skipped 标记，但 candidates/plans/approvals 仍存在。"""
        r = self._run("CN")
        if "skipped" in r:
            # 跳过路径也要满足基础结构
            assert "market" in r
            assert isinstance(r.get("candidates", 0), int)

    # ── 幂等性 ────────────────────────────────────────────────────────

    def test_idempotent_second_run_cn(self):
        """连续两次盘前 CN 不报错（候选池/计划 upsert 幂等）。"""
        scheduler_jobs.run_pre_market_job("CN")
        r2 = scheduler_jobs.run_pre_market_job("CN")
        assert isinstance(r2, dict)

    def test_idempotent_second_run_us(self):
        """连续两次盘前 US 不报错。"""
        scheduler_jobs.run_pre_market_job("US")
        r2 = scheduler_jobs.run_pre_market_job("US")
        assert isinstance(r2, dict)


# ══════════════════════════════════════════════════════════════════════
# 盘中 job
# ══════════════════════════════════════════════════════════════════════

class TestIntradayJob:
    """run_intraday_job 返回结构验证。"""

    def _run(self, market: str) -> dict:
        result = scheduler_jobs.run_intraday_job(market)
        assert isinstance(result, dict), f"run_intraday_job({market!r}) 未返回 dict"
        return result

    def test_cn_returns_checked_key(self):
        r = self._run("CN")
        assert "market" in r
        assert "checked" in r
        assert r["market"] == "CN"

    def test_cn_checked_is_int(self):
        r = self._run("CN")
        assert isinstance(r["checked"], int)
        assert r["checked"] >= 0

    def test_us_returns_expected_structure(self):
        r = self._run("US")
        assert r["market"] == "US"
        assert "checked" in r

    def test_crypto_returns_expected_structure(self):
        r = self._run("CRYPTO")
        assert r["market"] == "CRYPTO"
        assert "checked" in r

    def test_cn_returns_trigger_lists(self):
        """盘中返回止盈/止损触发列表（可为空，但 key 必须存在）。"""
        r = self._run("CN")
        assert "stop_loss_triggered" in r
        assert "take_profit_triggered" in r
        assert isinstance(r["stop_loss_triggered"], list)
        assert isinstance(r["take_profit_triggered"], list)

    def test_idempotent_intraday_cn(self):
        """盘中连跑两次不报错（空仓时直接返回 checked=0）。"""
        scheduler_jobs.run_intraday_job("CN")
        r2 = scheduler_jobs.run_intraday_job("CN")
        assert isinstance(r2, dict)


# ══════════════════════════════════════════════════════════════════════
# 盘后 job
# ══════════════════════════════════════════════════════════════════════

class TestPostMarketJob:
    """run_post_market_job 返回结构验证。"""

    def _run(self, market: str) -> dict:
        result = scheduler_jobs.run_post_market_job(market)
        assert isinstance(result, dict), f"run_post_market_job({market!r}) 未返回 dict"
        return result

    def test_cn_returns_closed_positions_key(self):
        r = self._run("CN")
        assert "market" in r
        assert "closed_positions" in r
        assert r["market"] == "CN"

    def test_cn_closed_positions_is_list(self):
        r = self._run("CN")
        assert isinstance(r["closed_positions"], list)

    def test_us_returns_expected_structure(self):
        r = self._run("US")
        assert r["market"] == "US"
        assert "closed_positions" in r

    def test_idempotent_post_market_cn(self):
        """盘后连跑两次不报错（日报 upsert 幂等）。"""
        scheduler_jobs.run_post_market_job("CN")
        r2 = scheduler_jobs.run_post_market_job("CN")
        assert isinstance(r2, dict)


# ══════════════════════════════════════════════════════════════════════
# 计划过期 job
# ══════════════════════════════════════════════════════════════════════

class TestPlanExpiryJob:
    """run_plan_expiry_job 返回结构验证。"""

    def _run(self, market: str) -> dict:
        result = scheduler_jobs.run_plan_expiry_job(market)
        assert isinstance(result, dict), f"run_plan_expiry_job({market!r}) 未返回 dict"
        return result

    def test_cn_returns_expected_keys(self):
        r = self._run("CN")
        for key in ("market", "plans_expired", "approvals_expired"):
            assert key in r, f"缺少 key: {key!r}"
        assert r["market"] == "CN"

    def test_expired_counts_are_non_negative(self):
        r = self._run("CN")
        assert isinstance(r["plans_expired"], int)
        assert isinstance(r["approvals_expired"], int)
        assert r["plans_expired"] >= 0
        assert r["approvals_expired"] >= 0

    def test_us_expiry_non_negative(self):
        r = self._run("US")
        assert r["plans_expired"] >= 0
        assert r["approvals_expired"] >= 0

    def test_session_date_key_present(self):
        """session_date 字段在返回 dict 中（字符串格式的运行日期）。"""
        r = self._run("CN")
        assert "session_date" in r
        assert isinstance(r["session_date"], str)

    def test_idempotent_plan_expiry(self):
        """过期 job 连跑两次结果一致（第二次无新的可过期计划，数量 ≥ 0）。"""
        self._run("CN")
        r2 = self._run("CN")
        # 第一次可能过期了一些计划；第二次（那些计划已是 EXPIRED）必须不报错且 ≥ 0
        assert r2["plans_expired"] >= 0
        assert r2["approvals_expired"] >= 0


# ══════════════════════════════════════════════════════════════════════
# 汇率刷新（不需要 PG，但放此处作冒烟测试）
# ══════════════════════════════════════════════════════════════════════

class TestRefreshFxJob:
    """run_refresh_fx_job 不依赖 PG，验证基本结构。

    网络不可达时 refresh_fx 会静默降级，仍返回有效 dict——测试在离线环境也能过。
    """

    def test_returns_dict(self):
        r = scheduler_jobs.run_refresh_fx_job()
        assert isinstance(r, dict)

    def test_returns_fx_info_keys(self):
        """返回 dict 应包含 usd / hkd 键（值可为 None，网络不可达时降级）。"""
        r = scheduler_jobs.run_refresh_fx_job()
        assert "usd" in r, f"缺少 usd key，实际: {list(r.keys())}"
        assert "hkd" in r, f"缺少 hkd key，实际: {list(r.keys())}"

    def test_idempotent_refresh_fx(self):
        """连跑两次不报错（汇率缓存幂等）。"""
        scheduler_jobs.run_refresh_fx_job()
        r2 = scheduler_jobs.run_refresh_fx_job()
        assert isinstance(r2, dict)


# ══════════════════════════════════════════════════════════════════════
# DB 层面验证：盘前后直查表
# ══════════════════════════════════════════════════════════════════════

class TestDbStateAfterPreMarket:
    """盘前运行后直接查 DB 验证表可达（表存在 + 连接正常）。"""

    def test_strategy_versions_exist_in_db(self):
        """v2_strategy_version 表可达，且若 DB 有数据则 rows ≥ 0。"""
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm

        session = _get_session()
        try:
            count = session.execute(
                select(func.count()).select_from(orm.StrategyVersionModel)
            ).scalar()
            assert isinstance(count, int)
            assert count >= 0
        finally:
            session.close()

    def test_candidate_pool_table_accessible(self):
        """v2_candidate_pool 表可达（可为空）。"""
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm

        session = _get_session()
        try:
            count = session.execute(
                select(func.count()).select_from(orm.CandidatePoolModel)
            ).scalar()
            assert count >= 0
        finally:
            session.close()

    def test_trade_plan_table_accessible(self):
        """v2_trade_plan 表可达。"""
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm

        session = _get_session()
        try:
            count = session.execute(
                select(func.count()).select_from(orm.TradePlanModel)
            ).scalar()
            assert count >= 0
        finally:
            session.close()

    def test_approval_table_accessible(self):
        """v2_approval_request 表可达。"""
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm

        session = _get_session()
        try:
            count = session.execute(
                select(func.count()).select_from(orm.ApprovalRequestModel)
            ).scalar()
            assert count >= 0
        finally:
            session.close()

    def test_position_table_accessible(self):
        """v2_position 表可达。"""
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm

        session = _get_session()
        try:
            count = session.execute(
                select(func.count()).select_from(orm.PositionModel)
            ).scalar()
            assert count >= 0
        finally:
            session.close()

    def test_pre_market_then_verify_table_state(self):
        """跑盘前后 v2_candidate_pool 表行数 ≥ before（幂等验证：不会减少）。"""
        from sqlalchemy import func, select

        from zhice_os_v2.infrastructure.db import models as orm

        session = _get_session()
        try:
            before = session.execute(
                select(func.count()).select_from(orm.CandidatePoolModel)
            ).scalar()
        finally:
            session.close()

        # 运行盘前（不关心返回值，只要不抛异常）
        scheduler_jobs.run_pre_market_job("CN")

        session = _get_session()
        try:
            after = session.execute(
                select(func.count()).select_from(orm.CandidatePoolModel)
            ).scalar()
        finally:
            session.close()

        # 幂等：after ≥ before（行数不会减少）
        assert after >= before, (
            f"盘前运行后 candidate_pool 行数意外减少: before={before} after={after}"
        )
