# -*- coding: utf-8 -*-
"""种子决策落库：复用回测引擎重放历史 bar，决策批量写入 aw_decision。

目的：GraduationService 需要 aw_decision.fwd_ret_5d 已回填的样本才能评估
毕业权重。回测引擎天然带 T+5 前向收益（重放可知未来），所以"重放落库"
等价于把 30 天历史决策当作影子运行的真实留痕——毕业闭环第一次有了真实样本。

写入口径：
- 全部决策（SELL/HOLD/WAIT）都落——毕业判据按"方法投 SELL 的子集"归因，
  HOLD/WAIT 决策中的 SELL 票同样参与归因（方法的责任只算它主张卖出的局）。
- status：重放决策无人审，统一 BACKTEST_SEEDED（区别于线上 PENDING_CONFIRM）。
- 幂等：按 decision_id 唯一键 upsert（重复运行只补缺）。

用法（code 目录）：
  C:/项目/py312/python.exe -m tools.seed_decisions --market CRYPTO --days 30 --freq 15m
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
if str(CODE_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(CODE_ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="CRYPTO")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--freq", default="15m")
    ap.add_argument("--batch", type=int, default=2000, help="每批 flush 行数")
    args = ap.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from zhice_os_v2.agent_platform.backtest.engine import BacktestEngine
    from zhice_os_v2.agent_platform.config import AgentPlatformConfig
    from zhice_os_v2.agent_platform.models import (
        AwBase,
        AwDecisionModel,
    )

    cfg = AgentPlatformConfig()
    engine_db = create_engine(cfg.db_url, pool_pre_ping=True)
    AwBase.metadata.create_all(engine_db)          # 幂等建表
    Session = sessionmaker(bind=engine_db, expire_on_commit=False)

    bt = BacktestEngine(cfg=cfg)
    result = bt.run(market=args.market, days=args.days, freq=args.freq,
                    symbols=None, real_llm_sample=0)
    decisions = result.decisions
    with_fwd = sum(1 for d in decisions if d.get("fwd_ret_5d") is not None)
    print(f"[seed] 重放完成: {len(decisions)} 决策, 含 fwd_ret_5d {with_fwd} 条")

    Session = Session()
    existing = {r[0] for r in Session.query(AwDecisionModel.decision_id).all()}
    todo = [d for d in decisions if d["decision_id"] not in existing]
    print(f"[seed] 已存在 {len(existing)} 条, 待写入 {len(todo)} 条")

    inserted = 0
    for i, d in enumerate(todo):
        votes = list(d.get("votes_json") or [])   # AgentDecision.to_dict 的字段名
        Session.add(AwDecisionModel(
            decision_id=d["decision_id"],
            ts_code=d["ts_code"],
            market=d["market"],
            bar_time=d.get("bar_time"),
            action=d["action"],
            score_sell=float(d.get("score_sell", 0.0)),
            score_hold=float(d.get("score_hold", 0.0)),
            votes_json=votes,
            regime_risk_off=bool(d.get("regime_risk_off", False)),
            arbitration_path=d["arbitration_path"],
            model_verdict=d.get("model_verdict"),
            status="BACKTEST_SEEDED",
            fwd_ret_5d=(float(d["fwd_ret_5d"])
                        if d.get("fwd_ret_5d") is not None else None),
        ))
        inserted += 1
        if inserted % args.batch == 0:
            Session.commit()
            print(f"  ... {inserted}/{len(todo)}")
    Session.commit()
    print(f"[seed] 落库完成: {inserted} 行写入 aw_decision（status=BACKTEST_SEEDED）")

    # 落库后抽查
    total = Session.query(AwDecisionModel).filter(
        AwDecisionModel.market == args.market).count()
    fwd = Session.query(AwDecisionModel).filter(
        AwDecisionModel.market == args.market,
        AwDecisionModel.fwd_ret_5d.isnot(None)).count()
    print(f"[seed] aw_decision[{args.market}] 总 {total} 行, 已回填 fwd {fwd} 行")
    Session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
