# 策略训练与金融执行分离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 为策略研究智能体增加独立的隔离训练资格，同时保持金融执行、模拟成交和商业化权限关闭。

**Architecture:** 在训练候选审计和策略验收报告两条现有只读链路中增加策略训练字段。策略训练资格只消费已核验的数据证据和影子验收结果，不调用交易接口、不写账本。

**Tech Stack:** Python 3.11、pytest、JSON 声明式清单、现有 `tools/devtask.py` 评审门。

**Spec:** `docs/superpowers/specs/2026-10-12-strategy-training-gate-design.md`

## Global Constraints

- `financial_execution_enabled=false`、`simulated_execution_enabled=false`、`commercialization_enabled=false`。
- 缺许可证、来源、哈希、离线审核回执、权限快照或时间切分时保持 `evaluation_only`。
- 不安装训练依赖，不启动 LoRA，不修改交易账本、雇佣记录或排程。

## Review Focus

- 策略训练开关关闭时不得误报 eligible；由训练候选回归测试覆盖。
- 策略训练开启但金融执行关闭时可以独立通过资格计算；由策略验收回归测试覆盖。
- 缺任一来源证据时必须阻断策略训练；由现有缺证据测试和新增断言覆盖。
- 报告字段必须区分策略训练与传统训练/生产资格；由 CLI 输出测试覆盖。
- 旧 manifest 和旧 matrix 不含新字段时必须向后兼容并默认关闭；由 fixture 测试覆盖。

### Task 1: 训练候选审计的策略资格字段

**Files:**
- Modify: `tools/audit_training_candidates.py`
- Test: `tools/test_audit_training_candidates.py`

**Interfaces:**
- Consumes: manifest `policy.strategy_training_enabled`（缺失按 `false`）。
- Produces: report `policy.strategy_training_enabled`、`strategy_training_eligible` 计数和每条记录的 `strategy_training_eligible`；传统字段保持兼容。

- [x] 写开关关闭和开启的红测试。
- [x] 运行新增测试确认失败。
- [x] 实现策略资格计算，禁止改变传统 `training_eligible`。
- [x] 运行训练候选测试并确认通过。

### Task 2: 策略分层验收报告字段

**Files:**
- Modify: `tools/audit_strategy_acceptance.py`
- Modify: `tools/test_audit_strategy_acceptance.py`
- Modify: `work/strategy-acceptance-v0.1/strategy-acceptance-matrix-v0.1.json`

**Interfaces:**
- Consumes: `policy.strategy_training_enabled`（缺失按 `false`）和现有策略证据。
- Produces: 顶层 `strategy_training_enabled`、`strategy_training_eligible`；金融执行、模拟执行、商业化字段独立保持关闭。

- [x] 写策略开关开启且证据完整的红测试。
- [x] 运行测试确认失败。
- [x] 实现仅在全部策略 `ready_for_shadow_review` 且金融执行关闭时返回策略训练资格。
- [x] 为正式矩阵显式登记 `strategy_training_enabled=false`。
- [ ] 运行策略验收和 CLI 测试并确认通过。

### Task 3: 回归与评审门

**Files:**
- Modify: `work/training-eligible-manifest-v0.1.json`
- Modify: `work/academy-source-evidence-v0.1/README.md`

- [x] 在 manifest 中显式写入 `strategy_training_enabled=false`。
- [x] README 说明策略训练与金融执行分离以及默认关闭状态。
- [ ] 运行聚焦 pytest、策略候选审计和三道评审门。
- [ ] 提交信息包含 `K-STOCK-185`，再执行 `python tools/devtask.py merge K-STOCK-185`。
