"""deep-test B7 批：报告/通知/错误形态/杂项。

覆盖清单（每项对应实测问题）：
A1  POST /auth/me/telegram/test 失败透传 reason（transport error_code），不再丢
B2  reports._index_moves US/HK 读 ^GSPC/^HSI 日线（有数出 moves、无数保持空）
C4  GET /exports/replays 带 Content-Disposition: attachment（文件名含当日日期）
C5  GET /governance/promotion-eval/{未知sv} → 404 {code:strategy.not_found}
C6  /reports/decay、/governance/auto-review 非法 market → 422 market.invalid（合法时过滤）
C7  /system/tasks COMPLETED 带 inferred=true（数据存在性反推口径）

需 DB 的用例均 monkeypatch 模块级 get_session 到隔离 sqlite（StaticPool），
绝不连真 PG（conftest _guard_real_pg 语义）。
"""
from __future__ import annotations

import datetime as dt
from datetime import UTC, date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.api.main import app
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

client = TestClient(app)


def _h(role: str = "ADMIN") -> dict:
    from zhice_os_v2.application.services.token_service import create_token
    return {"Authorization": f"Bearer {create_token('u-b7', 'b7tester', role)}"}


@pytest.fixture
def sqlite_session():
    """隔离 sqlite 会话工厂（测试自行 patch 到目标模块的模块级 get_session）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


# ─── A1：telegram test 端点 reason 透传 ─────────────────────────────────────


def _as_user(telegram_chat_id: str | None):
    """临时 override get_current_user（指定 telegram_chat_id）；返回清理函数。

    显式 setup/cleanup（不用生成器 next：半途生成器被 GC 会触发 finally 误删
    后续测试刚装好的 override）。
    """
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.routers.auth import get_auth_service
    from zhice_os_v2.domain.security.user import Role, User

    user = User("u-b7tg", "b7tg", Role.ADMIN, telegram_chat_id=telegram_chat_id)

    class _AuthService:
        def get_active_telegram_recipient(self, user_id: str) -> str | None:
            return telegram_chat_id

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_auth_service] = lambda: _AuthService()

    def cleanup() -> None:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_auth_service, None)

    return cleanup


def test_telegram_test_unbound_400():
    cleanup = _as_user(None)
    try:
        r = client.post("/api/v1/auth/me/telegram/test", headers=_h())
    finally:
        cleanup()
    assert r.status_code == 400
    assert r.json()["detail"] == {"code": "auth.telegram_not_bound"}


@pytest.mark.parametrize(
    "error_code",
    ["not_configured", "network", "http_401", "http_429"],
)
def test_telegram_test_failure_carries_reason(monkeypatch, error_code):
    import zhice_os_v2.application.services.telegram_notifier as tn

    cleanup = _as_user("123456")
    try:
        monkeypatch.setattr(
            tn,
            "send_telegram",
            lambda cid, text: tn.TelegramSendResult(ok=False, error_code=error_code),
        )
        r = client.post("/api/v1/auth/me/telegram/test", headers=_h())
    finally:
        cleanup()
    assert r.status_code == 200
    assert r.json() == {"ok": False, "reason": error_code}


def test_telegram_test_success_no_reason(monkeypatch):
    import zhice_os_v2.application.services.telegram_notifier as tn

    cleanup = _as_user("123456")
    try:
        monkeypatch.setattr(
            tn,
            "send_telegram",
            lambda cid, text: tn.TelegramSendResult(ok=True, status_code=200),
        )
        r = client.post("/api/v1/auth/me/telegram/test", headers=_h())
    finally:
        cleanup()
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ─── B2：_index_moves US/HK 基准指数 ────────────────────────────────────────


def _bar(session_factory, market: str, symbol: str, d: date, close: str, pre_close: str):
    with session_factory() as s:
        s.add(
            orm.BarDailyModel(
                instrument_id=f"idx-{symbol}-{d}",
                market=market,
                symbol=symbol,
                trade_date=d,
                close=Decimal(close),
                pre_close=Decimal(pre_close),
                created_at=dt.datetime.now(UTC),
            )
        )
        s.commit()


def test_index_moves_us_with_bars_generates_moves(sqlite_session):
    from zhice_os_v2.api.routers.reports import _index_moves

    d = date(2026, 9, 7)
    _bar(sqlite_session, "US", "^GSPC", d, "5450.0", "5000.0")
    out = _index_moves(sqlite_session(), "US", d)
    assert out["as_of"] == "2026-09-07"
    assert out["stale"] is False
    assert len(out["moves"]) == 1
    m = out["moves"][0]
    assert m["name"] == "标普500"
    assert m["pct"] == 9.0  # 5450/5000-1
    assert m["close"] == 5450.0


def test_index_moves_hk_no_bars_stays_empty(sqlite_session):
    """HK ^HSI 尚无采集（B9 批补）→ 保持空 + as_of=None，不虚造。"""
    from zhice_os_v2.api.routers.reports import _index_moves

    out = _index_moves(sqlite_session(), "HK", date(2026, 9, 7))
    assert out == {"as_of": None, "stale": False, "moves": []}


def test_index_moves_crypto_has_no_index_benchmark(sqlite_session):
    from zhice_os_v2.api.routers.reports import _index_moves

    _bar(sqlite_session, "CRYPTO", "BTC/USDT", date(2026, 9, 7), "60000", "58000")
    out = _index_moves(sqlite_session(), "CRYPTO", date(2026, 9, 7))
    assert out == {"as_of": None, "stale": False, "moves": []}


def test_index_moves_cn_regression_still_four(sqlite_session):
    """CN 四大指数回归：原逻辑不动。"""
    from zhice_os_v2.api.routers.reports import _index_moves

    d = date(2026, 9, 7)
    for sym in ("000001.SH", "000300.SH", "399001.SZ", "399006.SZ"):
        _bar(sqlite_session, "CN", sym, d, "3000", "2950")
    out = _index_moves(sqlite_session(), "CN", d)
    assert len(out["moves"]) == 4
    assert out["as_of"] == "2026-09-07"


def test_narrative_morning_renders_index_moves_when_present():
    """B3：narrative 组装无硬编码跳过——moves 有数据时「市场表现」段即渲染。"""
    from zhice_os_v2.api.routers.reports import _narrative_morning

    facts = {
        "candidate_pools": 1, "candidate_assets": 5, "plans_today": 2,
        "plans_pending": 1, "approvals_pending": 0, "plan_status": {"PENDING": 2},
        "regime": {"label": "上涨趋势", "regime": "BULL", "ret5": 0.01, "ret20": 0.02},
        "index_moves": {
            "as_of": "2026-09-07", "stale": False,
            "moves": [{"name": "标普500", "pct": 0.85, "close": 5450.0, "date": "2026-09-07"}],
        },
    }
    text = _narrative_morning(facts)
    assert "市场表现（基准指数）" in text
    assert "标普500" in text


# ─── C4：exports Content-Disposition ────────────────────────────────────────


def test_exports_replays_has_attachment_disposition():
    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    app.dependency_overrides[get_db] = lambda: InMemoryDatabase()
    try:
        r = client.get("/api/v1/exports/replays", headers=_h())
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    expected = f'attachment; filename="replays_{date.today():%Y%m%d}.csv"'
    assert r.headers["content-disposition"] == expected


# ─── C5：promotion-eval 未知 sv 404 ─────────────────────────────────────────


@pytest.fixture
def promotion_db(monkeypatch, sqlite_session):
    """隔离 sqlite：一个真实版本（sv-b7-real，DRAFT）供 happy path，未知 sv 走空查。"""
    import zhice_os_v2.infrastructure.db.session as sess_mod

    with sqlite_session() as s:
        s.add(
            orm.StrategyModel(
                strategy_id="st-b7",
                strategy_name="[V1] b7测试策略",
                market="CN",
                status="ACTIVE",
                visibility="PUBLIC",
                publication_status="PUBLISHED",
                created_at=dt.datetime.now(UTC),
                updated_at=dt.datetime.now(UTC),
            )
        )
        s.add(
            orm.StrategyVersionModel(
                strategy_version_id="sv-b7-real",
                strategy_id="st-b7",
                version_no="v1",
                market="CN",
                status="DRAFT",
                spec={},
                created_at=dt.datetime.now(UTC),
            )
        )
        s.commit()
    monkeypatch.setattr(sess_mod, "get_session", lambda: sqlite_session())
    return sqlite_session


def test_promotion_eval_unknown_sv_404(promotion_db):
    r = client.get("/api/v1/governance/promotion-eval/sv-b7-nope", headers=_h())
    assert r.status_code == 404
    assert r.json()["detail"] == {"code": "strategy.not_found"}


def test_promotion_eval_existing_sv_still_200(promotion_db):
    r = client.get("/api/v1/governance/promotion-eval/sv-b7-real", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["current_status"] == "DRAFT"


# ─── C6：decay / auto-review market 校验与过滤 ─────────────────────────────


def _replay(rid: str, market: str, pnl: Decimal, seq: int):
    from zhice_os_v2.domain.replay.replay import ReplayCase, ReviewStatus

    return ReplayCase(
        replay_id=rid,
        strategy_version_id=f"sv-{market.lower()}",
        market=market,
        ts_code="600519.SH" if market == "CN" else "AAPL",
        pnl=pnl,
        pnl_pct=Decimal("0.01"),
        holding_period_seconds=3600,
        exit_reason="TAKE_PROFIT",
        status=ReviewStatus.PENDING,
        created_at=dt.datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=seq),
    )


@pytest.fixture
def replays_db():
    """内存库预置 CN/US 各 10 条 replay（满足衰减监控 min 10 条）。"""
    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    db = InMemoryDatabase()
    # CN：前 8 胜后 2 负 → 衰减；US：全胜 → 正常
    for i in range(10):
        db.save_replay(_replay(f"rp-cn-{i}", "CN", Decimal("10") if i < 8 else Decimal("-10"), i))
        db.save_replay(_replay(f"rp-us-{i}", "US", Decimal("10"), i))
    app.dependency_overrides[get_db] = lambda: db
    yield db
    app.dependency_overrides.pop(get_db, None)


def test_decay_invalid_market_422(replays_db):
    r = client.get("/api/v1/reports/decay", params={"market": "XX"}, headers=_h())
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_decay_valid_market_filters(replays_db):
    """合法 market 只看该市场：CN/US 各一个策略，不再静默全市场混杂。"""
    r_cn = client.get("/api/v1/reports/decay", params={"market": "CN"}, headers=_h())
    assert r_cn.status_code == 200
    items = r_cn.json()
    assert len(items) == 1 and items[0]["strategy_version_id"] == "sv-cn"

    r_us = client.get("/api/v1/reports/decay", params={"market": "US"}, headers=_h())
    assert [i["strategy_version_id"] for i in r_us.json()] == ["sv-us"]


def test_decay_default_all_markets_regression(replays_db):
    """缺省 market 保持全市场（存量行为不动）。"""
    r = client.get("/api/v1/reports/decay", headers=_h())
    assert {i["strategy_version_id"] for i in r.json()} == {"sv-cn", "sv-us"}


def test_auto_review_invalid_market_422(replays_db):
    r = client.get("/api/v1/governance/auto-review", params={"market": "XX"}, headers=_h())
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_auto_review_valid_market_filters(replays_db):
    r_cn = client.get("/api/v1/governance/auto-review", params={"market": "CN"}, headers=_h())
    assert r_cn.status_code == 200
    body = r_cn.json()
    assert body["checked"] == 1
    assert [s["strategy_version_id"] for s in body["suggestions"]] == ["sv-cn"]


# ─── C7：/system/tasks inferred 标记 ────────────────────────────────────────


@pytest.fixture
def tasks_db(monkeypatch, sqlite_session):
    """隔离 sqlite：上周某交易日有候选池（盘前 done）、无分钟线/开仓/平仓。"""
    import zhice_os_v2.api.routers.system_ext as system_ext

    past = date.today() - timedelta(days=7)
    with sqlite_session() as s:
        s.add(
            orm.MarketCalendarModel(
                market="CN", trade_date=past, is_open=True,
                created_at=dt.datetime.now(UTC),
            )
        )
        s.add(
            orm.CandidatePoolModel(
                pool_id="pool-b7",
                strategy_version_id="sv-b7",
                market="CN",
                pool_date=past,
                pool_type="DAILY",
                status="READY",
                created_at=dt.datetime.now(UTC),
            )
        )
        s.commit()
    monkeypatch.setattr(system_ext, "get_session", lambda: sqlite_session())
    return sqlite_session


def test_system_tasks_completed_carries_inferred(tasks_db):
    """过去交易日：盘前（有候选池）done→inferred True；盘中（无分钟线/开仓且过截止）
    MISSING→inferred False；盘后/学习（无平仓，skip）COMPLETED→inferred True。"""
    r = client.get("/api/v1/system/tasks", params={"market": "CN"}, headers=_h())
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) == 4
    by_name = {t["task_name"]: t for t in tasks}
    assert by_name["盘前工作流"]["status"] == "COMPLETED"
    assert by_name["盘前工作流"]["inferred"] is True
    assert by_name["盘中快照"]["status"] == "MISSING"
    assert by_name["盘中快照"]["inferred"] is False
    # 无平仓 → skip 语义的 COMPLETED 也是反推口径
    assert by_name["盘后复盘"]["status"] == "COMPLETED"
    assert by_name["盘后复盘"]["inferred"] is True
    assert by_name["学习汇总"]["status"] == "COMPLETED"
    assert by_name["学习汇总"]["inferred"] is True


def test_system_tasks_pending_not_inferred(tasks_db):
    """当天日历 + 无任何产出：MISSING/PENDING 两态 inferred 均为 False
    （断言不受测试执行的具体时刻影响——只断言非 COMPLETED 的 inferred 恒 False）。"""
    from zhice_os_v2.api.routers.system_ext import _market_now

    # 播种日期须与端点口径一致：端点按 _market_now(market).date()（美东）计 today，
    # 用机器本地 date.today() 在亚洲时段会 > 美东当天而被过滤（CI 时区 flaky 根因）。
    us_today = _market_now("US").date()
    with tasks_db() as s:
        s.add(
            orm.MarketCalendarModel(
                market="US", trade_date=us_today, is_open=True,
                created_at=dt.datetime.now(UTC),
            )
        )
        s.commit()
    r = client.get("/api/v1/system/tasks", params={"market": "US"}, headers=_h())
    tasks = r.json()
    assert tasks, "当天日历应产出 4 个任务"
    non_completed = [t for t in tasks if t["status"] != "COMPLETED"]
    assert non_completed, "无任何产出的当天必有 MISSING/PENDING 任务"
    assert all(t["inferred"] is False for t in non_completed)
