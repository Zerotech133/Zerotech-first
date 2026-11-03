# tf APPROVER 授权回执 v1

- 时间：2026-11-03 11:52
- 授权人：老田（本会话明确选择「把 tf 升级为可审批的 APPROVER」）
- 变更：`zero_bound:tf` 角色 `VIEWER → APPROVER`；权限 `view → view + approve`；`owner_scope` 保持 `own`
- 不授予：ADMIN、manage、trade、risk、host/L2、数据写权限
- 原因：`/case` 自动化流程卡在人类审批；`tf` 是 VIEWER 时审批按钮被身份门禁用

## 验证

```json
{"sub":"zero_bound:tf","roles":["APPROVER"],"permissions":["view","approve"]}
```

- `/api/auth/me` 新登录返回 APPROVER；
- `/case` 显示「当前身份：tf · APPROVER · 可审批」；
- Playwright 全流程（模型/会议 mock）：新建→汇总→五角色→会议→方案→审批→下发 均走通；
- 真实 `/api/hq/meeting/save` 实测 200；田丰汇总真实云端调用通过；
- 旧会话需要重新登录才能拿到新角色。

## 回滚

将 `agent_platform/identity/identity_registry.json` 中 `zero_bound:tf` 改回：

```json
"roles": ["VIEWER"],
"permissions": ["view"],
"source_claims": {"zero_bound": {"roles": ["VIEWER"], "permissions": ["view"]}}
```

并重新登录；无需改代码/服务。

## 边界

- 该授权只解锁 Case 审批/下发按钮；
- 页面审批仍依赖 `/api/auth/me` 身份上下文；
- 正式服务端审批审计/双人签仍待后续；
- 不得把 APPROVER 当 ADMIN 使用。
