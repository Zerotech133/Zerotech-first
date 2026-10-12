# 平行空间策略智能体 v0.1

这是一个只用于研究和智能体互教的隔离配置。它培养的是能够分析市场状态、比较策略、给出风险说明和在缺数据时拒答的 AI 智能体。

本模式不要求生产许可证、本人离线签名、权限快照或交易日历认证，因为它不产生生产资格，也不发布原始资料。上述资料仍由生产训练治理链保留，不能用本模式绕过生产门禁。

## 能力边界

- 允许：策略分类、候选策略排序、风险等级、置信度、证据解释、缺数据拒答、AI 对 AI 的评审和蒸馏。
- 禁止：券商或交易所下单、模拟账本写入、成交撮合、持仓变更、收费上架和生产发布。
- 输入：结构化场景包、知识库片段、图表描述、指标序列、其他智能体的评审结果。
- 输出：`strategy_decision_packet`，只包含分析和研究结论。

## 训练路径

1. 用 `dataset-schema-v0.1.json` 组织场景和教师智能体答案。
2. 先做只读基线，检查候选策略、风险和拒答能力。
3. 再做教师智能体到学生智能体的蒸馏评测。
4. 只有计算环境满足要求时才评审 LoRA。当前机器为 CPU，`datasets` 和 `trl` 未安装，权重训练尚未开始。
5. 通过离线评测后，产物仍只能作为 `sandbox_only` 策略模型。

## 文件

- `agent-profile-v0.1.json`：智能体角色、输入输出和禁止接口。
- `dataset-schema-v0.1.json`：训练/蒸馏样本协议。
- `evaluation-matrix-v0.1.json`：离线评测维度和门槛。
- `training-job-manifest-v0.1.json`：当前训练准备状态。
- `distillation-evaluation-v0.1.json`：5 个跨市场场景的教师/学生离线蒸馏对照结果。
- `distillation-evaluation-full-v0.1.json`：12 个场景全量蒸馏结果和风险标签校准矩阵。

## 蒸馏评测

运行：

```text
python -X utf8 work/evaluate_parallel_space_distillation_20261013.py
```

当前评测固定抽取 `PS-001、PS-003、PS-005、PS-008、PS-012`，覆盖 A 股、指数、期货、虚拟币以及缺数据拒答。学生端使用独立规则基线，评测只比较策略选择、风险等级、拒答、失效条件和禁止动作协议，不复制教师答案，也不更新权重。

本轮 `distillation_baseline_pass=true`，策略一致率 1.0，风险一致率 0.8，缺数据拒答召回率 1.0。报告中的 `lora_review_status=pending_human_review` 只表示可以进入隔离 LoRA 方案评审；权重训练、生产资格、模拟成交、交易和商业化仍全部关闭。

全量复核命令：

```text
python -X utf8 work/evaluate_parallel_space_distillation_full_20261013.py
```

全量报告覆盖 12 个合成场景。若教师和学生在风险等级或缺数据拒答上出现分歧，报告会将样本列入 `calibration_gate.blocking_samples`，先校准标签再评审 LoRA。

