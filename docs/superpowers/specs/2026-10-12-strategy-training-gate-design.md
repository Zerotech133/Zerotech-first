# 策略训练与金融执行分离设计

## 目标

让“策略研究智能体”的隔离训练资格独立于金融执行权限。策略训练只产生分析、候选、风险和拒答结果，不接触下单接口、模拟账本或收费发布链路。

## 约束

- `financial_execution_enabled`、`simulated_execution_enabled` 和 `commercialization_enabled` 必须保持 `false`。
- 策略训练仍需要许可证、来源日期回执、样本 SHA256、离线审核回执、权限快照和 `train/validation/test` 时间切分。
- 缺任一证据时，样本保持 `evaluation_only`，策略训练资格为 `false`。
- 当前 6 份知识库不提升资格；新增字段默认关闭。

## 设计

训练候选审计器新增 `strategy_training_enabled` 输入和 `strategy_training_eligible` 输出。它只决定是否允许后续隔离策略训练评审，不改变现有 `training_eligible` 或生产资格。

策略分层验收报告新增 `strategy_training_enabled` 和 `strategy_training_eligible` 两个字段。策略训练资格要求所有策略达到 `ready_for_shadow_review`，同时策略训练开关为真；金融执行、模拟执行和商业化字段仍独立且必须为假。

报告和清单都记录两套字段，避免调用方把策略训练资格误读成交易资格。默认配置继续关闭策略训练开关。

## 验收

- 完整证据 + 策略训练开关关闭：`strategy_training_eligible=false`，现有 `training_eligible=false`。
- 完整证据 + 策略训练开关开启 + 金融执行关闭：可得到 `strategy_training_eligible=true`，但 `financial_execution_enabled=false`、`production_eligible=false`。
- 任一证据缺失或策略验收阻断：策略训练资格为 `false`。
- 现有回归测试、Mixin、导航和端口评审门全部通过。
