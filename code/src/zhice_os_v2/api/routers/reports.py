"""报告 API：V1 式晨报/晚报双报告体系。

- 晨报（盘前）：候选/计划/待授权统计 + 市场状态 + 风险提示
- 晚报（盘后）：当日平仓盈亏/胜率 + 分策略成绩单 + 建仓/出场统计 + 明日关注

历史报告来自 v2_daily_report：定时 job（晨报 9:26 / 晚报 16:10）经 compute_and_persist
幂等落库归档，前端翻历史即读库；手动 POST /generate 保持现算不落库（不污染归档）。
叙事一律用规则模板拼接（分段标题 + 要点列表），不依赖 AI。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import case, func

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.decay_monitor import DecayMonitor
from zhice_os_v2.application.services.learning_report_service import LearningReportService
from zhice_os_v2.application.services.regime_service import RegimeService
from zhice_os_v2.application.services.report_service import ReportService
from zhice_os_v2.application.services.reporting_service import ReportingService
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(get_current_user)])

_CST = ZoneInfo("Asia/Shanghai")

REGIME_LABEL = {
    "BULL": "上涨趋势",
    "BEAR": "下跌趋势",
    "SIDEWAYS": "震荡整理",
    "VOLATILE": "剧烈波动",
    "UNKNOWN": "未知",
}

# 基准指数（仅 CN 有指数日K；其他市场用代表性标的算 regime）
_INDEX_META: list[tuple[str, str]] = [
    ("000001.SH", "上证指数"),
    ("000300.SH", "沪深300"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
]
# 各市场报告「市场表现」段基准指数（B2）：US/HK 用 regime_service.REGIME_BENCHMARK
# 同源的 intended 基准（^GSPC/^HSI）——v2_bar_daily 查得到即出 moves，查不到保持
# 空 + as_of=None（数据采集由 B9 批补）。CRYPTO 无指数基准，恒空。
_INDEX_BY_MARKET: dict[str, list[tuple[str, str]]] = {
    "CN": _INDEX_META,
    "US": [("^GSPC", "标普500")],
    "HK": [("^HSI", "恒生指数")],
}
_REGIME_BENCHMARK = {"CN": "000001.SH", "US": "AAPL", "HK": "00700", "CRYPTO": "BTC/USDT"}

_PLAN_STATUS_LABEL = {
    "PENDING": "待审",
    "APPROVED": "已批准",
    "REJECTED": "已拒绝",
    "EXPIRED": "已过期",
    "CANCELLED": "已取消",
}
_EXIT_LABEL = {"TAKE_PROFIT": "止盈", "STOP_LOSS": "止损", "MANUAL": "手动"}


class GenerateBody(BaseModel):
    market: str = "CN"
    report_type: str = "morning"


# ─── 既有报告端点 ────────────────────────────────────────────────────────────


@router.get("/replay")
def replay_report(market: str | None = None, db: PersistencePort = Depends(get_db)):
    return ReportService(db).replay_report(market=market)


@router.get("/decay")
def decay_report(market: str | None = None, db: PersistencePort = Depends(get_db)):
    """衰减监控报告。C6：market 给定则只看该市场（非法值 422 market.invalid，
    对齐 B4 口径，不再静默忽略回退全市场）；缺省保持全市场（存量行为）。"""
    from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS

    if market is not None and market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=422, detail={"code": "market.invalid"})
    return DecayMonitor(db).monitor(market=market)


@router.get("/learning")
def learning_report(market: str | None = None, db: PersistencePort = Depends(get_db)):
    return LearningReportService(db).generate(market=market)


@router.get("/daily")
def daily_report(market: str | None = None, db: PersistencePort = Depends(get_db)):
    return ReportingService(db).daily_report(market=market)


@router.get("/pnl-calendar")
def pnl_calendar(
    market: str = "CN",
    year: int | None = None,
    month: int | None = None,
    strategy: str | None = None,
):
    """每日权益盈亏日历（V1 口径）：持仓浮动盈亏逐日盯市，无交易日也有盈亏。

    某日盈亏 = Σ 当日在仓批次 数量×(当日价−基准价)；基准=开仓价/昨收，平仓日
    当日价=平仓价×(1−卖出费率)。year/month 缺省用最近有数据的月份。
    响应保持前端 PnlCalendarResp 结构：days[]{day,pnl,count,wins,losses} +
    summary{total_pnl,win_days,loss_days,win_rate}（wins/losses 为天数最简映射：
    当日合计盈>0 → wins=1，亏 → losses=1，前端仅显示数字）。

    入参校验（deep-test B4）：market 非法（get_profile ValueError→500）与 month
    越界（date(2026,13,1) ValueError→500）均在入参层 422 拦截，不再静默回退。
    """
    from zhice_os_v2.domain.market.registry import SUPPORTED_MARKETS

    if market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=422, detail={"code": "market.invalid"})
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail={"code": "report.invalid_month"})

    from zhice_os_v2.application.services.pnl_calendar_service import get_pnl_calendar

    payload = get_pnl_calendar(year, month, market=market, strategy_version_id=strategy)
    days = [
        {
            "day": d["day"],
            "pnl": d["pnl"],
            "count": d["count"],
            "wins": 1 if d["pnl"] > 0 else 0,
            "losses": 1 if d["pnl"] < 0 else 0,
        }
        for d in payload["days"]
    ]
    win_days = payload["win_days"]
    loss_days = payload["loss_days"]
    traded_days = win_days + loss_days
    return {
        "market": market,
        "year": payload["year"],
        "month": payload["month"],
        "days": days,
        "summary": {
            "total_pnl": payload["month_pnl"],
            # 月度盈亏比例（净值口径）：月度盈亏 / 月初权益 ×100（build_calendar_payload 已算）
            "total_pnl_pct": payload["month_pnl_pct"],
            "win_days": win_days,
            "loss_days": loss_days,
            "win_rate": round(win_days / traded_days, 4) if traded_days else 0.0,
        },
    }


# ─── 晨报/晚报事实采集（现算，不落库） ────────────────────────────────────────


def _validate_report_type(report_type: str) -> None:
    if report_type not in ("morning", "evening"):
        raise HTTPException(status_code=400, detail={"code": "report.invalid_report_type"})


def _day_bounds(d: date, market: str = "CN") -> tuple[datetime, datetime]:
    """按市场时区切当日 [start, end)。此前固定 Asia/Shanghai，会把美/港股一个交易日
    按北京边界切成两半，导致今日计划数/今日新建仓错配到相邻自然日。"""
    from zhice_os_v2.domain.market.registry import get_profile
    tz = get_profile(market).tz
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def _latest_activity_date(session, market: str) -> date:
    """该市场最新「交易日」：候选池/复盘平仓/计划三处最新日期的最大值。"""
    dates: list[date] = []
    for scalar in (
        session.query(func.max(orm.CandidatePoolModel.pool_date))
        .filter(orm.CandidatePoolModel.market == market)
        .scalar(),
        session.query(func.max(orm.ReplayModel.close_date))
        .filter(
            orm.ReplayModel.market == market,
        )
        .scalar(),
    ):
        if scalar is not None:
            dates.append(scalar)
    ts = (
        session.query(func.max(orm.TradePlanModel.created_at))
        .filter(orm.TradePlanModel.market == market)
        .scalar()
    )
    if ts is not None:
        dates.append(ts.date())
    if dates:
        return max(dates)
    return datetime.now(tz=_CST).date()


def _index_moves(session, market: str, trade_date: date) -> dict:
    """基准指数涨跌（截至 trade_date 的最新结算）。数据陈旧时如实标注，绝不当今日。

    CN 读四大指数；US/HK 读基准指数 ^GSPC/^HSI（日线入库即出 moves，无数据保持
    空 + as_of=None，待 B9 补采集后自动出现）；CRYPTO 无指数基准恒空。
    narrative 组装（_narrative_morning）按 moves 非空条件渲染，数据到位即出段。
    """
    symbols_meta = _INDEX_BY_MARKET.get(market, [])
    if not symbols_meta:
        return {"as_of": None, "stale": False, "moves": []}
    symbols = [s for s, _ in symbols_meta]
    rows = (
        session.query(
            orm.BarDailyModel.symbol,
            orm.BarDailyModel.trade_date,
            orm.BarDailyModel.close,
            orm.BarDailyModel.pre_close,
        )
        .filter(
            orm.BarDailyModel.symbol.in_(symbols),
            orm.BarDailyModel.market == market,
            orm.BarDailyModel.trade_date <= trade_date,
        )
        .order_by(orm.BarDailyModel.trade_date.asc())
        .all()
    )
    latest: dict[str, tuple] = {}
    for sym, td, close, pre in rows:
        latest[sym] = (td, close, pre)
    moves = []
    for sym, name in symbols_meta:
        rec = latest.get(sym)
        if rec is None:
            continue
        td, close, pre = rec
        pct = None
        if pre is not None and float(pre) > 0 and close is not None:
            pct = round((float(close) / float(pre) - 1) * 100, 2)
        moves.append(
            {
                "name": name,
                "pct": pct,
                "close": float(close) if close is not None else None,
                "date": td.isoformat() if td is not None else None,
            }
        )
    as_of = max((r[0] for r in latest.values() if r[0] is not None), default=None)
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "stale": bool(as_of and as_of < trade_date),
        "moves": moves,
    }


def _regime(market: str) -> dict:
    info = dict(
        RegimeService().get_regime(market, benchmark=_REGIME_BENCHMARK.get(market, "000001.SH"))
    )
    info["label"] = REGIME_LABEL.get(info.get("regime") or "", info.get("regime"))
    return info


def _approvals_pending(session, market: str) -> int:
    return (
        session.query(func.count(orm.ApprovalRequestModel.id))
        .filter(
            orm.ApprovalRequestModel.market == market,
            orm.ApprovalRequestModel.status == "PENDING",
        )
        .scalar()
        or 0
    )


def _morning_facts(session, market: str, d: date) -> dict:
    start, end = _day_bounds(d, market)
    pools = (
        session.query(func.count(orm.CandidatePoolModel.id))
        .filter(orm.CandidatePoolModel.market == market, orm.CandidatePoolModel.pool_date == d)
        .scalar()
        or 0
    )
    assets = (
        session.query(func.count(orm.CandidateAssetModel.id))
        .join(orm.CandidatePoolModel, orm.CandidatePoolModel.pool_id == orm.CandidateAssetModel.pool_id)
        .filter(
            orm.CandidatePoolModel.market == market,
            orm.CandidatePoolModel.pool_date == d,
            orm.CandidateAssetModel.market == market,
        )
        .scalar()
        or 0
    )
    status_rows = (
        session.query(orm.TradePlanModel.plan_status, func.count())
        .filter(
            orm.TradePlanModel.market == market,
            orm.TradePlanModel.created_at >= start,
            orm.TradePlanModel.created_at < end,
        )
        .group_by(orm.TradePlanModel.plan_status)
        .all()
    )
    plan_status = {k: int(v) for k, v in status_rows}
    return {
        "report_type": "morning",
        "market": market,
        "date": d.isoformat(),
        "candidate_pools": int(pools),
        "candidate_assets": int(assets),
        "plans_today": sum(plan_status.values()),
        "plans_pending": plan_status.get("PENDING", 0),
        "approvals_pending": _approvals_pending(session, market),
        "plan_status": plan_status,
        "regime": _regime(market),
        "index_moves": _index_moves(session, market, d),
        "generated_at": datetime.now(tz=_CST).isoformat(),
    }


def _evening_facts(session, market: str, d: date) -> dict:
    start, end = _day_bounds(d, market)
    row = (
        session.query(
            func.count(orm.ReplayModel.id),
            func.coalesce(func.sum(orm.ReplayModel.pnl), 0),
            func.sum(case((orm.ReplayModel.pnl > 0, 1), else_=0)),
            func.sum(case((orm.ReplayModel.pnl < 0, 1), else_=0)),
        )
        .filter(
            orm.ReplayModel.market == market,
            orm.ReplayModel.close_date == d,
        )
        .one()
    )
    closed, total_pnl, wins, losses = int(row[0]), float(row[1]), int(row[2] or 0), int(row[3] or 0)

    score_rows = (
        session.query(
            orm.ReplayModel.strategy_version_id,
            func.coalesce(
                orm.StrategyModel.strategy_name, orm.StrategyVersionModel.strategy_version_id
            ),
            func.count(),
            func.sum(orm.ReplayModel.pnl),
            func.sum(case((orm.ReplayModel.pnl > 0, 1), else_=0)),
        )
        .join(
            orm.StrategyVersionModel,
            orm.StrategyVersionModel.strategy_version_id == orm.ReplayModel.strategy_version_id,
            isouter=True,
        )
        .join(
            orm.StrategyModel,
            orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id,
            isouter=True,
        )
        .filter(
            orm.ReplayModel.market == market,
            orm.ReplayModel.close_date == d,
        )
        .group_by(
            orm.ReplayModel.strategy_version_id,
            orm.StrategyModel.strategy_name,
            orm.StrategyVersionModel.strategy_version_id,
        )
        .all()
    )
    strategy_scores = [
        {
            "strategy_version_id": r[0],
            "strategy_name": r[1] or r[0],
            "trades": int(r[2]),
            "pnl": float(r[3] or 0),
            "wins": int(r[4] or 0),
            "losses": int(r[2]) - int(r[4] or 0),
            "win_rate": round(int(r[4] or 0) / int(r[2]), 4) if r[2] else 0.0,
        }
        for r in score_rows
    ]
    strategy_scores.sort(key=lambda x: x["pnl"], reverse=True)

    exit_rows = (
        session.query(orm.ReplayModel.exit_reason, func.count())
        .filter(
            orm.ReplayModel.market == market,
            orm.ReplayModel.close_date == d,
        )
        .group_by(orm.ReplayModel.exit_reason)
        .all()
    )
    opened = (
        session.query(func.count(orm.PositionModel.id))
        .filter(
            orm.PositionModel.market == market,
            orm.PositionModel.opened_at >= start,
            orm.PositionModel.opened_at < end,
        )
        .scalar()
        or 0
    )
    return {
        "report_type": "evening",
        "market": market,
        "date": d.isoformat(),
        "closed_trades": closed,
        "total_pnl": round(total_pnl, 2),
        "winning_trades": wins,
        "losing_trades": losses,
        "win_rate": round(wins / closed, 4) if closed else 0.0,
        "strategy_scores": strategy_scores,
        "exit_breakdown": {k: int(v) for k, v in exit_rows},
        "opened_positions": int(opened),
        "approvals_pending": _approvals_pending(session, market),
        "regime": _regime(market),
        "index_moves": _index_moves(session, market, d),
        "generated_at": datetime.now(tz=_CST).isoformat(),
    }


# ─── 规则模板叙事（分段标题 + 要点列表） ─────────────────────────────────────


def _fp(v) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fm(v) -> str:
    if v is None:
        return "—"
    return f"{v:+,.2f}"


def _narrative_morning(f: dict) -> str:
    lines = ["## 今日概览"]
    lines.append(
        f"- 候选池 {f['candidate_pools']} 个 / 覆盖标的 {f['candidate_assets']} 只，"
        f"生成交易计划 {f['plans_today']} 个"
    )
    lines.append(
        f"- 待授权 {f['approvals_pending']} 单（待审计划 {f['plans_pending']} 个）"
    )
    g = f.get("regime") or {}
    trend = ""
    if g.get("ret5") is not None and g.get("ret20") is not None:
        trend = f"（近5日 {_fp(g['ret5'] * 100)}，近20日 {_fp(g['ret20'] * 100)}）"
    lines.append(f"- 市场环境：{g.get('label') or '未知'}{trend}")

    im = f.get("index_moves") or {}
    moves = [m for m in (im.get("moves") or []) if m.get("pct") is not None]
    if moves:
        lines.append("")
        lines.append("## 市场表现（基准指数）")
        suffix = f"（截至 {im.get('as_of')}，今日未结算）" if im.get("stale") else ""
        for m in moves:
            close_txt = f"，收 {m['close']:,.2f}" if m.get("close") is not None else ""
            lines.append(f"- {m['name']} {_fp(m['pct'])}{close_txt}{suffix}")

    lines.append("")
    lines.append("## 今日待审计划")
    ps = f.get("plan_status") or {}
    if f["plans_today"]:
        seg = "、".join(
            f"{_PLAN_STATUS_LABEL.get(k, k)} {v} 个" for k, v in sorted(ps.items()) if v
        )
        lines.append(f"- 当日计划：{seg or '无'}")
    else:
        lines.append("- 今日无新增交易计划")
    if f["approvals_pending"]:
        lines.append(f"- 有 {f['approvals_pending']} 个授权请求等待处理")
    else:
        lines.append("- 无待处理授权请求")

    lines.append("")
    lines.append("## 风险提示")
    rejected = ps.get("REJECTED", 0)
    if rejected:
        lines.append(f"- 今日有 {rejected} 个计划被风控拒绝，复核入场条件与仓位约束")
    else:
        lines.append("- 今日无计划被风控拒绝")
    if g.get("regime") == "BEAR":
        lines.append("- 市场处于下跌趋势，注意收紧仓位与止损纪律")
    return "\n".join(lines)


def _narrative_evening(f: dict) -> str:
    lines = ["## 今日概览"]
    lines.append(
        f"- 当日平仓 {f['closed_trades']} 笔，总盈亏 {_fm(f['total_pnl'])}，"
        f"胜率 {f['win_rate'] * 100:.1f}%（盈利 {f['winning_trades']} 笔 / 亏损 {f['losing_trades']} 笔）"
    )
    lines.append(f"- 当日新建仓 {f['opened_positions']} 笔")

    lines.append("")
    lines.append("## 分策略成绩单")
    scores = f.get("strategy_scores") or []
    if scores:
        for s in scores:
            lines.append(
                f"- {s['strategy_name']}：{s['trades']} 笔，盈亏 {_fm(s['pnl'])}，"
                f"胜率 {s['win_rate'] * 100:.1f}%"
            )
    else:
        lines.append("- 今日无平仓记录")

    lines.append("")
    lines.append("## 建仓/出场统计")
    eb = f.get("exit_breakdown") or {}
    if eb:
        parts = [f"{_EXIT_LABEL.get(k, k)} {v} 笔" for k, v in sorted(eb.items())]
        lines.append("- 出场：" + " / ".join(parts))
    else:
        lines.append("- 今日无出场记录")
    lines.append(f"- 建仓：{f['opened_positions']} 笔")

    lines.append("")
    lines.append("## 明日关注")
    if f.get("approvals_pending"):
        lines.append(f"- 待授权 {f['approvals_pending']} 单待处理，盘前完成审批")
    g = f.get("regime") or {}
    lines.append(f"- 市场环境：{g.get('label') or '未知'}，明日开盘前复核候选池与计划状态")
    return "\n".join(lines)


def _compute_report(session, market: str, report_type: str, d: date) -> tuple[dict, str]:
    if report_type == "morning":
        facts = _morning_facts(session, market, d)
        rule = _narrative_morning(facts)
    else:
        facts = _evening_facts(session, market, d)
        rule = _narrative_evening(facts)
    # AI 主编优先（V1 ai_editor 语义）；fail-open 任何失败降级规则模板
    try:
        from zhice_os_v2.application.services.ai_editor import narrate
        ai = narrate(facts, report_type)
    except Exception:
        ai = None
    return facts, ai or rule


def _computed_payload(market: str, report_type: str, d: date, facts: dict, narrative: str) -> dict:
    model_used = "rule_template"
    try:
        import os as _os
        if _os.getenv("REPORT_LLM_API_KEY"):
            model_used = "ai_editor"
    except Exception:
        pass
    return {
        "report_id": None,
        "market": market,
        "report_date": d.isoformat(),
        "report_type": report_type,
        "facts": facts,
        "narrative": narrative,
        "model_used": model_used,
        "source": "computed",
    }


# ─── 定时归档落库（晨/晚报 job 专用；手动 /generate 不走此路径）────────────────


def _persist_report(
    session, market: str, report_type: str, d: date, facts: dict, narrative: str,
    model_used: str,
) -> str:
    """幂等落库当期报告到 v2_daily_report（同 market+type+date 唯一，重跑覆盖）。

    report_id 用确定性 `{market}_{type}_{date}`（如 cn_morning_2026-08-24，≤64），
    on_conflict_do_update 让盘中现算与收盘定稿多次触发只留最终一行。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from zhice_os_v2.domain.ontology.objects import utc_now

    report_id = f"{market.lower()}_{report_type}_{d.isoformat()}"
    stmt = (
        pg_insert(orm.DailyReportModel)
        .values(
            report_id=report_id, market=market, report_date=d, report_type=report_type,
            facts=facts, narrative=narrative, model_used=model_used, created_at=utc_now(),
        )
        .on_conflict_do_update(
            index_elements=["report_id"],
            set_={
                "facts": facts, "narrative": narrative,
                "model_used": model_used, "created_at": utc_now(),
            },
        )
    )
    session.execute(stmt)
    session.commit()
    return report_id


def compute_and_persist(market: str, report_type: str) -> dict:
    """现算当期报告并落库归档（晨/晚报 job 调用）。落库失败 fail-open 仍返回现算 payload。"""
    _validate_report_type(report_type)
    session = get_session()
    try:
        trade_date = _latest_activity_date(session, market)
        facts, narrative = _compute_report(session, market, report_type, trade_date)
        payload = _computed_payload(market, report_type, trade_date, facts, narrative)
        # 内存后端（单测）不写真库——保证隔离、不污染生产库（get_session 恒连真 PG）。
        from zhice_os_v2.config.settings import get_settings
        if get_settings().persistence_backend != "sql":
            return payload
        try:
            report_id = _persist_report(
                session, market, report_type, trade_date, facts, narrative,
                payload["model_used"],
            )
            payload["report_id"] = report_id
            payload["source"] = "db"
        except Exception:
            session.rollback()
            logger.exception("daily_report persist failed market=%s type=%s", market, report_type)
        return payload
    finally:
        session.close()


# ─── 晨报/晚报端点 ──────────────────────────────────────────────────────────


@router.get("/latest")
def latest_report(market: str = "CN", report_type: str = "morning"):
    """最新晨报/晚报。优先取 v2_daily_report 该类型最新一条；当期没有则现算（不落库）。"""
    _validate_report_type(report_type)
    session = get_session()
    try:
        row = (
            session.query(orm.DailyReportModel)
            .filter(
                orm.DailyReportModel.market == market,
                orm.DailyReportModel.report_type == report_type,
            )
            .order_by(orm.DailyReportModel.report_date.desc(), orm.DailyReportModel.id.desc())
            .first()
        )
        trade_date = _latest_activity_date(session, market)
        if row is not None and row.report_date == trade_date:
            return {
                "report_id": row.report_id,
                "market": row.market,
                "report_date": row.report_date.isoformat(),
                "report_type": row.report_type,
                "facts": row.facts or {},
                "narrative": row.narrative or "",
                "model_used": row.model_used,
                "source": "db",
            }
        facts, narrative = _compute_report(session, market, report_type, trade_date)
        return _computed_payload(market, report_type, trade_date, facts, narrative)
    finally:
        session.close()


@router.get("/list")
def list_reports(market: str = "CN", report_type: str | None = None, limit: int = 30):
    """报告历史列表（倒序）。"""
    session = get_session()
    try:
        q = session.query(orm.DailyReportModel).filter(orm.DailyReportModel.market == market)
        if report_type:
            _validate_report_type(report_type)
            q = q.filter(orm.DailyReportModel.report_type == report_type)
        rows = (
            q.order_by(orm.DailyReportModel.report_date.desc(), orm.DailyReportModel.id.desc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
        return [
            {
                "report_id": r.report_id,
                "report_date": r.report_date.isoformat(),
                "report_type": r.report_type,
                "model_used": r.model_used,
            }
            for r in rows
        ]
    finally:
        session.close()


@router.post("/generate")
def generate_report(body: GenerateBody):
    """手动生成当期报告（现算，不落库）。返回与 latest 相同结构。"""
    _validate_report_type(body.report_type)
    session = get_session()
    try:
        trade_date = _latest_activity_date(session, body.market)
        facts, narrative = _compute_report(session, body.market, body.report_type, trade_date)
        return _computed_payload(body.market, body.report_type, trade_date, facts, narrative)
    finally:
        session.close()


@router.get("/{report_id}")
def report_detail(report_id: str):
    """单份报告全文：facts(jsonb) + narrative(text)。"""
    session = get_session()
    try:
        row = (
            session.query(orm.DailyReportModel)
            .filter(orm.DailyReportModel.report_id == report_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "report.not_found"})
        return {
            "report_id": row.report_id,
            "market": row.market,
            "report_date": row.report_date.isoformat(),
            "report_type": row.report_type,
            "facts": row.facts or {},
            "narrative": row.narrative or "",
            "model_used": row.model_used,
            "source": "db",
        }
    finally:
        session.close()
