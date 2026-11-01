# 飞书卡片通道升级探测报告（2026-11-01）

> 任务：task-109 · 探测时间 2026-11-01T23:55:34+08:00 · **真实发送 1 次（限额 2）**

## 一、尝试结果

| 项 | 值 |
|---|---|
| 目标 | 内部测试（oc_6b97bee8b5340e071d0fc10cc6c8569f，chat_id，app=cli_aa3e4f1b58f85cdd） |
| 方法 | news_collector.send_feishu.send_content_to(群 chat_id, 'chat_id', feishu_payload, 'interactive') |
| payload | date=20261101，cards=5，candidates=1 |
| 结果 | **失败**（ok=False） |
| 通道 1 | OpenAPI：SEND_FAIL（silent window） |
| 通道 2 | lark-cli bot 群发：error code 230099（api/unknown） |

## 二、通道决策

- **keep_current_priority（群卡片通道未启用，保留现状）**：群卡片通道未启用，保留现状 |
- 当前默认优先级（不变）：
  1. 个人卡片：`send_content(content, 'interactive')`
  2. 失败 → 个人文本：`send_text(card_text)`
- 原因：1 次真实发送失败：OpenAPI 返回 silent window，lark-cli bot 群发兜底返回 230099（api/unknown）。按任务约定「失败则记录原因与前置条件、保留现状」。

## 三、升级前置条件

1. 飞书应用（cli_aa3e4f1b58f85cdd）需完成发布且可用范围包含「内部测试」群（silent window 通常=应用未发布/可用范围不含目标）。
2. 机器人需在「内部测试」群内，且群 chat_id（oc_6b97…）与该应用可见域一致。
3. 应用需具备群消息发送权限（im:message:send_as_bot 或等价），并完成权限审核。
4. push_targets 的 app 字段需与发送凭证应用一致（当前记录 app=cli_aa3e4f1b58f85cdd）。
5. 权限/发布就绪后，用 kb_daily_push.send_group_card(payload) 重试 1 次即可判定；成功后再切换 send_payload 优先级。

## 四、代码变更

- 文件：`tools/kb_corpus/kb_daily_push.py`
- 新增（探测用，默认不接入）：
  - resolve_group_target()：从 push_targets 读取「内部测试」群 id/type/app（不硬编码）
  - send_group_card()：群 chat_id + interactive 卡片探测（可注入 mock；成功不自动改默认优先级）
- **send_payload 默认优先级未改**（保持现状）；本次仅新增探测函数，默认发送优先级保持「个人卡片 → 个人文本」；待权限就绪后再切换为「群卡片 → 个人卡片 → 个人文本」。

## 五、测试覆盖

- resolve_group_target 按名读取 push_targets（mock 群目标）
- send_group_card 成功（mock 返回 True → channel=group_card）
- send_group_card 失败/异常（mock False/raise → 如实记录）
- 默认 send_payload 优先级不变（不调用群探测）
- 报告记录真实尝试/决策/前置条件

## 六、下一步

1. 按「前置条件」完成应用发布/可用范围/群权限/机器人进群配置；
2. 重试用 kb_daily_push.send_group_card(payload)（1 次真实调用）确认群卡片可用；
3. 确认成功后：send_payload 改优先级 群卡片→个人卡片→个人文本（群 chat_id 继续从 push_targets 读取）；
4. 更新测试为三层 mock，并保留一条真实成功发送结果。

> 本次为通道可行性探测（1 次真实发送），非生产推送；不构成投资建议。
