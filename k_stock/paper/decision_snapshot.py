# -*- coding: utf-8 -*-
"""D-1 决策快照（K-PLAN-V1-0.1，老田 2026-10-20 拍板「训练利器」地基）。

每次 exec_run 落单时，把「决策时全上下文」序列化落 decision_snapshots 表：
决策输入（run 文件全文）+ 决策输出（orders/skipped）+ 守卫语境（last_td/
allow_stale/min_conf）+ 版本号（X-6 纪律）。settle 成交后按 run_ref 关联
orders 即可配对后验结果 → 形成「上下文-决策-结果」训练样本对。

设计裁定：
- combo_id：四元组注册表（E-1，WO-QT-20261020-003）上线后，带 combo_id
  落单的组合=注册表组合 id；未带 combo_id 的旧路径仍= account_id（零变化）；
- schema v2 加法演进：capture 新增可选 combo（四元组印记）→ 写入
  context_json["combo"] 并替换 combo_id 列取值；不删列不改约束，旧快照
  （v1，context 无 combo）读侧照常解析（by_ref 容忍缺失）；
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

SNAPSHOT_SCHEMA_VERSION = 2   # v2：+四元组印记（context_json["combo"]，加法演进）

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
    con.execute(_DDL)


def capture(con, *, run_path: Path | str, run_name: str, out: dict,
            account_id: str, strategy: str, versions: dict | None = None,
            context: dict | None = None, combo: dict | None = None) -> dict:
    """写入一份决策快照    (调用方负责在其事务 commit 前调用）。返回快照行。

    combo：四元组印记（combo_registry.quad_stamp 产物，E-1）。给定时
    combo_id 列=注册表组合 id、context_json["combo"]=印记；不给时维持
    旧语义 combo_id=account_id（加法演进，调用方零改动兼容）。"""
    ensure_table(con)
    ctx = dict(context or {})
    if combo:
        ctx["combo"] = combo
    snap = {
        "snapshot_id": uuid.uuid4().hex,
        "ts": datetime.now().isoformat(timespec="seconds"),
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
        ":skipped_json,:versions_json,:context_json)", snap)
    return snap


def by_ref(con, run_ref: str, account_id: str | None = None) -> dict | None:
    """按 run_ref 取快照（settle 后配对后验结果的入口）。

    同一 run 可落多个子账本（ref 按 @账户分段）→ run_ref 非全局唯一，
    联合唯一键 = (run_ref, account_id, strategy)；account_id 给定时精确匹配。"""
    ensure_table(con)
    if account_id:
        row = con.execute(
            "SELECT * FROM decision_snapshots WHERE run_ref=? AND account_id=?",
            (run_ref, account_id)).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM decision_snapshots WHERE run_ref=? LIMIT 1",
            (run_ref,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    for k in ("orders_json", "skipped_json", "versions_json", "context_json"):
        d[k] = json.loads(d[k] or "null")
    return d
