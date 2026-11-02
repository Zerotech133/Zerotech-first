# HQ-06 解决方案：/hq 统一 UI/manifest + API/UI 接入 v1

- 日期：2026-11-02｜状态：**manifest + `/hq.html` 静态预览已落；实时 API/clean `/hq` 路由待 W3**
- 依赖：ADR-021、HQ-01~05、`portal/config/hq_manifest.json`
- 目标：一个 `/hq` 指挥面，5 个 tab，全部同源只读；不新增第二入口/DB/调度

## 一、页面/导航

```
/hq（8734 同源，OIDC）
 ├─ 组织（HQ-01）：部门/席位/角色/模型/工具/HR 缺口
 ├─ 任务（HQ-02）：doing/review/parked + WIP/stale + 原子 checkout
 ├─ 审批（HQ-03）：已决/风险/policy；不提供 HQ 直批
 ├─ 预算（HQ-04）：usage/API/token/credits/失败
 └─ 心跳（HQ-05）：5→3 合并、多调度、失败
```

- 统一 nav：挂在 `ops`/`quant` 下的“指挥部 `/hq`”；
- 页面三件套：`tokens.css`、`nav.js`、`shortscreen.js`；
- 路由基线：`portal_route_inventory --check` 同批 `--write-baseline`；
- 入口唯一：不新增端口/域名，不走 3100 Paperclip。

## 二、API 契约（只读）

| 方法 | 路径 | 数据层 |
|---|---|---|
| GET | `/hq/org` | hq_org.build_org |
| GET | `/hq/tasks` / `/hq/tasks/board` | hq_tasks.build_board |
| GET | `/hq/approvals` / `/hq/approvals/summary` | hq_approvals.build_approvals |
| GET | `/hq/budget` / `/hq/budget/alerts` | hq_budget.build_budget |
| GET | `/hq/heartbeat/status` | hq_heartbeat.build_heartbeat |

- 认证：由 8734 nginx `auth_request` 保护；未登录 401；
- 写操作：**本期不开放**；审批/工单仍走原通道；
- 错误：统一 `{ok:false, code, error}`；数据源缺失 fail-visible，不伪造。

## 三、W3 实施顺序

1. **HQ-06-a**：新增 `portal/api_hq.py`（只读 handler，挂 8091 同源 `/hq/*`）；不改现有 portal_api.py；
2. **HQ-06-b**：新增 `portal/hq.html`（5 tabs，fetch 上述 API；空/错状态明确）；
3. **HQ-06-c**：`unify_nav.py` 注入 + `portal_route_inventory --write-baseline`；
4. **HQ-06-d**：401/auth、短屏、移动端、Q2 健康（页面 200 + API 200）；
5. **HQ-06-e**：接 HQ-01~05 数据层；golden 快照对比；CI 测试。

## 四、L2 事项（需批准）

- 8091 重启加载 `api_hq.py`；
- nginx reload（若 `/hq` 需新增 location；若 8091 已有同源入口可复用）；
- 路由基线更新（代码/门禁，不需服务重启）；
- 五要素：命令/影响/不可逆/替代/回滚，按 ADR-012 执行。

## 五、验收（DOD）

1. 8734 未登录 `/hq` → 401；登录后 200；
2. 5 tabs 数据与 CLI 模型一致（golden）；
3. 页面无第二入口/端口；nav 仅一处 `/hq`；
4. route inventory/unify_nav/shortscreen 门全绿；
5. 只读：无写审批/工单路径；数据源缺失可见；
6. Q2：健康探针 + SLO + 回滚 + ≥1 测试。

## 六、当前落地

- `portal/config/hq_manifest.json`：页面/API/数据源/约束/W3 任务；
- `portal/hq.html`：5 tabs 静态预览（真实快照，只读）；实测 **`/hq.html` 200**；
- `tools/hq_snapshot.py`：合成 HQ-01~05 快照 → `portal/static/hq_snapshot.json`；
- `/hq` clean 路由仍 404，待 W3 nginx location + 8091 `api_hq.py`（L2）；
- `tools/tests/test_hq_manifest.py`：**4 passed**；
- HQ-01~05 数据层已具备（hq_org/tasks/approvals/budget/heartbeat）；
- 未改 8091/nav/路由/前端；W3 再接。

## 七、下一步

HQ-06 的实时 API/clean 路由在 W3（12-03 起）按 a→e 顺序实施；静态预览已可验；当前先完成 8093 诊断的 L2 修复，避免调度链带病进入 W3。
