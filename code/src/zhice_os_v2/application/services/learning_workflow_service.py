"""学习闭环工作流（V1 learning_workflow 移植）。

聚合各策略版本的 DRAFT 学习案例 → ≥3 样本自动建实验（hypothesis 自动文案）
→ 该批 case 全部置 REVIEWED（天然防重）。实验状态机 RUNNING→CONCLUDED→
（触发晋升评估，半自动由 promotion 服务负责）→ARCHIVED。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

MIN_CASES = 3  # V1: 样本 < 3 跳过


def run_learning_workflow() -> dict:
    created, skipped = [], []
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        svs = [
            r[0] for r in (
                session.query(orm.LearningCaseModel.strategy_version_id)
                .filter(orm.LearningCaseModel.status == "DRAFT")
                .distinct().all()
            )
        ]
        for sv in svs:
            cases = (
                session.query(orm.LearningCaseModel)
                .filter(orm.LearningCaseModel.strategy_version_id == sv,
                        orm.LearningCaseModel.status == "DRAFT")
                .order_by(orm.LearningCaseModel.created_at.asc())
                .limit(50)
                .all()
            )
            if len(cases) < MIN_CASES:
                skipped.append({"sv": sv, "draft": len(cases)})
                continue
            wins = sum(1 for c in cases if str(c.outcome) == "WIN")
            win_rate = wins / len(cases)
            now = datetime.now(UTC).strftime("%Y%m%d%H%M")
            exp_id = f"auto_exp_{sv}_{now}"
            session.add(orm.ExperimentRunModel(
                experiment_id=exp_id, strategy_version_id=sv,
                market=cases[0].market or "CN", status="RUNNING",
                config={
                    "hypothesis": (f"基于 {len(cases)} 笔学习案例，胜率 {win_rate:.0%}。"
                                   f"样本结果: WIN={wins}, LOSS={len(cases) - wins}"),
                    "learning_case_ids": [c.learning_id for c in cases],
                    "auto_created": True,
                },
            ))
            for c in cases:
                c.status = "REVIEWED"
            session.commit()
            created.append({"sv": sv, "experiment_id": exp_id, "samples": len(cases),
                            "win_rate": round(win_rate, 3)})
        result = {"sv_checked": len(svs), "experiments_created": created, "skipped": skipped}
        logger.info("learning_workflow %s", result)
        return result
    finally:
        session.close()
