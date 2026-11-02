# 超级个体商业 OS：五角色 → 军团 → 商业情报 → IP/行程/材料/PPT 一体化设计 v1

- 日期：2026-11-03｜定位：零界从“情报决策”扩展到“商业活动闭环”
- 核心原则：**一个驾驶舱、一条主链、五类对象、所见即所得；不新增第二平台/第二套入口。**

## 一、目标场景（一句话）

> 老田每天打开 `/hq`：看到商业信号与五角色意见 → 一键派给军团 → 看到进度/阻塞 → 一键生成内容/行程/材料/PPT → 在同一屏预览、确认、发出、留证。

## 二、现状资产（可复用，不重造）

| 能力 | 已有 | 缺口 |
|---|---|---|
| 五角色会诊 | `command_hq/roles.py`、`orchestrator/consensus/execution_gate`；RoleOpinion v1 | 无“意见→指令→军团”的持久对象与 UI |
| 会议/议程 | `hq_meeting` v2、`hq_agenda`、`hq_stats`；mission dispatch | 会议多日未开；无“决议→军团任务→回报”闭环 |
| 军团执行 | 6 compositions、77 席、task board、dev_events、mission worker | 无“按指令汇总的军团周报/进度卡” |
| 商业情报 | intel/news/JEV、Feishu/push/notify、commercial 底座 | 只按投资口径；无“对商务活动影响”评估与分级通知 |
| 个人 IP | 65 篇 gzh 草稿、视频/内容页、订阅 | 无选题→草稿→审核→发布→反馈 pipeline |
| 行程/材料/PPT | DSH `lark-calendar`、`office-pptx` 能力可用；docs/deliverables 资产 | 无 Itinerary/Binder/Deck 对象与驾驶舱入口 |
| 证据/治理 | 血缘/KG/证据包/Q2/L2 | 商业动作未强制 evidence 关联 |

## 三、五类核心对象（统一契约）

```yaml
BusinessSignal:   # 商业情报（不限于投资）
  id, source, title, url, published_at
  entities: [客户, 竞品, 渠道, 政策, 技术, 个人IP主题]
  relevance: {investment: 0-5, business: 0-5, ip: 0-5}
  materiality: high|medium|low
  confidence: 0-1
  suggested_action: notify|content|meeting|itinerary|none

ImpactAssessment: # 对商务活动的影响
  signal_id, affected: [客户/渠道/定价/合规/内容/IP/行程]
  magnitude, likelihood, urgency
  recommendation, owner, due

RoleOpinion:      # 已有 v1
  topic_id, role, position, recommendation, evidence[], risks[], conditions[]

Directive:        # 五角色→军团
  id, topic_id, decision, legion, owner, task_id
  acceptance, due, evidence_refs[]
  status: draft|dispatched|doing|review|done|blocked

LegionReport:     # 军团→HQ
  directive_id, legion, progress 0-100
  status: green|amber|red
  blockers[], evidence_refs[], eta, next_action
  reported_at

IPAsset:          # 个人IP
  id, topic, format(article|video|deck), stage(idea|draft|review|published)
  channel, publish_at, metrics{views,leads,conversions}
  linked_signal_ids[]

Itinerary:        # 商务行程
  id, date, event, location, objective
  attendees, materials[], prep_tasks[], status

Binder/Deck:      # 材料/PPT
  id, event/topic, materials[] (docs/links/evidence)
  outline[], template, deck_path, preview_images[]
```

## 四、主链（端到端）

```
商业信号/会议议题
  → 五角色会诊（RoleOpinion ×5）
  → 共识/田丰汇总（Directive）
  → 派单军团（mission/task）
  → 军团执行 + 进度回报（LegionReport）
  → 商务动作（通知/内容/行程/材料/PPT）
  → 证据 + 结果 + 记忆
```

### 4.1 五角色 → 军团（意见下发）

1. 会议/信号生成 `topic_id`；
2. 五角色并行产出 RoleOpinion（无证据禁止 verified）；
3. 田丰汇总：采纳/条件/反对 → `Directive`；
4. `Directive` 绑定军团、owner、acceptance、due；
5. 通过 mission dispatch 建任务（不自动 L2）；
6. `/hq` 显示“谁提出、谁同意、派给谁、何时验收”。

### 4.2 军团 → HQ（进度汇报）

- 自动：task 状态、dev_events、meeting decisions/orders 汇聚；
- 人工：军团每周 5 行报告（完成/阻塞/证据/风险/下周）；
- HQ：按 Directive 聚合为红黄绿；超期/无证据自动标红；
- 周报：自动生成“六军团周报”，不靠人肉拼。

### 4.3 商业情报：从“投资新闻”扩到“商务影响”

- 输入：news/JEV/政策/客户动态/竞品/平台规则；
- 过滤：先用**商业语境档案**（产品、客户、渠道、IP主题、近期行程）打分；
- 影响：`ImpactAssessment` 只把高 materiality 推给人；
- 通知：P0 即时（Feishu/critical）、P1 行动队列、P2 日报；
- 动作：一键生成内容选题/客户跟进/行程调整/材料补充；
- 去重：event_id + 静默窗 + 频控；延续 `notify_channels.json` 红线。

### 4.4 个人 IP 辅助

- 信号→选题→提纲→草稿（gzh）→审核→发布→反馈；
- 内容日历与商务行程联动（活动前预热、活动后复盘）；
- 已发布内容自动回填 metrics；高转化选题进入“可复用模板”。

### 4.5 商务行程 + 材料 + PPT

- 行程：从 Feishu Calendar（lark-calendar）或手工建 `Itinerary`；
- 材料：Binder 自动汇总 docs/链接/证据/历史 PPT；
- PPT：DSH `office-pptx` 生成；模板/大纲/预览图；
- WYSIWYG：同一屏预览文章/PPT/材料，确认后发布/发送/归档；
- 所有产出挂 `evidence_refs`，可追溯。

## 五、驾驶舱（所见即所得）

`/hq` 从“8 tabs”收为 **5 个任务型分组**：

```
今日：信号/议程/待决策/待派单/今日行程
决策：五角色意见 + 会议 + Directive
军团：6 军团进度/阻塞/周报
商业：客户/套餐/试用/内容/IP/渠道
资产：行程/材料/PPT（预览抽屉 + 一键生成）
```

右侧统一抽屉：详情、证据、聊天/意见、动作按钮；不跳页。

**一键动作**：采纳/派单/催办/生成选题/生成PPT/加入行程/通知客户；全部记录 owner/证据/结果。

## 六、分期落地（避免范围爆炸）

| 阶段 | 时间 | 交付 | 验收 |
|---|---|---|---|
| **P0 对象+主链** | 1–2 周 | Directive/LegionReport 契约；五角色→派单→进度；`/hq` 决策/军团两栏 | 一个真实议题走完闭环；有证据 |
| **P1 商业情报** | 2–3 周 | BusinessSignal/Impact；商业语境档案；分级通知 | 3 条非投资信号→影响→动作；误报可控 |
| **P2 IP** | 3–4 周 | IPAsset/内容日历；gzh 草稿→发布→反馈 | 连续 2 周内容节奏 |
| **P3 行程/材料/PPT** | 4–6 周 | Itinerary/Binder/Deck；office-pptx；预览 | 一次真实商务活动材料+PPT 一屏完成 |
| **P4 学习** | 持续 | 结果/经验入记忆；模板复用 | 决策/内容复用率提升 |

## 七、指标

- 决策→指令时间、指令关闭率、军团报告及时率；
- 信号→行动转化率、通知误报率/打扰率；
- 内容产出周期、发布→线索；
- 商务活动准备时间（材料+PPT）；
- “一屏完成率”（无跳转完成核心动作）。

## 八、边界与风险

- 不新增第二平台/第二入口；全部在 `/hq` + 现有 Feishu/办公能力上；
- 外部发送/行程写入属 L2，须确认/审批；
- 商业信息敏感：权限、脱敏、留痕；
- PPT/内容质量不能只靠模板，必须人审；
- 不把“自动生成”当“已发送/已发布”。

## 九、结论

这五件事不是五个功能，而是**同一条商业闭环的五个环节**：

> **信号 → 会诊 → 指令 → 军团 → 汇报 → IP/行程/材料/PPT → 证据/记忆**

最优落地不是再做页面，而是：
1. 先定 6 个对象契约；
2. 把现有五角色/会议/军团接进 Directive/LegionReport；
3. 用一个 `/hq` 驾驶舱做“所见即所得”；
4. IP、行程、材料、PPT 作为这条链的“输出动作”，按阶段接。
