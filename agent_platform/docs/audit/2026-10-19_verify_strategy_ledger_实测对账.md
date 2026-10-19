# verify_strategy_ledger 实测对账报告（a-verify / 任务 #118）

日期：2026-10-19。本文件为只读实测记录，零写面（未动任何账本）。

## 交付物状态

- `agent_platform/tools/verify_strategy_ledger.py`、`agent_platform/tests/test_verify_strategy_ledger.py`
  **已存在且已入库**（commit 55c9a4b3 初版 + 5bf028bd 审查 B2 修复：对账项③改真读
  evidence.db 实体，消除同源恒真）。本次按互斥协议未改动，转为验证既有实现。
- 复用的幂等键实现：`engine/strategy_registry.py:213` `verdict_key(strategy_id,
  symbol, trade_date)`（同源 import，只读）。
- 证据侧口径已核对 `adapters/evidence_collect.py:283` `_collect_strategy`：
  账本 `agent_platform/data/strategy/strategy_runs.jsonl`，证据行
  `source="strategy.runs"`、`payload.idempotency_key=verdict_key`，与工具判据一致。

## 测试结果（tmp 隔离根）

14/14 PASS（pytest tests/test_verify_strategy_ledger.py，1.35s）。四类场景全覆盖：

| 场景 | 结果 |
|---|---|
| 一致（三账本齐备） | exit 0，三项全 PASS |
| 缺 run_id（空值/影子无命中/影子账本缺失） | 对账项② FAIL，exit 1 |
| verdict_key 篡改（含缺 trade_date 无法重算） | 对账项① FAIL，exit 1 |
| 文件缺失（证据仓缺失/账本零好行） | 证据缺=FAIL exit 1；空账本=中性 exit 0 |

附加：字节级只读自证 PASS（对账前后三账本字节零变化）；CLI 无 --data-root 拒绝运行（exit 2）。

## 真仓只读实测（2026-10-19）

```
python tools/verify_strategy_ledger.py --data-root agent_platform/data
→ [中性] strategy_runs.jsonl 不存在或零好行（策略账本尚未首跑）
→ 对账行数 0，差异 0 条，exit 0
```

- 影子账本 `data/shadow/shadow_ledger.jsonl` 存在（7.4KB）；证据仓
  `data/evidence/evidence.db` 存在（64KB）。
- 策略账本待周一观察点首跑后此工具即为 M2 判据 1 的日常对账面。

## 已知口径偏差（报备 team-lead 裁决）

工单 CLI 形态写的是 `--runs/--ledger/--evidence` 显式路径 + `--root`；已入库实现
为单一 `--data-root`（=agent_platform/data，对齐 run_shadow DATA_ROOT /
strategy_registry._ledger_path 语义，必填、缺省拒绝运行）。理由见工具 docstring
"独立性边界"：三方账本共享同一 data-root，拆三个路径参数会引入根错配风险。
若需兼容显式三路径，属增量改动，须另行开单（本次不破互斥协议）。
