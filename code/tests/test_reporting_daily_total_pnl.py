"""日报 total_pnl KeyError（P3 旁路）。

缺陷：ReportingService.daily_report 读 replay['total_pnl']，但 ReportService.replay_report
     在 market=None（ALL，跨市场）时返回的键是 total_pnl_cny（本位币折算），不含 total_pnl
     → GET /reports/daily 不带 market 时 KeyError 500。指定 market 时才有 total_pnl。

修复：daily_report 兼容两键（ALL 用 total_pnl_cny、单市场用 total_pnl），不再硬取 total_pnl。

用内存后端真实播种 ReplayCase，直连 replay_report → daily_report 全链，不打桩。
"""
from decimal import Decimal

from zhice_os_v2.application.services.reporting_service import ReportingService
from zhice_os_v2.domain.replay.replay import ReplayCase
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase as InMemoryDB


def _replay(rid, market, pnl):
    return ReplayCase(replay_id=rid, strategy_version_id="sv-x", market=market,
                      ts_code="AAA", pnl=Decimal(str(pnl)), pnl_pct=Decimal("0.1"),
                      holding_period_seconds=3600, exit_reason="TP")


def _db_with_replays():
    db = InMemoryDB()
    db.save_replay(_replay("r1", "CN", 100))
    db.save_replay(_replay("r2", "US", 50))
    return db


def test_daily_report_all_markets_no_keyerror():
    """回归：market=None（ALL）时 daily_report 不应因 total_pnl 缺键而抛错。"""
    db = _db_with_replays()
    report = ReportingService(db).daily_report(market=None)  # 此前 KeyError
    assert "总盈亏" in report["content"]
    # ALL 口径下 replay 子块用折算键
    assert "total_pnl_cny" in report["replay"]


def test_daily_report_single_market_still_works():
    """单市场仍走 total_pnl 键，内容含盈亏行。"""
    db = _db_with_replays()
    report = ReportingService(db).daily_report(market="CN")
    assert "total_pnl" in report["replay"]
    assert "总盈亏: 100" in report["content"]
