"""P1-G 盘中入场 C0 持仓数上限复检。

缺陷：_run_entry_loop 执行盘前已 APPROVED 授权时，从不复检「该 sv 当前持仓 distinct
只数 < 上限」。C0 上限只在盘前 size_new_position 查一次，但盘前只建计划+授权不落仓 →
同一轮多个不同新候选都看到 distinct<10 全放行 → 盘中循环全执行 → 顶穿 per-sv≤10。

本测试用 monkeypatch 把 persistence_backend 打成 "sql"（否则 :1088 内存后端短路 return[]，
测不到缺陷本体），并把 _auto_execute 替换为 spy 记录调用。
"""
import types
from datetime import timedelta
from decimal import Decimal

from zhice_os_v2.application.services import workflow_service as wf
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

MARKET = "US"
SV_NUM = 1                     # 授权侧数字 sv（走归一）
SV_FULL = "v1-us-sv-1"        # 落仓侧完整 sv（list_open_positions 里的形态）


class _Appr:
    """伪授权（ApprovalRequestModel 桩）：只暴露 _run_entry_loop 读取的字段。"""

    def __init__(self, ts_code: str, sv, rid: str):
        self.ts_code = ts_code
        self.strategy_version_id = sv
        self.order_intent_id = rid
        self.request_id = rid
        self.price = 10.0
        self.qty = 100
        self.direction = "BUY"


class _DummySession:
    def close(self):
        pass


def _lot(ts_code: str, sv: str = SV_FULL) -> PositionLot:
    return PositionLot(
        position_id=f"pos-{ts_code}",
        strategy_version_id=sv,
        market=MARKET,
        ts_code=ts_code,
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("10"),
        opened_at=utc_now() - timedelta(days=5),
    )


def _setup(monkeypatch, held_lots, approvals):
    """装配：InMemory 库落 held_lots + 打桩放行所有前置闸 + 用 spy 替换 _auto_execute。

    返回 (svc, calls)。calls 记录 _auto_execute 被调用的 ts_code 序列。
    """
    db = InMemoryDatabase()
    for lot in held_lots:
        db.save_position(lot)
    svc = WorkflowService(db)

    # 关键：绕开 :1088 内存后端短路，真跑到入场循环 + C0 复检本体
    monkeypatch.setattr(
        "zhice_os_v2.config.settings.get_settings",
        lambda: types.SimpleNamespace(persistence_backend="sql"))
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session", lambda: _DummySession())
    monkeypatch.setattr(
        "zhice_os_v2.api.routers.circuit_breaker.is_tripped", lambda: False)
    monkeypatch.setattr(
        "zhice_os_v2.api.routers.positions._get_pre_closes",
        lambda codes, market: {})
    # 入场前置：全部放行
    monkeypatch.setattr(
        "zhice_os_v2.domain.execution.entry_signal.evaluate_entry",
        lambda **kw: (True, "E1E2E3 ok"))
    monkeypatch.setattr(
        "zhice_os_v2.domain.execution.market_rules.can_buy",
        lambda *a, **k: (True, "buyable"))
    monkeypatch.setattr(wf, "_sim_auto", lambda market: True)

    # 实例方法桩（真库查询全部旁路）
    monkeypatch.setattr(svc, "_query_entry_approvals",
                        lambda session, market, today, profile: approvals)
    live = {a.ts_code: 10.0 for a in approvals}
    monkeypatch.setattr(svc, "_snapshot_price_time",
                        lambda ts_codes, market: (dict(live),
                                                  {a.ts_code: None for a in approvals}))
    monkeypatch.setattr(svc, "_prev_tick_prices", lambda ts_codes, market: dict(live))
    monkeypatch.setattr(svc, "_day_opens", lambda ts_codes, market: dict(live))
    monkeypatch.setattr(svc, "_today_entry_counts", lambda market, today: {})
    monkeypatch.setattr(svc, "_today_last_fill_prices", lambda market, today: {})

    calls: list[str] = []

    def _spy(a, market, profile, live_price=None):
        calls.append(a.ts_code)
        return types.SimpleNamespace(position_id=f"pos-{a.ts_code}")

    monkeypatch.setattr(svc, "_auto_execute", _spy)
    return svc, calls


def test_c0_blocks_11th_when_sv_already_at_limit(monkeypatch):
    """已持 10 只 distinct（满上限）+ 1 个新标的授权 → C0 拦截，不建第 11 只。

    红（修前）：spy 被调（建第 11 只），entered 非空。
    绿（修后）：spy 未被调，entered == []。
    """
    held = [_lot(f"H{i:02d}") for i in range(10)]  # 10 只 distinct
    approvals = [_Appr("NEWSTK", SV_NUM, "plan-new")]  # 数字 sv → 归一到 SV_FULL
    svc, calls = _setup(monkeypatch, held, approvals)

    entered = svc._run_entry_loop(MARKET, get_profile(MARKET))

    assert calls == [], f"C0 应拦截第 11 只，但 _auto_execute 被调: {calls}"
    assert entered == []


def test_c0_allows_fill_up_to_limit_then_blocks(monkeypatch):
    """已持 9 只 + 2 个新标的授权 → 只成交 1 个（补到 10 即止）。

    验证第 4 步：成功建仓后运行时集合递增，同一轮后续授权立即感知。
    """
    held = [_lot(f"H{i:02d}") for i in range(9)]  # 9 只 distinct
    approvals = [_Appr("NEW1", SV_NUM, "plan-1"), _Appr("NEW2", SV_NUM, "plan-2")]
    svc, calls = _setup(monkeypatch, held, approvals)

    entered = svc._run_entry_loop(MARKET, get_profile(MARKET))

    assert calls == ["NEW1"], f"应只补到 10 即止，实际调用: {calls}"
    assert [e["ts_code"] for e in entered] == ["NEW1"]


def test_c0_does_not_block_add_on_to_held_symbol(monkeypatch):
    """反向：授权标的已在该 sv 持仓集合内（加仓）→ 即便已 10 只 distinct 仍放行。"""
    held = [_lot(f"H{i:02d}") for i in range(10)]  # 满 10 只
    approvals = [_Appr("H00", SV_NUM, "plan-addon")]  # 加仓到已持 H00
    svc, calls = _setup(monkeypatch, held, approvals)

    entered = svc._run_entry_loop(MARKET, get_profile(MARKET))

    assert calls == ["H00"], "加仓到已持标的不应被 C0 拦截"
    assert [e["ts_code"] for e in entered] == ["H00"]
