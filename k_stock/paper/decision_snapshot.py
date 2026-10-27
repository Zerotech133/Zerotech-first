# -*- coding: utf-8 -*-
"""D-1 决策快照（K-PLAN-V1-0.1，老田 2026-10-20 拍板「训练利器」地基）。

每次 exec_run 落单时，把「决策时全上下文」序列化落 decision_snapshots 表：
决策输入（run 文件全文）+ 决策输出（orders/skipped）+ 守卫语境（last_td/
allow_stale/min_conf）+ 版本号（X-6 纪律）。settle 成交后按 run_ref 关联
orders 即可配对后验结果 → 形成「上下文-决策-结果」训练样本对。

设计裁定：
- combo_id：四元组注册表（E-1，WO-QT-20261020-003）上线后，带 combo_id
  落单的组合=注册表组合 id；未带 combo_id 的旧路径仍= account_id（零变化）；
- schema v2/v3 加法演进：v2 加 combo（四元组印记，E-1）写 context_json
  ["combo"]；v3 加 pool（池快照印记，WO-QT-20261020-004 §6）写
  context_json["pool"]——取票时的池标识+版本+快照时间，供许褚运行时取票
  范围从契约+快照复现（计划 2.1a）；v3 同批加 signal（信号点消费印记，
  WO-QT-20261020-005 §6）写 context_json["signal"]——信号 id/触发规则/
  触发时刻/时效/触发时证据，信号点进跟单链的证据可回放锚点。均不删列
  不改约束，旧快照（v1，context 无 combo/pool/signal）读侧照常解析
  （by_ref 容忍缺失）；v3 同口径再加 plan_version（预案版本指针印记，
  WO-QT-20261020-006 §10）写 context_json["plan_version"]——快照引用
  的必须是信号时刻已生效版本（禁回写时点强制，见 capture）。
- 与落单同事务（exec_run commit 前写入）：快照失败 = 落单一并失败，
  fail-visible —— 禁「落了单没快照」的静默样本缺口（未落账≠假绿判例）；
- 仅 orders 非空且非 dry_run 时产快照（灰态零决策无样本信息量，不产）；
- 幂等：run_ref 唯一，同 run 重放（幂等跳过路径）不会产快照（orders 空）。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

SNAPSHOT_SCHEMA_VERSION = (
    3  # v2:+combo 印记（E-1）；v3:+pool 池快照印记（2.1a，加法演进）
)

# 池快照印记（WO-QT-20261020-004 §6）必填键：缺一即抛错（closed on failure）。
# pool_version 约定：快照型池=int 版本号；文件型池（日期+generated_at 软版本）=0。
POOL_STAMP_REQUIRED = ("pool_id", "pool_version")


class PoolStampError(ValueError):
    """池快照印记非法（缺必填键/类型不符）。"""


class SignalStampError(ValueError):
    """信号点消费印记非法（非 dict）。"""


class PlanStampError(ValueError):
    """预案版本指针印记非法（非 dict/缺必填键）。"""


# 预案版本指针印记（WO-QT-20261020-006）必填键：缺一即抛错（closed on
# failure）。effective_at 为该版本生效时点（须与 plan_versions 账一致，
# 时点强制校验归 plan_meeting_ledger，见 capture）。
PLAN_STAMP_REQUIRED = ("combo_id", "stock", "plan_version", "effective_at")


def normalize_pool_stamp(pool) -> dict:
    """校验并规范化池快照印记，返回可入 context_json 的 dict（原样浅拷贝）。"""
    if not isinstance(pool, dict):
        raise PoolStampError(f"pool_stamp 须为 dict: {type(pool).__name__}")
    for k in POOL_STAMP_REQUIRED:
        if k not in pool:
            raise PoolStampError(f"pool_stamp 缺必填键 {k}（契约 §6）: {sorted(pool)}")
    v = pool["pool_version"]
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise PoolStampError(
            f"pool_stamp.pool_version 须为非负 int（软版本池=0）: {v!r}"
        )
    return dict(pool)


_DDL = """
CREATE TABLE IF NOT EXISTS decision_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    combo_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    run_ref TEXT NOT NULL,
    run_path TEXT,
    run_content TEXT,
    orders_json TEXT NOT NULL,
    skipped_json TEXT NOT NULL,
    versions_json TEXT,
    context_json TEXT,
    UNIQUE(run_ref, account_id, strategy)
)
"""


def ensure_table(con) -> None:
    """建表（幂等）。

    短路规则（WO-QT-20261020-012 缺陷修复）：表已存在时**不执行任何 DDL**
    ——Python sqlite3 在 DDL 语句前会隐式提交当前未决事务，若在落单—快照
    同事务窗口内建表，落单会被提前提交，破坏 D-1「快照失败=落单一并失败」
    的 fail-visible 语义。表不存在时才建（首次建表发生在落单之前，无害）。
    """
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        ("decision_snapshots",),
    ).fetchone()
    if row is not None:
        return
    con.execute(_DDL)


def capture(
    con,
    *,
    run_path: Path | str,
    run_name: str,
    out: dict,
    account_id: str,
    strategy: str,
    versions: dict | None = None,
    context: dict | None = None,
    combo: dict | None = None,
    pool: dict | None = None,
    signal: dict | None = None,
    plan_version: dict | None = None,
) -> dict:
    """写入一份决策快照（调用方负责在其事务 commit 前调用）。返回快照行。

    combo：四元组印记（combo_registry.quad_stamp 产物，E-1）。给定时
    combo_id 列=注册表组合 id、context_json["combo"]=印记；不给时维持
    旧语义 combo_id=account_id（加法演进，调用方零改动兼容）。
    pool：池快照印记（normalize_pool_stamp 规范后写 context_json["pool"]，
    2.1a——取票时池标识+版本+快照时间，供许褚取票范围契约+快照复现；
    缺 pool_id/pool_version 即抛错，closed on failure）。
    signal：信号点消费印记（WO-QT-20261020-005，写 context_json["signal"]；
    字段校验归消费侧 signal_consumer，本层只拒非 dict——closed on
    failure）。
    plan_version：预案版本指针印记（WO-QT-20261020-006，写
    context_json["plan_version"]；v3 加法演进，不给=零变化）。禁回写
    强制（定纲：快照引用的必须是信号时刻版本）：① 结构校验——非 dict/
    缺必填键（combo_id/stock/plan_version/effective_at）即抛
    PlanStampError；② 时点强制——委托 plan_meeting_ledger.assert_plan_
    effective 断言该版本生效时点 ≤ 快照时刻（用会后版本冒充信号时刻
    版本在写入时即被拒并留痕，closed on failure；版本不在账同样拒）。"""
    ensure_table(con)
    ts = datetime.now().isoformat(timespec="seconds")
    ctx = dict(context or {})
    if combo:
        ctx["combo"] = combo
    if pool is not None:
        ctx["pool"] = normalize_pool_stamp(pool)
    if signal is not None:
        if not isinstance(signal, dict):
            raise SignalStampError(f"signal_stamp 须为 dict: {type(signal).__name__}")
        ctx["signal"] = dict(signal)
    if plan_version is not None:
        if not isinstance(plan_version, dict):
            raise PlanStampError(f"plan_stamp 须为 dict: {type(plan_version).__name__}")
        for k in PLAN_STAMP_REQUIRED:
            if k not in plan_version:
                raise PlanStampError(
                    f"plan_stamp 缺必填键 {k}（契约 WO-QT-20261020-006 §10）: {sorted(plan_version)}"
                )
        # 禁回写时点强制：plan 生效时点必须 ≤ 快照时刻（信号触发时已知道）
        import plan_meeting_ledger

        plan_meeting_ledger.assert_plan_effective(
            con,
            plan_version["combo_id"],
            plan_version["stock"],
            plan_version["plan_version"],
            at_time=ts,
            stage="capture",
        )
        ctx["plan_version"] = dict(plan_version)
    snap = {
        "snapshot_id": uuid.uuid4().hex,
        "ts": ts,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "combo_id": (combo or {}).get("combo_id") or account_id,
        "account_id": account_id,
        "strategy": strategy,
        "run_ref": run_name,
        "run_path": str(run_path),
        "run_content": Path(run_path).read_text(encoding="utf-8"),
        "orders_json": json.dumps(out.get("orders", []), ensure_ascii=False),
        "skipped_json": json.dumps(out.get("skipped", []), ensure_ascii=False),
        "versions_json": json.dumps(versions or {}, ensure_ascii=False),
        "context_json": json.dumps(ctx, ensure_ascii=False),
    }
    con.execute(
        "INSERT INTO decision_snapshots(snapshot_id,ts,schema_version,combo_id,"
        "account_id,strategy,run_ref,run_path,run_content,orders_json,"
        "skipped_json,versions_json,context_json) "
        "VALUES(:snapshot_id,:ts,:schema_version,:combo_id,:account_id,"
        ":strategy,:run_ref,:run_path,:run_content,:orders_json,"
        ":skipped_json,:versions_json,:context_json)",
        snap,
    )
    return snap


def by_ref(con, run_ref: str, account_id: str | None = None) -> dict | None:
    """按 run_ref 取快照（settle 后配对后验结果的入口）。

    同一 run 可落多个子账本（ref 按 @账户分段）→ run_ref 非全局唯一，
    联合唯一键 = (run_ref, account_id, strategy)；account_id 给定时精确匹配。"""
    ensure_table(con)
    if account_id:
        row = con.execute(
            "SELECT * FROM decision_snapshots WHERE run_ref=? AND account_id=?",
            (run_ref, account_id),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM decision_snapshots WHERE run_ref=? LIMIT 1", (run_ref,)
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    for k in ("orders_json", "skipped_json", "versions_json", "context_json"):
        d[k] = json.loads(d[k] or "null")
    return d
