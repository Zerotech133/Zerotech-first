# HQ-05 解决方案：heartbeat 合并唤醒 v1

- 日期：2026-11-02｜状态：只读模型 + 纯合并契约已落地；调度器接入留 W3
- 依赖：HQ-02 任务板、现有 `schedules.json`/任务板
- 目标：同一 Agent 的多个 wake 合并为一次 heartbeat，避免重复执行/重复调用

## 一、合并规则

1. 以 `agent` 为合并键；同一 Agent 的多个 schedule/assignment 合并成 **一条 wake**；
2. 保留最早 `due`；合并 `reasons`（schedule/assignment/on_demand）与 `sources`；
3. 合并后每条 wake 只触发一次 heartbeat；已运行中的 Agent 新 wake 不再并发新 run；
4. 失败唤醒不静默丢弃：进 `failed_last_result`，由 HQ 告警。

## 二、实测（只读）

| 项 | 值 |
|---|---|
| 调度 total / enabled | **5 / 3** |
| 原始 wake | **5** |
| 合并后 | **3**（合并 2） |
| 同 Agent 多调度 | A0A（2 条） |
| last_result 失败 | **3/3 enabled**：`8093 不可达: HTTP Error 400` |
| 活跃任务 | DSH-Lead 2 单（doing/review） |

发现：
- **3 条 enabled 调度全部失败**（8093 不可达）→ 现有调度链有运行态问题，应在 W3 前排查（不属 HQ-05 写域）；
- A0A 两条调度（日报/周报）可合并唤醒，但需保留各自 cron 语义；
- DSH-Lead 两个任务应合并为一次 wake，避免双执行。

## 三、API/契约（W3 接调度器）

```
POST /hq/heartbeat/coalesce
  body: {wakes: [{agent, due, reason, source}]}
  resp: {agents: [{agent, due, reasons, sources, count}]}

GET /hq/heartbeat/status
  -> schedules/enabled/failed/merged_wakes/active_tasks
```

调度器接入点：
1. `legion_scheduler`/`schedules.json` 触发前先 coalesce；
2. Agent 运行中收到新 wake → 合并到 pending，不并发；
3. 每次合并/触发写 `dev_events`（heartbeat_coalesced/heartbeat_fired）；
4. 失败保留 last_result 并告警。

## 四、验收（DOD）

1. 同 Agent 2 wake → 1 heartbeat，due 取最早；
2. 不同 Agent 不合并；
3. 运行中 Agent 不并发新 run；
4. 失败调度 100% 可见；
5. 不启停调度器、不改 schedules.json（本阶段只读）。

## 五、当前落地

- `tools/hq_heartbeat.py`：合并契约 + 调度体检（JSON/Markdown）；
- `tools/tests/test_hq_heartbeat.py`：**4 passed**；
- 实测：5→3 合并；3/3 enabled 调度失败（8093）；A0A 多调度；DSH 2 活跃任务。

## 六、下一步

**HQ-06 统一 UI/manifest + `/hq` API/UI 接入**；随后 W3 正式开工（12-03）按 HQ-01~06 验收。
