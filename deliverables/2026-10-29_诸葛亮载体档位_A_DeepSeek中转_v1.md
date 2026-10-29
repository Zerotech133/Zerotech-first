# 诸葛亮载体档位拍板：A（DeepSeek 中转正式接任）

- 日期：2026-10-29
- 决策人：老田（本会话授权 DSH-Lead 起草并落档）
- 范围：AI1 · 诸葛亮（OpenClaw 载体 / L5 咨询 / 8796 会议）
- 决策：**A — GPT-6 真身恢复前，正式接受 `deepseek/deepseek-chat` 中转为现役载体**。

## 1. 事实依据（2026-10-29 09:30-09:45 探测）

| 通道 | 探测 | 结果 |
|---|---|---|
| GPT-6 真身 `itokengate.com:443` | 连续探测 | **False** |
| ms1max 35B `192.168.5.156:8080` | 连续探测 | **False** |
| ms1max 177B `192.168.5.155:8080` | 连续探测 | **False** |
| DeepSeek `api.deepseek.com:443` | 连续探测 | **True** |
| OpenClaw carrier | `channel_status()` | `deepseek/deepseek-chat`，`openclaw_ready=true`，key 来自 OpenClaw secret store |

## 2. 决策内容

1. AI1 对外不再承诺“GPT-6 已可用”；所有答复、能力卡、健康卡均标 **DeepSeek 中转 / relay**。
2. 保留 `openai/gpt-6.0` 作为 **declared target** 与自动回切目标；GPT-6 恢复后由 `zhuge_carrier_switch.ps1` 自动切回，不改变调用方。
3. 正式定义当前载体档位为 **A（DeepSeek interim）**，有效期至：
   - GPT-6 跨境恢复并连续探测通过 24h；或
   - 老田另行改判（B 档：标“真模型不可用”）。
4. 不重启 8091/8796，不改 key，不动计划任务；本次仅落决策与标签口径。

## 3. 影响

- 正面：诸葛亮建议/咨询不再以“等待 GPT-6”为阻塞，能力卡与产品口径一致；
- 代价：与田丰（glm）的异族隔离只剩 DeepSeek vs GLM，异族多样性下降；
- 风险：DeepSeek 故障时只剩本地 9b 兜底，答案能力下降；已在 `comm_zhuge` 三级降级中如实标注。

## 4. 回滚

- 回滚只需把本决策标为 superseded，并恢复“GPT-6 pending”标签；
- 不涉及数据/代码/凭据变更，回滚成本为零；
- 自动回切开关保持开启，GPT-6 恢复后无需人工改配置。

## 5. 关联

- 证据：`C:\Temp\zhuge_carrier_switch.log` 09:30-09:40 探测；
- 代码：`portal/comm_zhuge.py`、`ops/zhuge_carrier_switch.ps1`；
- 后续：P0 golden set / tracing / 注入护栏（见同日 P0 执行记录）。
