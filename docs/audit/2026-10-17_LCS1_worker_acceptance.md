# LCS-1 首个 Worker 受控实战验收报告

- 验收时间：2026-10-17 07:00
- 课题：AC-20261017-LCS1
- 工单：legion-1017064344
- 执行成员：ACAD1（司马徽）
- 实际模型通道：Ollama
- 运行模式：controlled_shadow

## 1. 系统链路

| 环节 | 状态 | 证据 |
|---|---|---|
| 研究院课题读取 | 通过 | `agent_platform/data/academy/research_topics.json` |
| 阶段编排 | 通过 | `tools/academy_worker_plan.py` 输出 intake payload |
| Legion API 派工 | 通过 | 工单 `legion-1017064344` 已创建 |
| Agent 身份投影 | 通过 | 补齐 `team_academy.json`、7 个研究院 Prompt，bundle 36 agents |
| Worker 接单 | 通过 | `execution_status` 由 retry 转为执行 |
| 模型执行 | 通过 | 实际通道为 `ollama`，产出 1325 字符 |
| 产出落盘 | 通过 | `agent_platform/data/worker_outputs/legion-1017064344.md` |
| 自动验收 | 通过 | `status=done` |
| heartbeat | 通过 | Worker claim/finish 事件写入 observe 账本 |

## 2. 关键缺陷及修复

首次执行失败：

```text
KeyError: 'ACAD1'
```

根因：研究院成员虽然存在 HR 档案和 `org.json` 研究院部门，但没有职能部 `team_academy.json` 投影，Agent IDE bundle 不包含 ACAD1，统一模型路由无法加载身份卡。

修复：

- 新增 `agent_platform/registry/team_academy.json`。
- 补齐 7 个研究院成员的 Prompt：
  - `academy_director.md`
  - `strategy_researcher.md`
  - `backtest_researcher.md`
  - `commercialization_researcher.md`
  - `competitive_intelligence_researcher.md`
  - `ai_methodology_researcher.md`
  - 使用已有 `product_manager.md`
- 重建 `claude_agents.json` 和 `portal/agent_ide_bundle.json`。
- bundle 从 29 个 Agent 扩充到 36 个，Prompt 缺口归零。

## 3. ACAD1 研究结论

ACAD1 没有编造策略性能或产品化结论，而是判定：

```text
当前不能推进 Go 或产品化。
应先冻结策略 Spec、Gold Sample 和性能验收指标。
```

实际阻塞项：

1. LCS-1 具体算法逻辑尚未形成可执行 Spec。
2. 缺少用于复现的历史样本数据集。
3. 缺少基准性能指标和验收阈值。
4. 研究课题状态尚未自动绑定“数据依赖阻塞”语义。

## 4. 当前准入结论

### 系统层面：通过受控实战准入

已经证明数字军团可以完成：

```text
课题
→ 阶段编排
→ 派工
→ Agent 身份加载
→ 模型执行
→ 产出落盘
→ 自动验收
→ 工单 done
→ 观察账本记录
```

### 研究层面：暂缓推进

LCS-1 不能进入 hypothesis 阶段，必须先补齐：

- LCS-1 策略定义 Spec
- 图形算子和阈值
- 样本范围和时间边界
- 数据版本与血缘
- 基准策略
- 成本和滑点假设
- Go/No-Go 验收规则

当前决策不是 Go，也不是 No-Go，而是：

```text
补证 / 数据冻结
```

## 5. 下一阶段动作

1. 由 STRAT1 形成 LCS-1 策略 Spec。
2. 由 DATA1 冻结数据版本和样本范围。
3. 由 BACK1 建立回测登记和执行任务。
4. ACAD1 复核输入是否完整。
5. 通过后才推进 hypothesis → evidence → validation。

无人值守 Worker 排程继续保持关闭。
