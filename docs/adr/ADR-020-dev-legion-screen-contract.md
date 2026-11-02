# ADR-020 研发军团作战屏 v1 数据契约（只读）

- **状态**：**Accepted / M0 冻结（2026-10-28）**——老田会话授权「代确认」记录（见 docs/dev/legion_screen_target.md / milestones.md / review.md；AI 席位未单独回执，非人类签字）
- **日期**：2026-10-28｜**决策人**：ARCH1（DSH-Lead 代起草）
- **关联**：`deliverables/2026-10-27_任务拆解试点_研发军团作战屏v1_工作要素下发书.md`、`docs/dev/legion_screen_contract.json`、`WO-LS02-CONTRACT-20261027`

## 背景
老田/A0/ARCH1 需要一页看全研发军团（组织/11 席角色模型/在制/C6 指标/红线）。当前信息分散在 `org.json`、`.worktrees/tasks.json`、`agent_platform/data/dev/daily_metrics.json`、`docs/dev/org_chart.*`，且无统一只读 API。

## 决策
1. 新增 **只读** `GET /api/dev/legion`（新文件 `portal/api_dev_legion.py`，不改 `portal_api.py` 行数）；
2. 前端新页 `portal/dev_legion.html`（tokens/nav/短屏三件套）；
3. 数据契约冻结在 `docs/dev/legion_screen_contract.json`（v1），字段/类型/来源见下；
4. 只读边界：无写接口、不读凭据、不调生产；数据源缺失 → 空态 + `errors` 标注，**不 500**；
5. 页面不新增端口，接现有 8734 门户静态/路由模式（由 OPS1 在 LS-11 确认）。

## 契约（摘要；完整 JSON 见 `docs/dev/legion_screen_contract.json`）
- `schema`: `legion_screen_contract/v1`
- `endpoint`: `/api/dev/legion`（GET，`readonly=true`）
- `response`：
  - `generated_at`（ISO 字符串）
  - `department`：id/name/lead/lead_name/deputy/groups[]
  - `seats[]`：id/name/role_title/discipline/group/grade/model/org_role/report_to
  - `work_in_progress[]`：owner/tasks[{id,status,desc}]
  - `metrics`：window_days/dev_tasks_recent/dev_merged_recent/owner_legal_rate_recent/verification_rate/e3_trigger/dev_leadtime_p50_min
  - `redlines[]`：id/value/ok
  - `sources[]`：真源路径
- `errors`：缺失/损坏时的降级信息（`fallback="empty"`）

## 数据源
| 数据 | 真源 | 降级 |
|---|---|---|
| 组织/席位/组/汇报线 | `agent_platform/registry/org.json` | `department=null`、`seats=[]` |
| 在制任务 | `.worktrees/tasks.json` | `work_in_progress=[]` |
| C6 指标 | `agent_platform/data/dev/daily_metrics.json` | `metrics={}` |
| 红线 | 指标派生 | `redlines=[]` |

## 约束
- 只读：方法仅 GET；不写库/文件/事件（审计由访问日志承担）；
- 字段只增不改（v1 内）；新增字段必须向后兼容；
- 不暴露凭据/内部路径细节（只返回相对真源路径）；
- 性能：数据文件 ≤1MB，接口 <2s；缓存 ≤60s（由实现决定）。

## 后果
- DEV1/DEV5/DEV6/DEV2/3/4/7/8 按此契约并行；QA1 做契约测试；GUANYU 审只读边界；
- M0 冻结后 M1 才启动；字段变更走 ADR 修订，不静默改。
