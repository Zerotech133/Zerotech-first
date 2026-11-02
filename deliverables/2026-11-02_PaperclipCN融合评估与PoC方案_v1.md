# Paperclip CN（penclipai/paperclip-cn）研究与结合方案 v1

- 日期：2026-11-02 18:30｜对象：`penclipai/paperclip-cn`（Paperclip CN fork）+ 上游 `paperclipai/paperclip`
- 本机条件：Node **v24.20.0**（≥24.11 要求）、**3100 空闲**、Hermes **8787 LISTEN**、8734 在
- **结论：可以结合，但定位是“HQ 管理控制面”，不是替代 Hermes/agent_platform 运行时；先隔离 PoC，验证后再决定是否上 `/hq/`。**

## 一、Paperclip 是什么（社区事实）

| 项 | 事实 |
|---|---|
| 定位 | **管理 AI 员工的公司/控制面**：org chart、goals、tasks、skills、permissions、approvals、budgets、run history；不是 Agent runtime |
| License | **MIT**；上游 GitHub **110k+ stars**、18k+ forks；TypeScript |
| 运行 | Node.js **24.11+**；默认单 Node 进程 + **内嵌 PostgreSQL**；`npx paperclipai onboard --yes`；默认 `127.0.0.1:3100`、`local_trusted`；也支持自带 `DATABASE_URL`/Docker |
| 数据 | 任务/文档/审批/加密 secrets 存储在本机；prompt/文件/工具结果发往所选模型 provider；**匿名遥测默认开**（`DO_NOT_TRACK=1` / `PAPERCLIP_TELEMETRY_DISABLED=1` 关闭） |
| CN fork | `penclipai/paperclip-cn`，fork 自上游；README 明确 "Paperclip CN"；中文定位；**内置 `hermes_local` 与 `hermes_gateway` 适配器** |
| 适配器 | claude-local、codex-local、opencode-local、**hermes-local / hermes-gateway**、cursor、gemini、grok、kimi、qwen、pi、**openclaw-gateway**、process、http、external plugin |
| 运行模型 | Agent 不常驻；按 **heartbeat** 唤醒（timer/assignment/on-demand/automation），每次短执行窗口；记录 status/token/cost/logs，live UI；会话可 resume |
| 安全 | 本地 CLI 适配器 **不经沙箱、在宿主直接跑**（官方 runtime 文档明示）；`local_trusted` 只适合本机；对外需认证模式 + HTTPS |

来源：
- [Paperclip CN](https://github.com/penclipai/paperclip-cn)
- [Paperclip 上游](https://github.com/paperclipai/paperclip)
- [开源自托管说明（MIT/安装/数据边界）](https://paperclip.app/product/open-source/)
- [Agent Runtime 文档（heartbeat/适配器/安全）](https://github.com/penclipai/paperclip-cn/blob/master/docs/agents-runtime.md)
- [hermes-gateway 适配器](https://github.com/penclipai/paperclip-cn/tree/master/packages/adapters/hermes-gateway)
- [hermes 适配器 README](https://github.com/penclipai/paperclip-cn/blob/master/packages/adapters/hermes/README.md)

## 二、和现有体系的匹配度

| 需求 | Paperclip | 结论 |
|---|---|---|
| 多 AI 军团叠加 | org chart/teams/roles/skills/permissions | ✅ 强 |
| 任务/派单/审批/预算 | 内建 tasks、approvals、budgets、goals | ✅ 强（补我们 HQ 缺口） |
| Hermes 结合 | **内置 hermes-local / hermes-gateway** | ✅ 天生契合 |
| 单宿主 | Node+PG 可同机 | ✅（但多一个 app/DB） |
| 单入口/统一 UI | 自带 React UI，默认 3100 | ⚠️ 需 8734 反代/SSO/embed；否则第二入口 |
| 不新增第二控制面 | 会与 agent_platform/portal HQ 重叠 | ⚠️ 需明确“Paperclip=HQ 管理层，agent_platform=执行层” |
| 安全 | local CLI 无沙箱、local_trusted、telemetry 默认开 | ⚠️ PoC 必须隔离/关遥测/不碰生产 secrets |
| 中文/本地化 | CN fork 提供中文 | ✅；但 fork 漂移/维护风险 |
| 许可 | MIT | ✅ |

**判定：Paperclip 不是“又一个 Agent 运行时”，而是我们一直缺的“HQ 控制面”的成熟开源实现；与 Hermes（运行时/聊天）+ agent_platform（执行/账本）是互补关系。**

## 三、推荐结合架构

```
8734 统一门户（唯一入口/认证/导航）
  ├─ /hq/      → Paperclip UI :3100（仅 127.0.0.1 反代，auth_request + trusted header）
  ├─ /hermes/  → Hermes WebUI :8787（聊天/运行时）
  └─ /legion   → 军团作战大屏（可逐步迁入 Paperclip）
Paperclip（HQ 管理层）
  ├─ 组织/角色/权限/预算/审批/goals
  ├─ Adapter: hermes_gateway → 127.0.0.1:8787（现有 Hermes）
  ├─ Adapter: hermes_local / codex / claude / qwen / kimi / openclaw（已有运行时）
  └─ DB: 内嵌 PG 或同一 PG 实例的独立 database/schema（不新建 DB 引擎）
agent_platform（执行/账本层）
  └─ 工单/调度/证据/质量门；与 Paperclip 通过 API 同步（不双写同一真相）
```

ZBF 映射：
- **K1** 席位/角色：Paperclip org chart，映射 77 席；
- **K2/K3** 任务/运行：Paperclip tasks/heartbeats + agent_platform 执行；
- **K4** 接口：`interface/paperclip-control`、`interface/hermes-gateway`；
- **K5** 数据：Paperclip PG 独立 database/schema；MCP 仍走 Hermes 13 server；
- **K6** 呈现：`/hq/` + `/hermes/` + `/legion`；
- **K8** 治理：Paperclip approvals/budgets/permissions + 我们的 L2/Q 门。

## 四、隔离 PoC（2 周，不碰生产）

| 步 | 动作 | 安全边界 |
|---|---|---|
| P0 | 在 `C:\项目\_sandbox\paperclip-cn` 用 CN fork 安装（Node 24.20 已满足）；`127.0.0.1:3100`；内嵌 PG 独立数据目录；`local_trusted`；**telemetry 关** | 不反代、不对局域网、不写生产 DB/secrets |
| P1 | 配 `hermes_gateway` adapter → `http://127.0.0.1:8787`；建 2 个 agent（A0/HQ、acad1 指挥官）；测 on-demand/assignment wake、run 日志/token/cost | 只读/测试任务，不执行生产变更 |
| P2 | 导入 6 军团/77 席/2046 任务样例（只读导出+导入）；对比 agent_platform 组织/任务；测审批/预算/中文 UI | 不与生产双向同步 |
| P3 | 把 Paperclip UI 通过 8734 `/hq/` 反代 + SSO/trusted header（PoC 后期）；测单点登录、无第二入口 | 需 A0 批 L2（新增反代/服务） |
| P4 | Go/No-Go：适配覆盖率、组织/任务一致性、审批/预算、SSO、安全、资源占用、中文可用性 | 通过才进 W3/W4 实施 |

**PoC 验收线**：
1. Hermes gateway 能连 8787，2 个 agent 可 heartbeat/执行/记录成本；
2. org/tasks/approvals/budgets 可用，中文 UI 正常；
3. 数据可导出/导入，不与 agent_platform 冲突；
4. 无生产 secrets、无对外端口、无遥测；
5. 资源占用可接受（Node+内嵌 PG）。

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| 第二 UI/入口/DB，违反单宿主 | 只做 `/hq/` 反代 + SSO；Paperclip 仅 127.0.0.1；DB 用现有 PG 实例独立 schema 或内嵌 PG，不新增 DB 引擎 |
| 本地 CLI adapter 无沙箱 | PoC 用独立 Windows 用户/低权限目录；只读凭据；生产 adapter 逐步迁移；审批门 |
| `local_trusted` 不安全 | 仅本机；对外前切认证模式 + HTTPS；8734 前置 auth_request |
| 遥测默认开 | 安装即 `DO_NOT_TRACK=1`/`PAPERCLIP_TELEMETRY_DISABLED=1` |
| CN fork 漂移 | PoC 用 CN fork 固定 commit；生产评估 **上游 + 中文 locale patch**，不长期跟 fork |
| 与 agent_platform/portal HQ 重叠 | 明确“Paperclip=HQ 管理层，agent_platform=执行/账本层”，先只读导入，不做双写 |
| 110k stars/6626 open issues | 固定版本/commit；不追 main；关键路径自带测试 |
| 预算/审批模型差异 | 用 Paperclip budgets/approvals 覆盖 HQ 层，M4/L2 四签仍在我们的治理门 |

## 六、建议决策

1. **批准隔离 PoC**（不接生产、不反代、不启 L2）：2 周内验证 `hermes_gateway` + org/tasks/approvals；
2. **暂不替换** Hermes/agent_platform/portal；PoC 通过后，Paperclip 作为 `/hq/` HQ 控制面；
3. **CN fork 仅 PoC**；生产优先上游 + 本地化补丁（避免 fork 漂移）；
4. **PoC 期间不迁移生产数据/不双写**；只读导出导入；
5. 通过后纳入 W3/W4：新 composition `hq-paperclip`、`interface/paperclip-control`、Q2/SLO、L2 反代审批。

## 七、结论

Paperclip CN 与我们“多 AI 军团叠加 + 统一框架/UI”的目标高度契合，且 **CN fork 内置 Hermes 适配器**，是本轮开源调研里最值得结合的项目。但它是**管理层**，不是运行时；直接替换会造成第二控制面/UI/DB。最优路径是：

> **PoC 隔离验证 → Paperclip 做 `/hq/` 管理层 + Hermes 做 `/hermes/` 运行时 + 8734 唯一入口/认证 → W3/W4 正适。**

现有 Hermes 继续作为对外的 AI 交互面；Paperclip 补上 org chart/tasks/approvals/budgets 这些我们自建 HQ 尚未成熟的能力。
