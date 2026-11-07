# P0-05 运行时账本 readiness 回执（v1）

- 日期：2026-11-07 16:22 +08:00
- 任务：`docs/plans/2026-11-06_鲁肃D1研发计划_v1.md:141-150`
- 结论：**NOT READY / 不造数**
- 验收状态：**不满足 P0-05**；meeting/training 两类账为空，paper 无真实 trade_id。

## 1. 本次做了什么

新增只读工具 `tools/d1_runtime_ledger_check.py`：

- 解析 `d1_decisions.jsonl`、`d1_handoffs.jsonl`、`meetings/d1/*.json`、`training/episodes.jsonl`。
- 校验 `d1_decision/v1`、`d1_handoff/v2`、`d1_meeting/v1`、`training_episode/v1` 的关键字段、ID 唯一性、paper_only/禁止执行字段。
- 追踪 decision→handoff→watch plan→trigger→confirmation→paper trade；对比 8091 `/api/d1/decisions|handoffs|meeting|training/episodes` 计数。
- 缺账/空账输出 `data_unavailable`，不生成样例、不写生产账本。

## 2. 当前结果

- `ok=true`，`ready=false`；`incomplete_books=[meetings, training]`。
- decisions：4 行，HOLD 4，4 个唯一 id，paper_only 全 true，无结构错误。
- handoffs：4；watch plans：4；triggers：4；confirmations：4。
- 链路：decisions_with_handoff=4、handoffs_with_plan=4、plans_with_trigger=4、triggers_with_confirmation=4，无断链。
- paper：positions=0、paper_trade_ids=0；空仓，无真实成交可追。
- meetings：目录不存在 → `data_unavailable=true`，0 行。
- training：episodes 文件不存在 → `data_unavailable=true`，0 行。
- 8091 四个只读 API 的计数均与本地账一致（`match=true`）。

## 3. 证据

`deliverables/d1_q2/20261107/`：

| 文件 | bytes | sha256 |
| --- | ---: | --- |
| `P0-05_readiness.json` | 3165 | `d02ac184554105d96723e8868afbe553b37f9b3de5f942b8218616fc415f6623` |
| `P0-05_readiness.md` | 1178 | `f97dffadd4b129a8a5556a60284d545e664c28599ce252f5b0280b5fea84c872` |

## 4. 缺口与下一步

1. **meetings**：由 D1 在真实投研会中创建 `meetings/d1/*.json`，完成 quorum/最终化；本工具不创建会议。
2. **training**：由真实 paper 成交/outcome 链生成 `training_episode`，写入 `training/episodes.jsonl`；不造 episode。
3. **trade 段**：paper 当前空仓，无 trade_id；需真实成交后再验证 decision→...→trade 全链。
4. 上述真实事件产生后重跑本工具，要求 `ready=true` 且三类账各 ≥1 条、API 可读、行数一致，才可申报 P0-05 通过。
