# 五角色晨会草案 · 2026-11-03

- 性质：**只读草案**（hq_meeting_prep 生成；未写生产会议/未派单/未调模型）
- 生成：2026-11-03T07:02:15+0800｜来源：meetings=8 议题=9 tasks=on directives=12 reports=ok
- 挑选规则：未派单 Directive > 红/黄军团 > 到期事项（今天+2 天）；候选 16 → 选中 3（上限 3）
- 红/黄军团：quant-strategy-line / acad1-legion, hq-audit, hq-command, hq-governance, huangzhong-acceptance｜未派单候选 11｜到期候选 2

## 五角色

| 角色 | 注册身份 | 职责 | 模型 |
|---|---|---|---|
| 田丰（TIANFENG） | A0 | 目标拆解、任务 DAG、派单、汇总、推进、最终执行编排 | glm/glm-5.3 |
| 诸葛亮（ZHUGE） | AI1 | 反向质询、pre-mortem、找反例、挑战前提 | openai/gpt-6.0 |
| 赵云（ZHAOYUN） | ZHAOYUN | 独立证据复核、审计、经验归档、进化建议 | deepseek/deepseek-v4-pro |
| 黄忠（HUANGZHONG） | HUANGZHONG | 验收标准、抽样、质量终检、建议放行/拒收 | glm/glm-5.3 |
| 关羽（GUANYU） | GUANYU | 代码审计、技术债、门禁、整改建议 | deepseek/deepseek-flash |

## 今日议程（最多 3 项）

### 1. [研发] 三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。

- topic_id：HQ-20261103-01｜owner：GUANYU｜legion：hq-audit
- due：2026-11-03｜状态：due_soon｜优先级：未派单 / 红黄军团 / 到期
- 期望产出：方案 + 代码/工单 + 验收命令（含 rc 与证据包路径）
- 证据要求：meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#decision:D1；evidence:baseline:2209/0；evidence:meeting:morning-20261028；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:hq-audit（blockers 逐条证据）；到期证据：due=2026-11-03（due_soon）；验收命令 + rc + 证据包路径（rc=0 不等于有产出）
- 议程项：① 背景与证据：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。（来源 DIR-MEET-hq-20261028-233044-D1）；② 五角色意见：田丰、诸葛亮、赵云、黄忠、关羽 各出 1 条 RoleOpinion（无证据不得 verified）；③ A0 决议：结论 + owner + due + acceptance（人工拍板，不代签）；④ 派单/关闭：Directive → task/order；未派单进下次晨会红标

角色提示（只读提示，不含角色意见）：
  - `TIANFENG`：【田丰｜TIANFENG / 注册身份 A0】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
  - `ZHUGE`：【诸葛亮｜ZHUGE / 注册身份 AI1】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
  - `ZHAOYUN`：【赵云｜ZHAOYUN / 注册身份 ZHAOYUN】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
  - `HUANGZHONG`：【黄忠｜HUANGZHONG / 注册身份 HUANGZHONG】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
  - `GUANYU`：【关羽｜GUANYU / 注册身份 GUANYU】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。

<details><summary>角色提示全文（JSON 见 --json）</summary>

**TIANFENG**

```text
【田丰｜TIANFENG / 注册身份 A0】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
职责：目标拆解、任务 DAG、派单、汇总、推进、最终执行编排
边界：不代替四签；不代替老田 L2；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#decision:D1；evidence:baseline:2209/0；evidence:meeting:morning-20261028；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：研发。
```

**ZHUGE**

```text
【诸葛亮｜ZHUGE / 注册身份 AI1】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
职责：反向质询、pre-mortem、找反例、挑战前提
边界：不拍板；不写生产代码；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#decision:D1；evidence:baseline:2209/0；evidence:meeting:morning-20261028；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：研发。
```

**ZHAOYUN**

```text
【赵云｜ZHAOYUN / 注册身份 ZHAOYUN】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
职责：独立证据复核、审计、经验归档、进化建议
边界：不做业务决策；不改生产；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#decision:D1；evidence:baseline:2209/0；evidence:meeting:morning-20261028；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：研发。
```

**HUANGZHONG**

```text
【黄忠｜HUANGZHONG / 注册身份 HUANGZHONG】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
职责：验收标准、抽样、质量终检、建议放行/拒收
边界：不代签；不修代码；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#decision:D1；evidence:baseline:2209/0；evidence:meeting:morning-20261028；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：研发。
```

**GUANYU**

```text
【关羽｜GUANYU / 注册身份 GUANYU】议题 HQ-20261103-01：三席失联（赵云/关羽/黄忠），仅A0、AI1有效，失联席今日补签。 本周决议：质量闸负责人今日交9项flaky根因+修复排期，报A0复核；指挥部明晨实跑“决议→派单→回执”闭环验收；P0否决人本周内由A0指定。
职责：代码审计、技术债、门禁、整改建议
边界：不自行合并；不碰生产；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261028-233044.json#decision:D1；evidence:baseline:2209/0；evidence:meeting:morning-20261028；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：研发。
```

</details>

### 2. [其他] ????[OBSERVE]:?????;?????????????

- topic_id：HQ-20261103-02｜owner：HUANGZHONG｜legion：huangzhong-acceptance
- due：2026-11-04｜状态：due_soon｜优先级：未派单 / 红黄军团 / 到期
- 期望产出：结论 + owner + due + acceptance
- 证据要求：meeting:agent_platform/data/meetings/hq/hq-20261029-054846.json#decision:D1；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:huangzhong-acceptance（blockers 逐条证据）；到期证据：due=2026-11-04（due_soon）
- 议程项：① 背景与证据：????[OBSERVE]:?????;?????????????（来源 DIR-MEET-hq-20261029-054846-D1）；② 五角色意见：田丰、诸葛亮、赵云、黄忠、关羽 各出 1 条 RoleOpinion（无证据不得 verified）；③ A0 决议：结论 + owner + due + acceptance（人工拍板，不代签）；④ 派单/关闭：Directive → task/order；未派单进下次晨会红标

角色提示（只读提示，不含角色意见）：
  - `TIANFENG`：【田丰｜TIANFENG / 注册身份 A0】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
  - `ZHUGE`：【诸葛亮｜ZHUGE / 注册身份 AI1】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
  - `ZHAOYUN`：【赵云｜ZHAOYUN / 注册身份 ZHAOYUN】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
  - `HUANGZHONG`：【黄忠｜HUANGZHONG / 注册身份 HUANGZHONG】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
  - `GUANYU`：【关羽｜GUANYU / 注册身份 GUANYU】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????

<details><summary>角色提示全文（JSON 见 --json）</summary>

**TIANFENG**

```text
【田丰｜TIANFENG / 注册身份 A0】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
职责：目标拆解、任务 DAG、派单、汇总、推进、最终执行编排
边界：不代替四签；不代替老田 L2；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261029-054846.json#decision:D1；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:huangzhong-acceptance（blockers 逐条证据）；到期证据：due=2026-11-04（due_soon）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**ZHUGE**

```text
【诸葛亮｜ZHUGE / 注册身份 AI1】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
职责：反向质询、pre-mortem、找反例、挑战前提
边界：不拍板；不写生产代码；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261029-054846.json#decision:D1；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:huangzhong-acceptance（blockers 逐条证据）；到期证据：due=2026-11-04（due_soon）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**ZHAOYUN**

```text
【赵云｜ZHAOYUN / 注册身份 ZHAOYUN】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
职责：独立证据复核、审计、经验归档、进化建议
边界：不做业务决策；不改生产；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261029-054846.json#decision:D1；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:huangzhong-acceptance（blockers 逐条证据）；到期证据：due=2026-11-04（due_soon）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**HUANGZHONG**

```text
【黄忠｜HUANGZHONG / 注册身份 HUANGZHONG】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
职责：验收标准、抽样、质量终检、建议放行/拒收
边界：不代签；不修代码；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261029-054846.json#decision:D1；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:huangzhong-acceptance（blockers 逐条证据）；到期证据：due=2026-11-04（due_soon）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**GUANYU**

```text
【关羽｜GUANYU / 注册身份 GUANYU】议题 HQ-20261103-02：????[OBSERVE]:?????;?????????????
职责：代码审计、技术债、门禁、整改建议
边界：不自行合并；不碰生产；只出意见，不执行、不签字、不改生产。
证据要求：meeting:agent_platform/data/meetings/hq/hq-20261029-054846.json#decision:D1；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）；legion_report:huangzhong-acceptance（blockers 逐条证据）；到期证据：due=2026-11-04（due_soon）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

</details>

### 3. [其他] 情报板块规划评审（#1024-0431）

- topic_id：HQ-20261103-03｜owner：laotian｜legion：unmapped
- due：未定（会上定）｜状态：正常｜优先级：未派单
- 期望产出：结论 + owner + due + acceptance
- 证据要求：council:portal/data/council_new_topics.json#topic:8；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
- 议程项：① 背景与证据：情报板块规划评审（#1024-0431）（来源 DIR-COUNCIL-080cc51d）；② 五角色意见：田丰、诸葛亮、赵云、黄忠、关羽 各出 1 条 RoleOpinion（无证据不得 verified）；③ A0 决议：结论 + owner + due + acceptance（人工拍板，不代签）；④ 派单/关闭：Directive → task/order；未派单进下次晨会红标

角色提示（只读提示，不含角色意见）：
  - `TIANFENG`：【田丰｜TIANFENG / 注册身份 A0】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
  - `ZHUGE`：【诸葛亮｜ZHUGE / 注册身份 AI1】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
  - `ZHAOYUN`：【赵云｜ZHAOYUN / 注册身份 ZHAOYUN】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
  - `HUANGZHONG`：【黄忠｜HUANGZHONG / 注册身份 HUANGZHONG】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
  - `GUANYU`：【关羽｜GUANYU / 注册身份 GUANYU】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）

<details><summary>角色提示全文（JSON 见 --json）</summary>

**TIANFENG**

```text
【田丰｜TIANFENG / 注册身份 A0】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
职责：目标拆解、任务 DAG、派单、汇总、推进、最终执行编排
边界：不代替四签；不代替老田 L2；只出意见，不执行、不签字、不改生产。
证据要求：council:portal/data/council_new_topics.json#topic:8；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**ZHUGE**

```text
【诸葛亮｜ZHUGE / 注册身份 AI1】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
职责：反向质询、pre-mortem、找反例、挑战前提
边界：不拍板；不写生产代码；只出意见，不执行、不签字、不改生产。
证据要求：council:portal/data/council_new_topics.json#topic:8；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**ZHAOYUN**

```text
【赵云｜ZHAOYUN / 注册身份 ZHAOYUN】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
职责：独立证据复核、审计、经验归档、进化建议
边界：不做业务决策；不改生产；只出意见，不执行、不签字、不改生产。
证据要求：council:portal/data/council_new_topics.json#topic:8；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**HUANGZHONG**

```text
【黄忠｜HUANGZHONG / 注册身份 HUANGZHONG】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
职责：验收标准、抽样、质量终检、建议放行/拒收
边界：不代签；不修代码；只出意见，不执行、不签字、不改生产。
证据要求：council:portal/data/council_new_topics.json#topic:8；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

**GUANYU**

```text
【关羽｜GUANYU / 注册身份 GUANYU】议题 HQ-20261103-03：情报板块规划评审（#1024-0431）
职责：代码审计、技术债、门禁、整改建议
边界：不自行合并；不碰生产；只出意见，不执行、不签字、不改生产。
证据要求：council:portal/data/council_new_topics.json#topic:8；meeting.hq 纪要（人工确认后落盘）：五日晨会 C2；五角色 RoleOpinion（每人 1 条；无证据不得 verified）；派单证据：Directive → task/order（owner/due/acceptance）
输出：按 RoleOpinion v1（hq_role_opinion/v1）给出 position（support|conditional|oppose|abstain）/recommendation/evidence[]/risks[]/conditions[]/escalate；无证据不得 verified。
注：本提示由 hq_meeting_prep 只读生成，**未调用模型、未产生角色意见**；意见由会上五角色本人（或授权代理）给出。议题类型：其他。
```

</details>

## 边界

1. 本草案**不写生产会议**：确认后在 `/hq` 晨会落纪要（hq_meeting 服务端记录）；
2. **不自动派单**：派单由 A0/田丰确认后走 Directive/task 单；
3. **角色提示不伪造意见**：prompts 只是意见征询提示；真实 RoleOpinion 由五角色给出；
4. 无真源数据时 fail-visible（rc=2），不以空草案冒充成功。
