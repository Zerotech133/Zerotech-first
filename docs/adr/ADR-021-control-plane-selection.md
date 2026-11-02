# ADR-021：多 AI 军团控制面选型——原方案为主，Paperclip 仅作参考/隔离 PoC

- 状态：Proposed（待 A0/ARCH1 确认）
- 日期：2026-11-02
- 关联：ADR-016 单宿主、ADR-017 黄金路径、ADR-015 组合模型；W1 条件关闭、W2 L1 运行态、W3 12-03 起
- 决策人：A0 田丰（批）/ ARCH1 / DSH-Lead

## 背景

ZBF v1 已确定唯一宿主零界、唯一入口 8734、唯一认证、K0–K8 内核与 C1–C8 组合；W1 条件关闭、W2/L1 运行态签署；W3 尚未启动。团队研究了 Paperclip CN（MIT、110k+ stars、org chart/tasks/approvals/budgets、内置 `hermes_local`/`hermes_gateway` 适配器）是否可作为 HQ 控制面整体整合。

关键事实：
1. Paperclip 是**管理层/控制面**，不运行模型；需 adapter 执行；
2. 通用 SSO/OIDC 仍是**未合并 PR #3040**（issue #3028 open）；OSS 核心当前为 Better Auth 会话 + API key，`local_trusted` 无认证；
3. 默认新增 React UI + Node 服务 + 内嵌 PostgreSQL（3100），属第二控制面/第二 UI/第二 DB；
4. 本地 CLI adapter **无沙箱、在宿主直接执行**；遥测默认开；
5. CN fork 漂移 + 上游 6.6k open issues；
6. 加入它会挤压 11-04/11-16/12-02/12-03 硬节点，并与 agent_platform/portal HQ 重叠。

## 决策

1. **生产控制面继续采用原方案**：ZBF + Hermes（交互/控制面）+ 8734 门户 + 薄 HQ（自建），不引入 Paperclip 作为生产控制面/第二入口/第二 DB。
2. **吸收 Paperclip 的五个机制，而非平台**：
   - org chart/角色/权限；
   - 单任务原子 checkout（防并发重复）；
   - heartbeat 合并唤醒；
   - approvals（审批门）；
   - budgets/run cost（预算与运行成本）。
   这些进入 HQ 工作包（W3）。
3. **Paperclip 只做隔离 PoC/benchmark**：3100 本地、独立 PG、`local_trusted`、关遥测、`hermes_gateway` 接 8787；不反代、不接生产数据、不双调度；时间盒 2 周，不占 W3 关键路径。
4. **重新评估门（W4/2027-01-15 后）**，须同时满足：
   - Paperclip 通用 SSO/OIDC 已合并且稳定，或可安全接 8734 单登录；
   - 安全评审通过（无沙箱 CLI/遥测/秘密/权限）；
   - 自建 HQ 仍无法覆盖 org/approvals/budget/seat 管理；
   - 明确单一调度/单一数据真相，不双写。
   未满足则**不整合**，仅保留参考实现。
5. **不做事**：不整体替换 Hermes/agent_platform；不复制整仓；不设第二入口/认证/调度；不做 130 条工具全量迁移。

## 备选方案与否决理由

| 方案 | 否决/保留理由 |
|---|---|
| 整体采用 Paperclip 替换 HQ | 第二控制面/UI/DB；SSO 未合并；安全/迁移风险；挤压 W3 |
| 新建 Paperclip 项目整仓抽取 | 同样第二平台；隐藏耦合；维护双份 |
| 纯自研、不参考 Paperclip | 重复造 org/tasks/approvals/budget；慢 |
| **原方案 + 吸收机制 + Paperclip 隔离 PoC** | **采纳**：风险最低、时间线可控、保留未来选项 |

## 后果

- 正：单栈/单入口/单认证不变；W1/W2 不受影响；HQ 能力按 W3 补齐；Paperclip 作为保险。
- 负：HQ 自建需投入 W3 约 2–3 周；预算/审批 UI 需自研；短期不如 Paperclip 成熟。
- 触发复审：W3 出口（2027-01-15）或 Paperclip SSO PR 合并/安全评审通过时。
