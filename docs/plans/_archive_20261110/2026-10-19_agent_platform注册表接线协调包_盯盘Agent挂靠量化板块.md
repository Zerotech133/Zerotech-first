# agent_platform 注册表接线协调包：盯盘 Agent 挂靠量化板块（跨工位交接）
> STATUS: frozen（协调包历史；2026-10-31 W2 只读保留）



- 日期：2026-10-19
- 提出工位：量化盯盘上行汇报工位（watch_dispatch / W-Q 线）
- 接收工位：**参谋长线（A0）注册表工位**（registry 权属方，K-ORG-* 工单线）
- 性质：交接材料。**本工位不改 registry 任何文件**；以下 JSON 为建议稿，由注册表工位经 `onboard_agent.py` 唯一写入口审定后落盘。
- 依据：用户指令"把盯盘 Agent 挂到量化板块之下、登记量化板块接收 Agent"；层级汇报铁律（盯盘只向上级量化交易部门汇报，QUANT_UPSTREAM_REPORT 单一上行路）。

---

## 1. 现场事实（侦查结论，注册表工位可复核）

| 事实 | 位置 |
|---|---|
| 注册表目录共 14 个 team_*.json + org.json | `agent_platform/registry/` |
| 最新注册判例（schema 样板） | `team_quant_strategy.json`（ST1，K-ORG-189） |
| 组织汇报线表达方式 | org.json `departments[].lead` + `agents[]` + `lead_duty`，**无 reports_to 结构化字段** |
| 盯盘相关现有条目 | 仅风控部 R1/R2/R3（RiskGuard 风控哨，K-ORG-170，只在 org.json），**不是 WATCH 角色** |
| WATCH / QUANT_SECTOR 条目 | **均不存在，需新注册** |
| 身份有效期/注册引用字段 | 注册表**无** `registered_at` / `valid_until` / `registry_ref` 字段（全目录 grep 零命中） |
| Agent 侧硬校验 | `quant_backtest/watch_dispatch/agent_reporting.py` L43-64：`AgentIdentity` 必填 `registry_ref`、`registered_at`、`valid_until`；`agent_id` 须匹配 `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`；role 枚举 `{WATCH, QUANT_SECTOR}`；`registered_at < valid_until` |
| 目录端口 | `AgentDirectoryPort.resolve(agent_id, *, role, now) -> AgentIdentity | None`（agent_reporting.py L132-135）**无生产实现**，需新写适配器 |

## 2. 拟注册席位（建议稿）

### 2.1 WA1 —— 盯盘上行汇报席（role = WATCH）

```json
{
  "agent_id": "WA1",
  "name": "盯盘上行汇报席",
  "kind": "watch_upstream_reporter",
  "role_profile": "watch_monitor",
  "identity": {
    "agent_role": "WATCH",
    "registry_ref": "agent_platform/registry/team_watch.json#/agents[agent_id=WA1]",
    "registered_at": "<注册工位落盘当日 ISO8601 UTC>",
    "valid_until": "<registered_at + 180 天，到期续注>",
    "upstream_route": "QUANT_UPSTREAM_REPORT",
    "report_kinds": ["ANOMALY", "HEALTH"]
  },
  "skills": [
    {"id": "watch_upstream_report", "kind": "tool", "enabled": true}
  ],
  "constraints": {
    "execute": [],
    "danger": "deny",
    "note": "盯盘只观察/预警；所有上行仅走 QUANT_UPSTREAM_REPORT 单一上行路；不直连飞书/投研/交易所；三类动作权限恒 False"
  }
}
```

### 2.2 QS1 —— 量化板块上行受理席（role = QUANT_SECTOR）

```json
{
  "agent_id": "QS1",
  "name": "量化板块上行受理席",
  "kind": "quant_sector_upstream_receiver",
  "role_profile": "quant_sector_receiver",
  "identity": {
    "agent_role": "QUANT_SECTOR",
    "registry_ref": "agent_platform/registry/team_quant_upstream.json#/agents[agent_id=QS1]",
    "registered_at": "<同上>",
    "valid_until": "<同上>",
    "accepted_routes": ["QUANT_UPSTREAM_REPORT"],
    "ack_semantics": "受理回执=agent 级 ACK（AgentAck），受理不等于授权，不等于研判确认"
  },
  "skills": [
    {"id": "upstream_inbox_ack", "kind": "tool", "enabled": true}
  ],
  "constraints": {
    "execute": [],
    "danger": "deny",
    "note": "受理后由量化交易层决定三路分发（飞书信息/投研研判/交易所观察）；W-Q1 建设中，本席位先挂名待模块落地"
  }
}
```

### 2.3 挂靠关系（org.json 变更建议）

- `departments[dept="invest"]`（量化交易部）`agents[]` 追加 `"WA1"`、`"QS1"`（org.json 现 agents 为 `["T1","T2","N1","F1","D1","ST1"]`）。
- 汇报线沿用部门 lead/lead_duty 约定：WA1 发现任何问题（含自身故障 HEALTH）第一动作是向量化板块（QS1 受理席）汇报；建议在量化交易部 `lead_duty` 追加一句"QS1 负责签收 WA1 的 QUANT_UPSTREAM_REPORT 并出具 agent 级 ACK"。
- 可选建 `team_watch.json` / `team_quant_upstream.json`（照 team_quant_strategy.json schema），`parent_node_keys` 留空、`level: 1`；若两个 team 分建，`parent_node_keys` 校验仅限本 team，跨 team 挂靠仍由 org.json 部门名单承载——**采用哪种落法由注册表工位拍板，本包两种都兼容**。

### 2.4 注册表需新增的三个字段（硬需求，缺一则 AgentIdentity 无法实例化）

1. `registered_at`（UTC ISO8601，带时区）
2. `valid_until`（UTC ISO8601，须 > registered_at；到期目录解析按 `AGENT_NOT_CURRENT` fail-closed）
3. `registry_ref`（字符串，建议格式 `agent_platform/registry/<file>.json#/agents[agent_id=<ID>]`，长度 1-128）

另请保证 `agent_id` 与 `role_profile→agent_role` 映射可查询（适配器按 `resolve(agent_id, role=..., now=...)` 语义取数）。

### 2.5 人格化映射与盯盘营扩编预留（2026-10-19 追加，依老田口谕）

依 `deliverables/2026-10-19_量化板块三国命名体系_虚拟金融组织架构.md` v1.2（纯人格化层，零业务语义变更）：

- **WA1 = 许褚**（仲康）——盯盘营营官；建议条目加 `"persona": {"name": "许褚", "courtesy_name": "仲康", "dept": "盯盘营"}`。
- **QS1 = 司马懿**（仲达）侧上行受理席——量化板块总 Agent 司马懿的受理署；建议 `"persona": {"name": "司马懿", "courtesy_name": "仲达", "dept": "量化板块总署"}`。
- **多席扩编预留**：盯盘营未来按"股票太多一人盯不过来"扩编 WA2/WA3…（均 role=WATCH，同 schema，各自独立 registered_at/valid_until），信号经**营级信号池**聚合后仍走单一上行路（设计见 `docs/plans/2026-10-19_许褚盯盘营信号池设计_多Agent协作底座.md`）；注册表侧只需支持多 WATCH 条目，无额外 schema 需求。
- 上行链路确认：许褚（盯盘营）→ 司马懿（量化板块总 Agent）→ 军团田丰；本接线只落到第一跳。

## 3. 需注册表工位拍板的事项

1. WA1/QS1 的 `agent_id` 命名是否认可（亦可改名，适配器按注册结果对齐）。
2. `valid_until` 有效期默认 180 天是否合适，续注流程由谁负责。
3. WATCH/QUANT_SECTOR 席位建独立 team_*.json 还是仅入 org.json 名单。
4. 新增三字段的 schema 评审与回读校验（`Team.validate()` 是否需要扩展）。

## 4. 本工位（W-Q 线）承接的配套工作项

| 工作项 | 说明 |
|---|---|
| AgentDirectoryPort 适配器 | 读注册表 JSON → 构造 AgentIdentity（校验正则/角色/时序），解析失败返回 None（fail-closed），**不反向依赖 agent_platform**；落点建议 `quant_backtest/watch_dispatch/registry_directory.py`，待注册表字段定稿后实施 |
| ACK 出具端 | QS1 受理席的 AgentAckPort.verify 对应端（签收+独立核验），归 W-Q1 立项范围 |
| 回归 | 注册表落地后，端到端联测：WA1.report → outbox.plan → QS1 ACK → claim_and_confirm ACCEPTED_VERIFIED |

## 5. 边界与不变量（双方共同遵守）

- 注册表文件只由注册表工位经 `onboard_agent.py` 写入；本包仅为材料。
- WA1/QS1 均 `execute: []`、`danger: deny`；接线不改变"三类动作权限恒 False、dispatch_permitted 恒 False"的离线纪律。
- 本接线不触碰 strategy_registry 权重、线上 picks、PG、真实交易。
- 生产放行条件（A/B 来源资格、W-04/W-05/W-06 审批、外部身份与回执根）不因接线而视为解除。
