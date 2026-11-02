# 本机只读预览模式 v1（/preview/hq）

- 日期：2026-11-03｜触发：A0 选择“本机只读预览（推荐）”
- 目的：开发未整体完成期间，免登录查看指挥部只读数据；**不替代正式登录页**
- 入口：`http://127.0.0.1:8734/preview/hq`
- 正式入口：`http://127.0.0.1:8734/hq`（仍登录）

## 一、范围与安全边界

| 项 | 约束 |
|---|---|
| 监听 | 8734 仅 `127.0.0.1`（已实测；非局域网/公网） |
| 页面 | 静态 `portal/preview_hq.html`，无写操作、无表单 |
| 数据 | **仅** `/api/preview/hq/{roles,agenda,directives,reports}` 四类只读 |
| 不接 | 审批、商业、预算、心跳、snapshot、A2A、任务写入 |
| 方法 | `limit_except GET { deny all; }` |
| 缓存 | `no-store`；`X-Robots-Tag: noindex` |
| 正式页 | `/hq`、`/api/hq/*` 鉴权不变（302/401） |

## 二、实现

- `portal/preview_hq.html`：只读驾驶舱（骨架数据来自上述四 API）。
- `portal/nginx.conf`：
  - `location = /preview/hq` → `try_files /preview_hq.html`，无 `auth_request`；
  - `location ~ ^/api/preview/hq/(roles|agenda|directives|reports)$` → 重写到 `/api/hq/$1` 代理 8091；
  - 精确白名单，其他 `/api/preview/hq/*` 落默认拒绝/404。

## 三、L2 五要素

1. **命令全文**：`docker exec ai-portal nginx -t && docker exec ai-portal nginx -s reload`（已执行，rc=0）。
2. **影响面**：仅新增 `/preview/hq` 与四条只读 API；不重启 8091；不修改认证代码/数据/任务；正式入口不变。
3. **不可逆点**：nginx 配置热加载；回滚即删除两段 location 后 reload，无数据变更。
4. **替代方案**：一次性 cookie/正式登录（已保留）；未选“完全关闭鉴权”。
5. **回滚预案**：删除 `portal/nginx.conf` 中 preview 两段 → `nginx -t` → `nginx -s reload`；页面文件可保留不用。

## 四、验证

- `/preview/hq` → 200（未登录）
- `/api/preview/hq/roles|agenda|directives|reports` → 200
- `/api/preview/hq/snapshot|commercial|approvals` → 404
- `/hq` → 302 `/login?next=/hq`
- `/api/hq/snapshot` → 401
- 数据：五角色 5、Directive 12、军团 9
- `unify_nav --check` PASS；route 292 零 diff；composition 6/6

## 五、退出条件

- 开发评审结束或 A0 要求恢复全登录 → 执行回滚；
- 预览不接入审批/商业写面，不作为生产入口。
