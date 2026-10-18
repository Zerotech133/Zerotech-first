"""agent_platform 数据库基座：独立 engine + aw_* 建表 + 决策记录器。

复用 zhice 同一 Postgres 实例（只读消费 v2_* 表），aw_* 表用
AwBase.metadata.create_all 自建（不写 alembic，回滚=停进程，表留档不删）。
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn

from zhice_os_v2.agent_platform.config import AgentPlatformConfig, utc_now
from zhice_os_v2.agent_platform.models import AwBase, AwDecisionModel

if TYPE_CHECKING:  # 仅类型引用，避免运行时循环导入
    from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision

logger = logging.getLogger(__name__)


def make_engine(db_url: str | None = None, cfg: AgentPlatformConfig | None = None):
    """只读引擎（对 v2_* 表只 SELECT；aw_* 建表除外）。"""
    url = db_url or (cfg.db_url if cfg else None)
    if not url:
        url = AgentPlatformConfig().db_url
    return create_engine(url, pool_pre_ping=True)


_engine_cache: dict[str, object] = {}
_engine_lock = threading.Lock()


def shared_engine(db_url: str):
    """按 db_url 缓存的共享只读 engine（进程内单例，线程安全）。

    背景（PG 5446 连接耗尽）：临时 create_engine 用完即弃，连接归还到
    临时 engine 自己的池，engine 丢弃后连接关闭依赖 GC 时机 → 连接堆积。
    本函数保证同 url 全进程只建一个 engine，连接池随之收敛。
    """
    with _engine_lock:
        eng = _engine_cache.get(db_url)
        if eng is None:
            eng = create_engine(db_url, pool_pre_ping=True)
            _engine_cache[db_url] = eng
        return eng


def ensure_aw_columns(engine) -> list[str]:
    """模型↔DB 缺列自愈（幂等）。返回本次补齐的「表.列」清单。

    背景：metadata.create_all 只建新表不补列；T06/T07 给 AwDecisionModel/
    AwMethodModel 加 playbook_id 后，生产旧表缺列 → 写库 UndefinedColumn
    降级 JSONL（2026-10-18 实锤）。本函数只 ADD nullable 列+补单列索引，
    不改/删任何现有结构，可每次启动安全执行。
    """
    added: list[str] = []
    insp = sa_inspect(engine)
    with engine.begin() as conn:
        for tname, table in AwBase.metadata.tables.items():
            try:
                actual = {c["name"] for c in insp.get_columns(tname)}
            except Exception:  # 表不存在 → create_all 负责建，这里跳过
                continue
            for col in table.columns:
                if col.name in actual:
                    continue
                colddl = str(CreateColumn(col).compile(dialect=engine.dialect))
                conn.execute(text(f'ALTER TABLE "{tname}" ADD COLUMN {colddl}'))
                if col.index:
                    conn.execute(text(
                        f'CREATE INDEX IF NOT EXISTS "ix_{tname}_{col.name}" '
                        f'ON "{tname}" ("{col.name}")'))
                added.append(f"{tname}.{col.name}")
    if added:
        logger.warning("aw_* 缺列自愈补齐 %d 列: %s", len(added), ", ".join(added))
    return added


def create_aw_tables(engine) -> int:
    """建 aw_* 五表（幂等）+ 缺列自愈。返回本次新建的表数。"""
    before = set(AwBase.metadata.tables.keys())
    AwBase.metadata.create_all(engine)
    ensure_aw_columns(engine)
    return len(before)


class DecisionRecorder:
    """决策落库 aw_decision；DB 不可用时降级本地 JSONL 留痕（不阻塞主链）。

    - record(decision)：先入内存缓冲；满 batch 或 flush() 时写库。
    - 回测场景不传 engine → 纯内存模式（结果由 BacktestResult 承载）。
    """

    def __init__(self, engine=None, batch_size: int = 50,
                 jsonl_fallback: str | Path | None = None) -> None:
        self.engine = engine
        self.batch_size = batch_size
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False) if engine else None
        self.buffer: list[AwDecisionModel] = []
        self.memory_rows: list[dict] = []          # 无 engine 时的内存表（回测用）
        self.jsonl_path = Path(jsonl_fallback) if jsonl_fallback else None
        self.written = 0
        self.failed = 0

    def record(self, decision: AgentDecision) -> None:
        row = decision_to_row(decision)
        self.memory_rows.append(row_to_dict(row))
        if self._session_factory is not None:
            self.buffer.append(row)
            if len(self.buffer) >= self.batch_size:
                self.flush()

    def flush(self) -> None:
        if not self.buffer or self._session_factory is None:
            return
        try:
            session = self._session_factory()
            try:
                session.add_all(self.buffer)
                session.commit()
                self.written += len(self.buffer)
                self.buffer.clear()
            finally:
                session.close()
        except Exception as exc:  # 降级：JSONL 留痕，不阻塞
            self.failed += len(self.buffer)
            logger.warning("aw_decision 写库失败，降级 JSONL: %s", exc)
            self._dump_jsonl(self.buffer)
            self.buffer.clear()

    def _dump_jsonl(self, rows: Iterable[AwDecisionModel]) -> None:
        if self.jsonl_path is None:
            return
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row_to_dict(row), ensure_ascii=False, default=str) + "\n")


def decision_to_row(decision: AgentDecision) -> AwDecisionModel:
    """AgentDecision（领域对象）→ AwDecisionModel（ORM 行）。"""
    return AwDecisionModel(
        decision_id=decision.decision_id,
        ts_code=decision.ts_code,
        market=decision.market,
        bar_time=decision.bar_time,
        action=decision.action,
        score_sell=decision.score_sell,
        score_hold=decision.score_hold,
        votes_json=[v.to_dict() for v in decision.votes],
        regime_risk_off=decision.regime_risk_off,
        arbitration_path=decision.arbitration_path,
        model_verdict=decision.model_verdict,
        status=decision.status,
        fwd_ret_5d=decision.fwd_ret_5d,
        playbook_id=getattr(decision, "playbook_id", None),
        created_at=decision.created_at or utc_now(),
    )


def row_to_dict(row: AwDecisionModel) -> dict:
    """ORM 行 → 可 JSON 化 dict（内存表/JSONL 共用）。"""
    return {
        "decision_id": row.decision_id,
        "ts_code": row.ts_code,
        "market": row.market,
        "bar_time": row.bar_time.isoformat() if row.bar_time else None,
        "action": row.action,
        "score_sell": row.score_sell,
        "score_hold": row.score_hold,
        "votes_json": row.votes_json,
        "regime_risk_off": row.regime_risk_off,
        "arbitration_path": row.arbitration_path,
        "model_verdict": row.model_verdict,
        "status": row.status,
        "fwd_ret_5d": row.fwd_ret_5d,
        "playbook_id": getattr(row, "playbook_id", None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
