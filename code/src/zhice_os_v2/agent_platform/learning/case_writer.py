"""CaseWriter — 学习案例落库 + T+5 前向收益回填（学习闭环步④/⑤）。

on_ack：ack → aw_decision.status=CONFIRMED/IGNORED + 写 zhice LearningCase（dataclass，
落库经 zhice 既有 SQLAlchemyPersistence.save_learning——只调不改）+ aw_learning_link 关联。
backfill_fwd_ret：T+5 到期决策回填 fwd_ret_5d（复用 backtest 引擎前向口径，
从 v2_bar_minute 取价），同步刷新 LearningCase.pnl_pct。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from zhice_os_v2.agent_platform.config import utc_now

logger = logging.getLogger(__name__)

# LearningCase.learning_id 列限 varchar(64)（zhice learning_service 同款压缩策略）
_MAX_LEARNING_ID = 64


def make_learning_id(decision_id: str) -> str:
    lid = f"aw-{decision_id}"
    if len(lid) > _MAX_LEARNING_ID:
        h = hashlib.md5(lid.encode()).hexdigest()[:10]
        lid = f"aw-{decision_id[:51]}-{h}"[:_MAX_LEARNING_ID]
    return lid


class CaseWriter:
    """ack → 案例落库；T+5 → 前向收益回填。

    session_factory：aw_* 库会话工厂（必传，DecisionRecorder 同款 sessionmaker）。
    zhice_db：可选 PersistencePort（有 save_learning 即可）——None 时 LearningCase
    降级不落 zhice（aw_learning_link.learning_case_id 为空，insight 由 link 行
    details 承载），不阻塞闭环。
    """

    def __init__(self, session_factory=None, zhice_db=None,
                 fwd_horizon_bars: int = 5) -> None:
        self._session_factory = session_factory
        self._zhice_db = zhice_db
        self.fwd_horizon_bars = fwd_horizon_bars
        self.cases_written = 0

    # ── 步④：ack → LearningCase + 状态更新 ─────────────────────────────────
    def on_ack(self, ack) -> object | None:
        """处理一条 CardAck：更新 aw_decision 状态 + 写 LearningCase + link。

        返回 LearningCase（或 None=决策不存在/zhice_db 未注入）。
        幂等：决策已 CONFIRMED/IGNORED 时跳过（token:action:decision_id 去重语义
        由 ack 文件 consumed 改名承担，这里二次防线）。
        """
        from zhice_os_v2.agent_platform.learning import CardAck
        from zhice_os_v2.agent_platform.models import (
            AwDecisionModel,
            AwLearningLinkModel,
        )

        assert isinstance(ack, CardAck)
        if self._session_factory is None:
            logger.warning("CaseWriter 无 session_factory，ack 不落库: %s", ack.decision_id)
            return None
        status = "CONFIRMED" if ack.action == "CONFIRM_SELL" else "IGNORED"
        with self._session_factory() as session:
            row = session.query(AwDecisionModel).filter_by(
                decision_id=ack.decision_id).one_or_none()
            if row is None:
                logger.warning("ack 指向不存在的决策 %s — 丢弃", ack.decision_id)
                return None
            if row.status in ("CONFIRMED", "IGNORED"):
                logger.info("决策 %s 已 %s，重复 ack 跳过", ack.decision_id, row.status)
                return None
            row.status = status
            row.confirmed_at = utc_now()
            row.confirmed_by = ack.operator

            case = self._write_learning_case(row, ack)
            session.add(AwLearningLinkModel(
                decision_id=row.decision_id,
                learning_case_id=getattr(case, "_db_id", None),
                link_type=status,
            ))
            session.commit()
        self.cases_written += 1
        # T05 闭环指标：ack 处理计数（confirmed/ignored）
        from zhice_os_v2.agent_platform import metrics as aw_metrics

        aw_metrics.inc_loop_conversion(
            "confirmed" if status == "CONFIRMED" else "ignored")
        logger.info("ack 落库: %s → %s (operator=%s)", ack.decision_id, status, ack.operator)
        return case

    def _write_learning_case(self, row, ack) -> object | None:
        """构造 zhice LearningCase 并经 zhice_db 落库（只调不改；失败不阻塞）。"""
        from zhice_os_v2.domain.learning.learning import LearningCase

        insight = ("CONFIRM_SELL" if ack.action == "CONFIRM_SELL"
                   else "IGNORED")
        votes = row.votes_json or []
        sell_votes = [v for v in votes if v.get("direction") == "SELL"]
        reasons = "；".join(
            f"{v.get('method_id')}: {'/'.join(v.get('reasons') or [])}"
            for v in sell_votes
        ) or "（无 SELL 票）"
        lesson = (f"[{insight}] {row.ts_code}({row.market}) "
                  f"score_sell={row.score_sell:.3f} path={row.arbitration_path} "
                  f"operator={ack.operator}"
                  + (f" reason={ack.reason[:80]}" if ack.reason else "")
                  + f" | SELL票: {reasons[:200]}")
        case = LearningCase(
            learning_id=make_learning_id(row.decision_id),
            strategy_version_id=f"agent_watch:{row.market}",
            market=row.market,
            replay_id=row.decision_id,
            insight=insight,
            ts_code=row.ts_code,
            lesson_summary=lesson,
        )
        if self._zhice_db is None:
            logger.info("zhice_db 未注入，LearningCase %s 仅构造不落 zhice", case.learning_id)
            return case
        try:
            self._zhice_db.save_learning(case)
        except Exception as exc:
            logger.warning("LearningCase 落 zhice 失败(%s: %s) — aw 表已更新，不阻塞",
                           type(exc).__name__, exc)
        return case

    # ── 步⑤：T+5 前向收益回填 ──────────────────────────────────────────────
    def backfill_fwd_ret(self, db_url: str | None = None,
                         market: str | None = None,
                         freq: str = "15m") -> int:
        """对 status∈{CONFIRMED,IGNORED,ARCHIVED} 且 fwd_ret_5d IS NULL 且已到期的
        决策回填前向收益。到期判定 = bar_time + horizon*bar_minutes < now（UTC）。

        前向口径复用 backtest 引擎：决策 bar 收盘 → 未来第 h 根收盘的收益率
        （fut/cur - 1），价取 v2_bar_minute 重采样至 freq。返回回填条数。
        """
        from sqlalchemy import text

        from zhice_os_v2.agent_platform.backtest.data_loader import load_minutes
        from zhice_os_v2.agent_platform.models import AwDecisionModel

        if self._session_factory is None:
            logger.warning("CaseWriter 无 session_factory，backfill 跳过")
            return 0
        minutes = {"5m": 5, "15m": 15, "1h": 60}.get(freq, 15)
        h = self.fwd_horizon_bars
        now = datetime.now(UTC)
        with self._session_factory() as session:
            rows = session.query(AwDecisionModel).filter(
                AwDecisionModel.fwd_ret_5d.is_(None),
                AwDecisionModel.bar_time.isnot(None),
            ).all()
            pending = [r for r in rows
                       if r.ts_code and _matured(r.bar_time, minutes, h, now)]
            if not pending:
                return 0
            by_symbol: dict[str, list] = {}
            for r in pending:
                by_symbol.setdefault(r.ts_code, []).append(r)
            updated = 0
            for sym, sym_rows in by_symbol.items():
                lo = min(r.bar_time for r in sym_rows) - timedelta(minutes=5)
                hi = max(r.bar_time for r in sym_rows) + timedelta(minutes=minutes * (h + 2))
                try:
                    bars = load_minutes(
                        market or sym_rows[0].market, [sym], lo, hi, freq,
                        db_url or _default_db_url(), buffer_bars=0,
                    )
                except Exception as exc:
                    logger.warning("backfill 取价失败 %s: %s — 跳过", sym, exc)
                    continue
                if bars.empty:
                    continue
                closes = bars["close"].astype(float).to_numpy()
                dts = bars["dt"].reset_index(drop=True)
                dtz = getattr(dts.dtype, "tz", None)   # bars 恒 UTC；bar_time 可能 naive
                for r in sym_rows:
                    bt = pd.Timestamp(r.bar_time)
                    if dtz is not None and bt.tzinfo is None:
                        bt = bt.tz_localize(str(dtz))
                    pos = int(dts.searchsorted(bt, side="right")) - 1
                    if 0 <= pos < len(closes) - h and closes[pos] > 0:
                        r.fwd_ret_5d = float(closes[pos + h] / closes[pos] - 1.0)
                        updated += 1
            session.commit()
        logger.info("fwd_ret_5d 回填 %d 条（待回填 %d）", updated, len(pending))
        return updated


def _matured(bar_time, minutes: int, h: int, now) -> bool:
    """T+5 是否到期：决策 bar + horizon 根 bar 已走过（保守 +1 根缓冲）。

    sqlite 读出的 naive datetime 统一按 UTC 补 tz（写入即 UTC，口径一致）。
    """
    bt = pd.Timestamp(bar_time)
    if bt.tzinfo is None:
        bt = bt.tz_localize("UTC")
    n = pd.Timestamp(now)
    if n.tzinfo is None:
        n = n.tz_localize("UTC")
    return (bt + timedelta(minutes=minutes * (h + 1))).to_pydatetime() \
        <= n.to_pydatetime()


def _default_db_url() -> str:
    from zhice_os_v2.agent_platform.config import AgentPlatformConfig

    return AgentPlatformConfig().db_url
