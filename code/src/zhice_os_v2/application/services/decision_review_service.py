"""决策复盘生成服务（V1 decision_review_service 移植，4 类）。

V1 算法（阈值 1:1）：
  评价窗：T+5 交易日（基准=决策日往前 5 个交易日 → 评价日）。
  CANDIDATE：raw=after/base-1（不复权 close）；行业内超额（peers≥5 才排名）；
      组内≥3 标 BEST/WORST。score=行业超额百分点（无行业退化大盘超额）。
  ENTRY：pct=(买价-日低)/(日高-日低)（日高低/VWAP 来自当日 1min 聚合）；
      score=(1-pct)*100；pct≤0.3 时机很好 / ≤0.6 一般 / >0.6 追高了。
  EXIT：对称 score=pct*100；pct≥0.7 卖在高位 / ≥0.4 一般 / <0.4 卖在低位。
  MISSED：T-5 日 EXPIRED/REJECTED 计划反事实 hypo=after/base-1；
      score=-hypo*100（躲过下跌=正分）。
幂等：review_id 唯一（ON CONFLICT DO NOTHING）。object_id 进 review_id。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

EVAL_LAG_DAYS = 5


def _eval_base_date(market: str, review_date: date) -> date | None:
    """评价基准日 = 决策日（review_date 往前第 5 个交易日）。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        row = (
            session.query(orm.BarDailyModel.trade_date)
            .filter(orm.BarDailyModel.market == market,
                    orm.BarDailyModel.trade_date < review_date)
            .distinct()
            .order_by(orm.BarDailyModel.trade_date.desc())
            .offset(EVAL_LAG_DAYS - 1)
            .first()
        )
        return row[0] if row else None
    finally:
        session.close()


def _closes(market: str, ts_code: str, d0: date, d1: date) -> tuple[float, float] | None:
    """(base日close, eval日close)；任一缺失返回 None。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        rows = (
            session.query(orm.BarDailyModel.trade_date, orm.BarDailyModel.close)
            .filter(orm.BarDailyModel.market == market,
                    orm.BarDailyModel.symbol == ts_code,
                    orm.BarDailyModel.trade_date.in_([d0, d1]))
            .all()
        )
        m = {r[0]: float(r[1]) for r in rows if r[1]}
        if d0 in m and d1 in m and m[d0] > 0:
            return m[d0], m[d1]
        return None
    finally:
        session.close()


def _insert(review_id: str, market: str, sv: str, review_date: date,
            decision_type: str, ts_code: str, score, metrics: dict, verdict: str) -> bool:
    # review_id 列限 varchar(64)：dr-entry-{fill_id}，而 CRYPTO fill_id 本身是压缩
    # 长串，再套前缀常超 64（id 长串窝案同款）→ 静默丢失。超限 md5 尾缀压缩。
    if len(review_id) > 64:
        import hashlib
        h = hashlib.md5(review_id.encode()).hexdigest()[:10]
        review_id = f"{review_id[:53]}-{h}"[:64]
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        stmt = pg_insert(orm.DecisionReviewModel).values(
            review_id=review_id, market=market, strategy_version_id=sv or "",
            review_date=review_date, decision_type=decision_type, ts_code=ts_code,
            score=score, metrics=metrics, verdict=verdict, created_at=utc_now(),
        ).on_conflict_do_nothing(index_elements=["review_id"])
        session.execute(stmt)
        session.commit()
        return True
    except Exception:
        session.rollback()
        logger.exception("decision_review insert failed %s", review_id)
        return False
    finally:
        session.close()


def review_candidates(market: str, review_date: date | None = None) -> int:
    """CANDIDATE：T-5 日候选池标的事后评估（行业内超额）。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    rd = review_date or date.today()
    d0 = _eval_base_date(market, rd)
    if d0 is None:
        return 0
    session = get_session()
    try:
        cands = (
            session.query(orm.CandidateAssetModel)
            .filter(orm.CandidateAssetModel.created_at >= d0,
                    orm.CandidateAssetModel.created_at < d0 + timedelta(days=1))
            .all()
        )
        if not cands:
            return 0
        # V2 无行业字段 → 等权全候选基准（V1 行业基准的退化形态）
        rets: dict[str, float] = {}
        for c in cands:
            pair = _closes(market, c.ts_code, d0, rd)
            if pair:
                rets[c.ts_code] = pair[1] / pair[0] - 1
        if not rets:
            return 0
        bench = sum(rets.values()) / len(rets)
        # v2_candidate_asset 无 sv 列，但每个 asset 经 pool_id 关联候选池的 sv → join 恢复，
        # 否则归因恒空、无法按策略统计、BEST/WORST 跨策略乱比。
        pool_ids = {c.pool_id for c in cands}
        pool_sv: dict[str, str] = {
            row[0]: row[1]
            for row in session.query(orm.CandidatePoolModel.pool_id,
                                     orm.CandidatePoolModel.strategy_version_id)
            .filter(orm.CandidatePoolModel.pool_id.in_(pool_ids))
            .all()
        }
        n = 0
        by_sv: dict[str, list] = {}
        for c in cands:
            if c.ts_code in rets:
                by_sv.setdefault(pool_sv.get(c.pool_id, ""), []).append(c)
        for sv, group in by_sv.items():
            scored = [(rets[c.ts_code] - bench) * 100 for c in group if c.ts_code in rets]
            for c, alpha in zip([g for g in group if g.ts_code in rets], scored, strict=True):
                verdict = "中等"
                if len(scored) >= 3:
                    if alpha == max(scored):
                        verdict = "BEST"
                    elif alpha == min(scored):
                        verdict = "WORST"
                ok = _insert(f"dr-cand-{c.id}", market, sv, rd, "CANDIDATE",
                             c.ts_code, round(alpha, 2),
                             {"raw_ret": round(rets[c.ts_code], 4),
                              "bench_ret": round(bench, 4)}, verdict)
                n += 1 if ok else 0
        return n
    finally:
        session.close()


def _day_range_vwap(market: str, ts_code: str, d: date) -> tuple[float, float, float] | None:
    """当日 (low, high, vwap)——1min 聚合；缺分钟线退化日K。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        rows = (
            session.query(orm.BarMinuteModel.high, orm.BarMinuteModel.low,
                          orm.BarMinuteModel.close, orm.BarMinuteModel.volume,
                          orm.BarMinuteModel.turnover)
            .filter(orm.BarMinuteModel.market == market,
                    orm.BarMinuteModel.symbol == ts_code,
                    orm.BarMinuteModel.trade_date == d)
            .all()
        )
        if rows:
            hi = max(float(r[0] or 0) for r in rows)
            lo = min(float(r[1] or 0) for r in rows)
            tv = sum(float(r[4] or 0) for r in rows)
            vv = sum(float(r[3] or 0) for r in rows)
            vwap = tv / vv if vv > 0 else float(rows[-1][2] or 0)
            return lo, hi, vwap
        row = (
            session.query(orm.BarDailyModel.high, orm.BarDailyModel.low, orm.BarDailyModel.close)
            .filter(orm.BarDailyModel.market == market,
                    orm.BarDailyModel.symbol == ts_code,
                    orm.BarDailyModel.trade_date == d)
            .first()
        )
        if row and row[0] and row[1]:
            return float(row[1]), float(row[0]), float(row[2] or 0)
        return None
    finally:
        session.close()


def _day_fills(market: str, side: str, rd: date):
    """当日指定方向的成交（join 四表链拿 sv）。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        return (
            session.query(orm.FillRecordModel, orm.OrderIntentModel.strategy_version_id)
            .join(orm.ExecutionRecordModel,
                  orm.FillRecordModel.execution_id == orm.ExecutionRecordModel.execution_id)
            .join(orm.OrderRequestModel,
                  orm.ExecutionRecordModel.request_id == orm.OrderRequestModel.request_id)
            .join(orm.OrderIntentModel,
                  orm.OrderRequestModel.intent_id == orm.OrderIntentModel.intent_id)
            .filter(orm.FillRecordModel.market == market,
                    orm.OrderIntentModel.intent_side == side,
                    orm.FillRecordModel.fill_time >= rd,
                    orm.FillRecordModel.fill_time < rd + timedelta(days=1))
            .all()
        )
    finally:
        session.close()


def review_entries(market: str, review_date: date | None = None) -> int:
    """ENTRY：当日买入成交的分位评估。"""
    rd = review_date or date.today()
    n = 0
    for fill, sv in _day_fills(market, "BUY", rd):
        rng = _day_range_vwap(market, fill.ts_code, rd)
        if not rng:
            continue
        lo, hi, vwap = rng
        price = float(fill.fill_price or 0)
        pct = min(1.0, max(0.0, (price - lo) / (hi - lo))) if hi > lo else 0.5
        score = round((1 - pct) * 100, 1)
        verdict = "时机很好" if pct <= 0.3 else ("时机一般" if pct <= 0.6 else "追高了")
        vwap_diff = price / vwap - 1 if vwap > 0 else None
        ok = _insert(f"dr-entry-{fill.fill_id}", market, sv, rd, "ENTRY",
                     fill.ts_code, score,
                     {"pct": round(pct, 3),
                      "vwap_diff": round(vwap_diff, 4) if vwap_diff is not None else None},
                     verdict)
        n += 1 if ok else 0
    return n


def review_exits(market: str, review_date: date | None = None) -> int:
    """EXIT：当日卖出成交分位（卖越高越好）。"""
    rd = review_date or date.today()
    n = 0
    for fill, sv in _day_fills(market, "SELL", rd):
        rng = _day_range_vwap(market, fill.ts_code, rd)
        if not rng:
            continue
        lo, hi, _vwap = rng
        price = float(fill.fill_price or 0)
        pct = min(1.0, max(0.0, (price - lo) / (hi - lo))) if hi > lo else 0.5
        score = round(pct * 100, 1)
        verdict = "卖在高位" if pct >= 0.7 else ("一般" if pct >= 0.4 else "卖在低位")
        ok = _insert(f"dr-exit-{fill.fill_id}", market, sv, rd, "EXIT",
                     fill.ts_code, score, {"pct": round(pct, 3)}, verdict)
        n += 1 if ok else 0
    return n


def review_missed(market: str, review_date: date | None = None) -> int:
    """MISSED：T-5 日被过滤(EXPIRED/REJECTED)计划的事后反事实。"""
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    rd = review_date or date.today()
    d0 = _eval_base_date(market, rd)
    if d0 is None:
        return 0
    session = get_session()
    try:
        plans = (
            session.query(orm.TradePlanModel)
            .filter(orm.TradePlanModel.market == market,
                    orm.TradePlanModel.plan_status.in_(("EXPIRED", "REJECTED")),
                    orm.TradePlanModel.created_at >= d0,
                    orm.TradePlanModel.created_at < d0 + timedelta(days=1))
            .all()
        )
        n = 0
        for p in plans:
            pair = _closes(market, p.ts_code, d0, rd)
            if not pair:
                continue
            hypo = pair[1] / pair[0] - 1
            score = round(-hypo * 100, 2)  # 躲过下跌=正分
            verdict = "过滤有效(躲过下跌)" if hypo < 0 else "错过上涨"
            ok = _insert(f"dr-missed-{p.plan_id}", market, p.strategy_version_id or "",
                         rd, "MISSED", p.ts_code, score,
                         {"hypo_ret": round(hypo, 4)}, verdict)
            n += 1 if ok else 0
        return n
    finally:
        session.close()


def _current_trading_date(market: str) -> date:
    """市场当前交易日（今日开市→今日，否则最近开市日）；无日历回退系统日期。

    与 positions._current_trading_date 同款：复盘评价日不能盲用 date.today()——CRYPTO
    tz=UTC、宿主亦 UTC，且需对齐 v2_market_calendar 的开市口径。
    """
    from datetime import datetime

    from zhice_os_v2.domain.market.registry import get_profile
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    try:
        today = datetime.now(get_profile(market).tz).date()
    except Exception:  # noqa: BLE001 - profile 缺失不阻断复盘
        today = date.today()
    session = get_session()
    try:
        row = (
            session.query(orm.MarketCalendarModel.trade_date)
            .filter(orm.MarketCalendarModel.market == market,
                    orm.MarketCalendarModel.trade_date <= today,
                    orm.MarketCalendarModel.is_open.is_(True))
            .order_by(orm.MarketCalendarModel.trade_date.desc())
            .first()
        )
        return row[0] if row else today
    finally:
        session.close()


def _daily_ready(market: str, rd: date) -> bool:
    """评价日 rd 的当日日K是否已入库（close 非空）。

    复盘 CANDIDATE/MISSED 用评价日收盘价算收益（`_closes`）；若在 fetch_daily(16:05)
    落库前跑，rd 日K缺失 → 全部算 0（8-27 复盘空产的时序真因）。就绪门在此拦截。
    """
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        return (
            session.query(orm.BarDailyModel.trade_date)
            .filter(orm.BarDailyModel.market == market,
                    orm.BarDailyModel.trade_date == rd,
                    orm.BarDailyModel.close.isnot(None))
            .first()
        ) is not None
    finally:
        session.close()


def run_decision_reviews(market: str, review_date: date | None = None) -> dict:
    """一次跑齐 4 类。评价日今日日K未入库时 fail-loud 跳过，不产脏 0（时序护栏）。"""
    rd = review_date or _current_trading_date(market)
    if not _daily_ready(market, rd):
        logger.error(
            "decision_review 跳过：market=%s 评价日 %s 当日日K未入库（时序护栏——复盘"
            "依赖当日收盘价；确认 fetch_daily 已在复盘前落库）", market, rd,
        )
        return {
            "market": market, "review_date": str(rd), "skipped": "daily_not_ready",
            "candidates": 0, "entries": 0, "exits": 0, "missed": 0,
        }
    return {
        "market": market,
        "review_date": str(rd),
        "candidates": review_candidates(market, rd),
        "entries": review_entries(market, rd),
        "exits": review_exits(market, rd),
        "missed": review_missed(market, rd),
    }
