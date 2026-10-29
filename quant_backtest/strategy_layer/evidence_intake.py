# -*- coding: utf-8 -*-
"""B1 证据链接线（M0 计划 B1）：融合裁决产出后自动写入证据链「入池」态。

定位
  SignalAggregator.run() 成功路径尾部（persist 完成/降级后的完成点）调用
  record_pool_intake()，把当日融合入池票（combined 行）逐条推进到证据链
  pool_in 态（evidence_chain 四态线性状态机：pool_in → authorized →
  pool_out → result），补齐「谁选的→凭什么」账本的第一环。

幂等（对齐 evidence_chain 判例）
  · entry_id = make_entry_id(策略集, trade_date, symbol)，策略集取
    contributions 键排序后 "+" 连接（同票同日同策略集唯一）。
  · 幂等键由 transition 内部按 payload_hash 生成：同日同票同内容重跑 →
    outcome=replayed，不重复记账；同 entry+state 不同内容 → ConflictError
    （粘性，须人工介入）——本层捕获后计为 conflict 跳过，绝不外抛。

closed on failure 铁律
  证据链写入是观测层，任何失败（模块缺失/库打不开/单票异常）一律内部
  捕获、打印 WARN 降级继续，绝不向融合主链抛异常、绝不阻断 run()。
  divergence=true 票照常入链（它也是裁决产物）。

测试安全联锁
  db_path 缺省解析顺序：显式入参 → EVIDENCE_CHAIN_DB 环境变量 →
  evidence_chain.DEFAULT_DB_PATH；但 pytest 进程（PYTEST_CURRENT_TEST
  存在）且未显式给路径时拒绝默认库，防测试污染真实证据账本。

用法：
  record_pool_intake("20261019", agg_result)              # 默认库
  record_pool_intake("20261019", agg_result, db_path=p)   # 显式库
"""

from __future__ import annotations

import os
from typing import Any, Optional
import logging

WARN_PREFIX = "[evidence-intake][WARN]"


def strategy_key_of(row: dict) -> str:
    """combined 行 → 策略集键（contributions 键排序 "+" 连接，确定性强）。

    entry_id 的 strategy 段要求同票同日可重现：排序连接保证键序稳定，
    同票重跑得到同一 entry_id，幂等重放才能命中。无贡献（异常行）
    返回空串，由调用方按失败计数跳过。
    """
    contribs = row.get("contributions") or {}
    return "+".join(sorted(str(k) for k in contribs))


def build_intake_payload(row: dict, trade_date: str, strategy_key: str) -> dict:
    """combined 行 → 证据链入池 payload（canonical_json 可序列化）。

    _meta 供 evidence_chain 的 evidence_state 投影列（symbol/trade_date/
    strategy）使用，随 payload 一起进哈希，同内容重跑哈希不变。
    """
    return {
        "symbol": str(row.get("symbol", "") or ""),
        "score": float(row.get("score", 0.0) or 0.0),
        "action": str(row.get("action", "") or ""),
        "confidence": float(row.get("confidence", 0.0) or 0.0),
        "contributions": row.get("contributions") or {},
        "divergence": bool(row.get("divergence", False)),
        "rationale": str(row.get("rationale", "") or ""),
        "_meta": {
            "symbol": str(row.get("symbol", "") or ""),
            "trade_date": str(trade_date),
            "strategy": str(strategy_key),
        },
    }


def _resolve_db_path(db_path: Optional[str]) -> Optional[str]:
    """入库路径解析：显式入参 → EVIDENCE_CHAIN_DB → 默认库。

    pytest 进程且未显式指定时返回 None（调用方据此跳过），防测试
    读写真实 data/evidence_chain.db。
    """
    if db_path:
        return db_path
    env_db = os.environ.get("EVIDENCE_CHAIN_DB", "").strip()
    if env_db:
        return env_db
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    import evidence_chain as ec

    return ec.DEFAULT_DB_PATH


def record_pool_intake(
    trade_date: str, agg_result: Any, db_path: Optional[str] = None
) -> dict:
    """把当日融合入池票逐条写入证据链 pool_in 态（closed on failure）。

    Parameters
    ----------
    trade_date : str
        交易日 YYYYMMDD（entry_id 与 _meta 投影用）。
    agg_result : AggResult | dict
        融合裁决结果；按 .combined / ["combined"] duck-type 取行，
        空集直接返回不触库。
    db_path : str | None
        证据链 sqlite 路径；None 走 _resolve_db_path 缺省解析。

    Returns
    -------
    dict
        计数摘要 {total, recorded, replayed, conflict, failed,
        db_path, error}；任何失败只落在 error/failed 计数，绝不抛出。
    """
    summary = {
        "total": 0,
        "recorded": 0,
        "replayed": 0,
        "conflict": 0,
        "failed": 0,
        "db_path": None,
        "error": None,
    }

    # 1) 取融合行（AggResult / dict 双形态；空集不触库直接返回）
    try:
        if isinstance(agg_result, dict):
            rows = list(agg_result.get("combined") or [])
        else:
            rows = list(getattr(agg_result, "combined", None) or [])
    except Exception as exc:  # noqa: BLE001
        summary["error"] = "read agg_result: %s" % str(exc)[:120]
        print(
            "%s 读取融合结果失败，跳过证据链入池: %s" % (WARN_PREFIX, summary["error"])
        )
        return summary
    summary["total"] = len(rows)
    if not rows:
        return summary

    # 2) 打开证据链库（惰性 import；失败降级返回）
    try:
        import evidence_chain as ec

        resolved = _resolve_db_path(db_path)
        if resolved is None:
            summary["error"] = (
                "skipped: pytest 进程未显式指定 db_path，"
                "拒绝写默认证据库（防污染真实账本）"
            )
            print("%s %s" % (WARN_PREFIX, summary["error"]))
            return summary
        summary["db_path"] = resolved
        store = ec.EvidenceChainStore(resolved)
    except Exception as exc:  # noqa: BLE001
        summary["error"] = "open store: %s" % str(exc)[:160]
        print(
            "%s 证据链库打开失败，跳过入池（closed on failure）: %s"
            % (WARN_PREFIX, summary["error"])
        )
        return summary

    # 3) 逐票入池（单票失败只计数告警，不中断其余票）
    for row in rows:
        try:
            symbol = str(row.get("symbol", "") or "").strip()
            skey = strategy_key_of(row)
            if not symbol or not skey:
                summary["failed"] += 1
                print(
                    "%s combined 行缺 symbol/contributions，跳过"
                    "（symbol=%r）" % (WARN_PREFIX, symbol)
                )
                continue
            payload = build_intake_payload(row, trade_date, skey)
            entry_id = ec.make_entry_id(skey, trade_date, symbol)
            out = store.transition(entry_id, ec.S_POOL_IN, payload, actor="aggregator")
            if out.get("outcome") == "replayed":
                summary["replayed"] += 1
            else:
                summary["recorded"] += 1
        except ec.ConflictError:
            # 同 entry+state 不同内容（粘性冲突）按已存在跳过，不覆盖
            summary["conflict"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["failed"] += 1
            print("%s 入池单票失败（不阻断其余票）: %s" % (WARN_PREFIX, str(exc)[:120]))
    try:
        store.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/strategy_layer/evidence_intake.py", exc)
    return summary
