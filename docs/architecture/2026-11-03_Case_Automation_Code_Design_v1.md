# Case 自动化代码设计 v1

- 日期：2026-11-03｜范围：`/case` 一键自动化、Case 状态、审批门、下发
- 结论：**前端编排已能跑通，但 Case 状态仍只在本机；审批/下发没有服务端硬门。** 下一版必须把 Case 变成服务端实体，前端只做视图。

## 一、现状

```
/case 前端（localStorage：case 状态）
  ├─ /api/chat           真实云端 A0/五角色
  ├─ /api/hq/meeting/save   保存会议（服务端）
  ├─ /api/hq/meeting/dispatch 创建任务申请（服务端）
  └─ /api/auth/me        身份 / APPROVER

hq_case.py（CLI）另有完整状态机与审批硬门；与前端是两套状态。
```

### 已可用

- 一键自动化到方案：汇总 → 5 角色并行 → 失败重试 → 会议 → 方案；
- 人类审批后才允许页面下发；
- `tf=APPROVER`，会议观察席 R2/OPT1 已能上桌；
- 进度条/超时/会话身份提示。

### 缺陷

| ID | 级别 | 问题 | 影响 |
|---|---|---|---|
| C-01 | P0 | Case 状态在 localStorage | 刷新/换浏览器丢失；服务端无审计 |
| C-02 | P0 | 审批只是前端状态 | 绕过页面即可 dispatch；服务端不知道 Case 是否 APPROVED |
| C-03 | P0 | 前端与 hq_case.py 两套状态机 | 同一 Case 两本账，证据不一致 |
| C-04 | P1 | dispatch 不携带 case_id/revision | 无法把任务回执写回 Case |
| C-05 | P1 | 无幂等键 | 重复点击可能重复建会议/任务 |
| C-06 | P1 | 角色失败重试在前端 | 换设备无法恢复；重试记录不服务端 |
| C-07 | P2 | 前端 `/case` 与旧页面/CLI 入口未统一 | 用户不知道哪个是正式入口 |

## 二、目标架构

```
/case 前端（只渲染 + 触发）
        │  GET /api/hq/case/{id}
        ▼
Case Service（唯一状态机 + 唯一真源）
  ├─ 持久化：deliverables/cases/<case_id>/case.json
  ├─ 事件：case.events.jsonl（append-only）
  ├─ 模型编排：aggregate / review(5 parallel+retry) / propose
  ├─ 会议：hq_meeting.save
  ├─ 审批：approve（服务端校验 APPROVER/ADMIN，写 approval.json）
  └─ 下发：dispatch（仅 APPROVED；写 task_request + dispatch 回执）
```

### 领域模型

```json
{
  "schema": "hq/case/v2",
  "case_id": "CASE-YYYYMMDD-xxxx",
  "revision": 3,
  "status": "DRAFT|AGGREGATED|REVIEWED|MEETING|PROPOSED|APPROVED|DISPATCHED",
  "title": "",
  "materials": [{"path": "", "sha256": ""}],
  "a0_summary": "",
  "opinions": [{"seat": "AI1", "text": "", "binding": "", "error": null}],
  "meeting_id": "",
  "proposal": "",
  "approval": {"approved": true, "approver": "", "sub": "", "at": ""},
  "dispatch": {"request_id": "", "owner": "", "at": ""},
  "idempotency": {"aggregate": "", "review": "", "meeting": "", "propose": "", "dispatch": ""}
}
```

### API 契约

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/hq/case` | 登录 | 新建；材料仅引用/粘贴，不传本地路径 |
| GET | `/api/hq/case/{id}` | 登录 | 返回 Case + 事件 |
| POST | `/api/hq/case/{id}/aggregate` | 登录 | 田丰汇总 |
| POST | `/api/hq/case/{id}/review` | 登录 | 5 角色并行，服务端重试失败 1 次 |
| POST | `/api/hq/case/{id}/meeting` | 登录 | 保存会议，写 meeting_id |
| POST | `/api/hq/case/{id}/propose` | 登录 | 田丰方案 |
| POST | `/api/hq/case/{id}/approve` | APPROVER/ADMIN/OWNER | 服务端写审批记录；**唯一审批入口** |
| POST | `/api/hq/case/{id}/dispatch` | APPROVER/ADMIN/OWNER | 仅 APPROVED；幂等；创建任务申请 |
| GET | `/api/hq/case/{id}/events` | 登录 | 审计事件 |

### 服务端硬门

1. `approve` 必须校验会话身份（`/api/auth/me`），审批人=session sub；
2. `dispatch` 必须 `status=APPROVED` 且 `approval.approved=true`，否则 409；
3. `dispatch` 幂等键=case_id+revision；重复请求返回原 task_request；
4. 角色失败不得伪造文本；保留 error 字段；
5. 会议保存失败不得进入 PROPOSED；
6. 所有动作写 append-only 事件和 revision（CAS）。

## 三、本轮已落地的服务端部分（P0-1）

- `legion_command/case_api.py`：`deliverables/cases/<case_id>/case.json` 原子存储、revision、list/latest/get/approve；
- 8796 新增 `/api/case/list|latest|get/{id}|save|approve`，nginx `/api/case/` 登录保护；
- `/case` 每次 `save()` 同步 `/api/case/save`，无本地状态时从 `/api/case/latest` 恢复；
- 实测：清 localStorage 后刷新，从服务端恢复标题/case_id；`save` 200。
- **仍未做**：服务端 approval 身份校验、服务端 orchestrator、dispatch 硬门；这三项仍是 P0 剩余。

## 四、迁移步骤

1. **P0（今天）**：新增 Case Service 读/写 API + 文件存储；`/case` 改为读服务端；
2. **P1**：前端动作全部改调 API；localStorage 只保留草稿；审批/下发服务端硬门；
3. **P2**：服务端 orchestrator `autorun`（到方案），前端只显示进度；
4. **P3**：任务回执回写 Case（DISPATCHED→VERIFYING→DONE），周报统计闭环率。

## 五、本轮已修 BUG

- 五角色“假并行”改真并行；失败显示 x/5，支持单角色重试；
- 页面错误显示 5/5 导致会议/方案卡死；
- 会议观察席 R2/OPT1 等未注册导致 400 → 增加 OBSERVER_SEATS，并重启 8796 验证；
- `/case` 进度条/120s 超时/APPROVER 身份；
- `tf` 身份旧会话提示重新登录。

## 六、验收

- `agent_platform/tests/test_hq_meeting.py` 9 passed（含观察席）；
- `portal/test_case_page.py` 3 passed；
- 真实会议保存 200；观察席 R2/OPT1 实测 200；
- 代码设计验收以 Case Service API 落地为准，不用前端 localStorage 冒充持久化。
