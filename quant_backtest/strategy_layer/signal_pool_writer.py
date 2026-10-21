# -*- coding: utf-8 -*-
"""signal_pool.jsonl 实体化写入模块（六度选股 R0→R1→R2 生产切流 / 工位 #372）。

本模块归属门 N11（多池权威口径）与门 N10（门户导航接入）的下游落地件之一，
但**默认不自动落账**，仅暴露纯 stdlib 的能力供老田在 N11 拍板后的显式调用。

========================================================================
A. 当前并存 4 池现状（来自 N11 清单，仅文档化，不在本工位裁决）
========================================================================
1) xunyou_pool      —— 荀攸推池（荀攸席位独立选股 Agent 的产出池）。
2) todays_picks     —— 多策略聚合候选池（聚合各策略信号后的候选）。
3) bt-signals       —— 人工 watchlist × 技术信号（人工 watchlist 叠加技术信号）。
4) signal_pool.jsonl —— 下游统一消费入口池。**当前 ABSENT 未落盘，仅契约已定义**。

========================================================================
B. 推荐方案（option B = 分层流转；仅推荐，最终口径须经老田在 N11 门拍板）
========================================================================
各池各司其职，signal_pool.jsonl 作为下游统一消费入口（统一口径、统一 schema）：
    xunyou_pool ─┐
    todays_picks ┼─►（分层汇聚/归一化）─► signal_pool.jsonl ─► 下游消费链/复盘
    bt-signals  ─┘
- 上游各池继续按各自职责产出；signal_pool.jsonl 只做"统一消费入口"这一件事。
- 各池到 signal_pool.jsonl 的汇聚规则、权威层级、去重/时效口径，**须经老田在
  N11 门拍板后方可生效**；在此之前本模块不做任何自动落账。

========================================================================
C. 休眠默认红线（N11 拍板后已激活 SIGNAL_POOL_AUTO_WRITE=True）
========================================================================
- SIGNAL_POOL_AUTO_WRITE = True（见下）：本模块**import 时仍不自动创建或修改真实文件**。
- 真实 signal_pool.jsonl 的落账时机 / 权威层级须经老田在 N11 门批准。
- 本模块在 import 时**绝不自动调用 write_signals**；default_output_path() 仅
  返回约定路径，供用户/其它模块在获得批准后进行显式调用。
- 本工位不替老田在 N11 决定多池层级、不在 N10 决定导航挂载方式（入口仅作能力
  目录可发现的描述页，激活/挂载留待拍板）。

依赖：纯 stdlib（json / pathlib）。
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "SIGNAL_POOL_AUTO_WRITE",
    "default_output_path",
    "build_record",
    "write_signals",
    "POOL_ID",
    "POOL_VERSION",
]

# ---------------------------------------------------------------------------
# 池印记常量（与现有契约对齐：文件型池软版本 = 0，见 signal_pool.py 口径）
# ---------------------------------------------------------------------------
POOL_ID = "signal_pool"
POOL_VERSION = 0

# ---------------------------------------------------------------------------
# 休眠默认开关：真实 signal_pool.jsonl 落账须经老田在 N11 门批准。
# 本模块 import 时绝不自动调用 write_signals（import-safe 铁律不变）；
# 是否在本开关为 True 时由调用方显式落账，才是"已批准"的落地标志。
# ---------------------------------------------------------------------------
SIGNAL_POOL_AUTO_WRITE: bool = True

# === N11 决策记录（2026-10-21 老田拍板；同日经"按计划组织实施"指令激活）===
# 选定多池层级 = 方案 B（分层流转）：xunyou_pool / todays_picks / bt-signals 各司其职，
# signal_pool.jsonl 仅作下游统一消费入口（统一口径、统一 schema），不强制归并上游。
# 汇聚规则 / 权威层级 / 去重·时效口径仍须经 N11 批准后生效——本开关即"已批准"的落地标志。
# SIGNAL_POOL_AUTO_WRITE 已置 True（老田反复要求按计划推进）：决定后配置的 trial 试跑
# 将显式调用 write_signals() 把分层产物落 data/signal_pool.jsonl（幂等 upsert，重跑不累积）。
# 生产落账仍须 R2 切流批准（N12）；本开关仅授权 trial 试运行层面的分层实体化。
TIERING_PLAN: str = "B"
N11_DECISION_DATE: str = "2026-10-21"
N11_DECISION_BY: str = "老田"
# 方案 B 分层汇聚顺序（上游→入口）：仅文档化登记，落账前不生效。
TIER_LAYER_ORDER: tuple = ("xunyou_pool", "todays_picks", "bt-signals", "signal_pool")

# 约定落盘文件名（相对仓根）
_POOL_FILENAME = "signal_pool.jsonl"
_DATA_DIR = "data"


def default_output_path() -> str:
    """返回约定落盘路径（相对仓根）：data/signal_pool.jsonl。

    注意：本函数**不**在此模块导入时调用 write_signals，也不创建任何文件；
    仅作为约定路径字符串供用户/其它模块在获得 N11 批准后显式调用。
    真实文件当前 ABSENT，落账时机须经老田在 N11 门拍板。
    """
    return str(Path(_DATA_DIR) / _POOL_FILENAME)


def build_record(symbol, score, source, trade_date, meta=None) -> dict:
    """构造单条标准化 signal_pool 记录，便于下游统一消费。

    必填字段：
        symbol      —— 标的代码（如 600519.SH）。
        score       —— 归一化评分（浮点）。
        source      —— 上游来源池标识（xunyou_pool / todays_picks / bt-signals ...）。
        trade_date  —— 交易日期（YYYYMMDD 或 ISO 文本）。
        meta        —— 可选扩展字段字典（任何下游需要的附加上下文）。
    以及本模块统一附带的池印记（pool_stamp）与生成时刻（generated_at）。
    """
    record = {
        "symbol": symbol,
        "score": score,
        "source": source,
        "trade_date": trade_date,
        "meta": dict(meta) if meta is not None else {},
        "pool_stamp": {
            "pool_id": POOL_ID,
            "pool_version": POOL_VERSION,
            "generated_at": _now_iso(),
        },
    }
    return record


def _record_key(rec: dict) -> tuple:
    """去重键：同一 (trade_date, symbol, source) 视为同一条记录的更新。"""
    return (rec.get("trade_date"), rec.get("symbol"), rec.get("source"))


def write_signals(records: list[dict], path: str) -> int:
    """把 records 幂等写入 path（按 (trade_date, symbol, source) upsert），返回新写入条数。

    纯 stdlib（json / pathlib）。
    - 幂等：同键旧记录被本次同键新记录覆盖，**不会出现同键重复累积**；
      不同键记录追加保留——既满足"不丢历史"的契约，又防止 trial 重跑 / 生产
      重算造成无限累积的缺陷。
    - 原子写：先写 .tmp 再 replace，避免中途崩溃损坏 jsonl。
    - 空 records 列表安全返回 0，不创建 / 不改动文件。
    - 本函数不会在模块导入时被调用；真实落账须经 N11 批准后显式调用。
    """
    if not records:
        return 0
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    incoming_keys = {_record_key(r) for r in records}
    kept = []
    if p.exists():
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 跳过损坏行，保留好数据
                if _record_key(obj) in incoming_keys:
                    continue  # 被本次同键记录覆盖
                kept.append(obj)

    merged = kept + list(records)

    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in merged:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(p)
    return len(records)


def _now_iso() -> str:
    """生成时刻（ISO 秒级），纯 stdlib。"""
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")
