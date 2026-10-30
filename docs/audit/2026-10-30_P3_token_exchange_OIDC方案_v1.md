# P3 后半段：Token Exchange / OIDC 方案 v1

- 日期：2026-10-30
- 工单：`WO-LEGION-P3-OIDC-20261030`
- 前置：`IdentityContext` 注册表已上线（`/api/bff/identity/*` 200）；本单只做**方案与契约**，不改登录代码。
- 结论：**先做 BFF 会话桥（Phase A），再上 Pocket ID/OIDC + RFC 8693（Phase B）**；不把 Zhice token 放浏览器、不复制 autoSession。

## 1. 现状与缺口

| 组件 | 现状 | 缺口 |
|---|---|---|
| 零界 | 无登录页；身份只读 API 未鉴权，仅本机 | 没有真实登录态 |
| 智策 | 自有 HMAC JWT；前端 autoSession 自动注册、密码存 localStorage | 不适合直接作为浏览器身份 |
| 菜鸟 | 只有只读快照；无本机运行身份服务 | 无法作为当前 IdP |
| IdentityContext | 注册表 + 解析器 + BFF/CLI | 未与 session/token 绑定 |

目标：**登录后得到可验证的 SessionContext，内部含 IdentityContext；浏览器只拿 HttpOnly cookie，不拿 access token。**

## 2. 首选路径（两阶段）

### Phase A：BFF 会话桥（先落地，2-3 天）

```text
Browser
  │  POST /api/auth/session  (username/password, 仅本机/HTTPS)
  ▼
零界 auth-gateway / BFF
  │  POST 8102 /api/v1/auth/login
  │  GET  8102 /api/v1/auth/me   ← 验证 token
  │  identity_registry.resolve_source("zhice", user_id)
  ▼
服务端 session store（opaque sid，不存密码）
  │  Set-Cookie: zb_session=<sid>; HttpOnly; Secure; SameSite=Lax
  ▼
GET /api/bff/identity/me → SessionContext + IdentityContext
```

约束：

- 密码只在登录请求内存中出现，不落盘、不写日志；
- Zhice token 只存服务端会话（内存/SQLite），不下发浏览器；
- 浏览器不写 localStorage；
- 会话 TTL 1 小时，登录轮换 sid；
- 只保护 `/api/bff/identity/me` 与未来管理面，不阻塞现有 95 页；
- 不改 Zhice autoSession，Phase A 期间明确禁用/不使用。

优点：不引入新 IdP，今天可做；缺点：仍使用 Zhice 密码，不是真正 SSO。

### Phase B：Pocket ID/OIDC + Token Exchange（1-2 周）

```text
Browser
  │  /auth/login → 302
  ▼
Pocket ID (OIDC, passkeys/MFA)
  │  Authorization Code + PKCE
  ▼
零界 auth-gateway
  │  校验 id_token 的 iss/aud/nonce/exp/签名
  │  identity_registry: idp_sub → IdentityContext
  │  POST /oauth/token  (RFC 8693)
  │    subject_token ← id_token
  │    audience     = zero_bound / zhice
  ▼
SessionContext + HttpOnly cookie
```

- **Pocket ID**：单人/家庭规模，轻量、支持 passkeys；优先。
- **Zitadel**：多用户/团队、服务账号、审计更完整时再升级；当前不必上。
- **Keycloak**：JVM/运维重，不作为首选。
- **Zhice 适配**：Zhice 当前只认自研 HMAC JWT，不认 OIDC。Phase B 需二选一：
  1. 在 Zhice 增加 **OIDC 校验适配器**（推荐，长期）；
  2. 零界 auth-gateway 用受信服务账号/短期 token 代调 Zhice（过渡，权限最小化）。

## 3. 统一契约

新增三份：

| 契约 | 文件 | 用途 |
|---|---|---|
| SessionContext | `agent_platform/contracts/reuse/session_context.schema.json` | 服务端会话 + IdentityContext |
| TokenExchangeRequest | `token_exchange_request.schema.json` | RFC 8693 请求 |
| TokenExchangeResponse | `token_exchange_response.schema.json` | 交换结果（access_token 只回 BFF） |

`IdentityContext` 仍是**描述**，不是授权；各系统 RBAC 继续独立判断。

### SessionContext 关键字段

```json
{
  "session_id": "opaque",
  "sub": "zero_bound:owner",
  "idp": "zhice|pocket_id",
  "auth_time": "...",
  "expires_at": "...",
  "amr": ["pwd|passkey|mfa|sso"],
  "read_only": true,
  "identity_context": {"...": "IdentityContext"},
  "csrf_token_hash": null
}
```

### Token Exchange 关键字段

```json
{
  "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
  "subject_token": "<id_token>",
  "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
  "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "audience": "zero_bound",
  "scope": "quant.readonly research.readonly"
}
```

约束：

- `subject_token` 必须由受信 IdP 签发并验证；
- `audience` 必须精确匹配下游；
- `expires_in` ≤ 3600；
- 代理场景才加 `act`（RFC 8693 actor claim）；
- 不接受客户端自报的 `sub/roles/permissions`。

## 4. 安全要求（硬门）

1. **HTTPS 之前不允许密码登录**；本机 localhost 仅临时；
2. Cookie：HttpOnly + Secure + SameSite=Lax；禁止 localStorage；
3. CSRF：登录/登出/交换全部校验 CSRF token（hash 存服务端）；
4. OIDC：Authorization Code + PKCE(S256)、state、nonce、精确 redirect_uri、iss/aud/exp/签名校验；
5. 会话：登录轮换 SID、绝对/空闲超时、登出即撤销；
6. 限流/锁定/审计：登录失败按 IP+用户限流，不区分用户不存在/密码错；
7. Token Exchange：短 TTL、audience 限制、可撤销、不得转发给浏览器；
8. 不记录密码/token/cookie；不把 `zhice` token 放日志；
9. 不将 `unconfirmed` 的老田↔菜鸟映射用于登录/授权；
10. 不改写实盘/交易参数；P3 只做认证。

## 5. 工作拆解

| 步骤 | 内容 | 工作量 |
|---|---|---|
| A1 | SessionContext 契约 + 服务端 session store（内存/SQLite） | 1 天 |
| A2 | `POST /api/auth/session`、`GET /api/bff/identity/me`、logout | 1 天 |
| A3 | Zhice `/auth/login` + `/auth/me` 校验 + registry 映射 | 0.5 天 |
| A4 | 登录页（UI Kit）+ 受保护路由/特征开关 | 0.5 天 |
| A5 | 测试/审计/HTTPS 前缀检查 | 0.5 天 |
| B1 | Pocket ID 部署（OIDC client） | 0.5 天 |
| B2 | OIDC RP（code+PKCE）+ 回调 | 1-2 天 |
| B3 | registry 增 `idp_sub/idp_issuer` + 映射 | 0.5 天 |
| B4 | RFC 8693 `/oauth/token` + audience 策略 | 2-3 天 |
| B5 | Zhice OIDC 适配器/受信代调；`/zhice/` 同源 | 2-4 天 |
| B6 | 安全评审 + L2 + 回归 | 1 天 |

Phase A 可独立上线，不阻塞当前只读链路。

## 6. 验收

### Phase A

- 登录成功 → `zb_session` cookie HttpOnly；浏览器 localStorage 无 token；
- `GET /api/bff/identity/me` 返回 SessionContext + IdentityContext；
- 过期/登出 → 401；
- 登录失败限流；密码/token 不出现在日志；
- 现有 `/api/bff/*` 只读端点行为不变。

### Phase B

- OIDC code+PKCE 登录成功；nonce/state 校验失败被拒；
- id_token 验签/iss/aud/exp 全通过；
- token exchange audience 错误 → 拒绝；
- 老田↔菜鸟仍是 unconfirmed；不产生跨系统权限；
- 无 token 进入 localStorage / URL / 日志。

## 7. 明确不做

- 不把 Zhice autoSession 搬进零界；
- 不在零界复制用户表/密码；
- 不把 access token 放浏览器；
- 不合并智策/菜鸟数据库；
- 不做交易/审批写操作（后续 L2）。

## 8. 参考

- [RFC 8693 OAuth 2.0 Token Exchange](https://www.rfc-editor.org/rfc/rfc8693)
- [OAuth 2.0 for Browser-Based Apps (BCP)](https://datatracker.ietf.org/doc/draft-ietf-oauth-browser-based-apps/)
- [Auth0: BFF pattern 常见错误](https://auth0.com/blog/things-developers-get-wrong-about-the-backend-for-frontend-pattern/)
- [Pocket ID vs Zitadel vs Authentik 对比](https://www.bigiron.cc/guides/pocket-id-vs-zitadel-vs-authentik-homelab-identity-2026)
- [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html)

## 9. 建议

先做 **Phase A**（BFF 会话桥），它能把“真实登录态 → IdentityContext”接通，且不引入新 IdP；验证稳定后再上 Pocket ID/OIDC。Phase A 的登录页和 session 端点属于认证面，应单独开 `auth-gateway` 模块，放在只读 BFF 之外，避免破坏“BFF 只读”边界。
