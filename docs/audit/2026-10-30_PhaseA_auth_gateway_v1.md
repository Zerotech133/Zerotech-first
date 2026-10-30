# Phase A auth-gateway v1

- 日期：2026-10-30
- 工单：`WO-LEGION-AUTHGW-20261030`
- 目标：**真实登录态 → SessionContext → IdentityContext** 闭环；不改 95 个现有页面，不把 token 放浏览器。

## 1. 边界

- `portal/auth_gateway.py` 是独立模块，与只读 BFF `api_zhice_bff.py` 分离；
- 挂在 8091 的独立路由：
  - `POST /api/auth/login`
  - `POST /api/auth/logout`
  - `GET /api/auth/me`
  - `GET /api/auth/status`
  - `GET /api/bff/identity/me`
- 仅认证/会话，不写业务数据、不交易、不派单。

## 2. 登录流程

```text
POST /api/auth/login {username,password}
  → 智策 8102 /api/v1/auth/login
  → 智策 /api/v1/auth/me 验证
  → identity_registry.resolve_source("zhice", user_id)
  → 仅已映射账号可登录（老田、zero_bff）
  → 内存 SessionStore 创建 opaque sid
  → Set-Cookie: zb_session=<sid>; HttpOnly; SameSite=Lax; Max-Age=3600
  → 响应只含 sub/expires_in，不含 token/密码
```

`GET /api/bff/identity/me` 返回 `SessionContext` + `IdentityContext`，不含智策 token。

## 3. SessionStore

- 进程内内存 `dict`，线程锁；
- session 字段：`sub / identity_context / csrf_hash / issued_at / expires_at / zhice_token`；
- zhice token 仅服务端使用；浏览器不可见；
- TTL 3600s；过期自动清理；
- 进程重启 session 丢失（Phase A 明确取舍）。

## 4. 安全控制

| 项 | 实现 |
|---|---|
| 密码 | 仅本次请求内存；不落盘/不写日志 |
| Token | 服务端 session；不下发浏览器 |
| Cookie | HttpOnly + SameSite=Lax；HTTPS 部署时需补 Secure |
| CSRF | `/me` 返回 csrf_token；logout 要求 `X-CSRF-Token` |
| 登录限流 | IP+用户名 5 次/5 分钟 |
| 错误 | 统一 `invalid_credentials`，不区分用户不存在/密码错 |
| 未映射账号 | 403 `identity_not_mapped`，不创建 session |
| 缓存 | 认证响应 `Cache-Control: no-store` |
| 写操作 | 无；仅认证 |

## 5. 验证

单元测试 `portal/tests/test_auth_gateway.py`（8 项）：

- 登录创建 HttpOnly session；
- `/me` 返回 IdentityContext；
- logout 必须带 CSRF，登出后 401；
- 未映射账号 403；
- 失败登录 401；
- 无 cookie 401；
- session store 清理；
- portal 路由已接线。

端到端 smoke（8091 重启后）：

```bash
python tools/auth_gateway_smoke.py
```

- 用 vault 内 `zero_bff` 服务账号登录（只读映射）；
- 输出 login/me/logout 状态与 `sub`，不打印密码/token/sid。

## 5.1 上线记录（2026-10-30 11:46）

- 8091 受控重启：`43064 → 29816`（任务 `/End` → 重启标记 → 启动任务；冷启动冒烟通过）；
- 8734 同源 smoke：`login 200 sub=zero_bound:bff / me 200 / logout 200`；
- 未登录访问 `/api/bff/identity/me` → 401；
- 登录响应只有 `ok/sub/expires_in`，无 token；
- `Set-Cookie` 含 `HttpOnly` + `SameSite=Lax`；
- `/api/bff/overview`、`/api/bff/identity/actors` 仍 200，只读 BFF 不受影响。

## 5.2 登录 UI 上线（2026-10-30 11:56）

- `/login` 新增 UI Kit 登录页；nginx 精确路由 `location = /login` 已 `nginx -t` + reload；
- 登录成功 → `/os-entry`；
- `/os-entry` 顶栏显示「已登录 · 零界只读 BFF」；工作平台面板显示身份/角色/过期；
- 登出 → 顶栏「未登录 · 登录」；
- Playwright 实测 login → me → logout 全通，无 pageerror；
- 截图：
  - `docs/visual_baseline/2026-10-30_login_ui/login_1440.png`
  - `docs/visual_baseline/2026-10-30_login_ui/entry_logged_in_1440.png`

## 6. 已知限制

- 登录 UI 已加；后续可把登录态接入页面保护/权限提示；
- 内存 session：8091 重启需重新登录；
- 未启用 HTTPS/Secure cookie；
- 仅支持智策已映射账号；菜鸟无本机认证；
- 不解决 refresh token；token 过期需重新登录；
- Phase B 用 Pocket ID/OIDC 替换本模块，不叠加。

## 7. 回滚

- 删除 `portal/auth_gateway.py` 与 `portal/portal_api.py` 中 2 处早分支；
- 8091 重启后回到无登录态（只读 BFF 不受影响）。
