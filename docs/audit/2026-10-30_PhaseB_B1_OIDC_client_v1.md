# Phase B1：auth-gateway OIDC 客户端（默认 off）v1

- 日期：2026-10-30
- 工单：`WO-LEGION-POCKETID-B1-20261030`
- 前置：Pocket ID B0 已部署（`http://localhost:1411` healthy）
- 状态：**代码完成；默认 `AUTH_OIDC_MODE=off`；登录入口未切换**

## 1. 新增端点

由 auth_gateway 转发（`/api/auth/oidc/*`）：

| 端点 | 行为 |
|---|---|
| `GET /api/auth/oidc/status` | 返回 mode / issuer / ready；不泄露 secret |
| `GET /api/auth/oidc/start` | mode=off → 404；否则生成 state/nonce/PKCE(S256)，302 Pocket ID |
| `GET /api/auth/oidc/callback` | 校验 state/code，换 token，验 id_token，建 HttpOnly session |

## 2. 配置

```text
AUTH_OIDC_MODE=off | shadow | on      # 默认 off
OIDC_ISSUER=http://localhost:1411
OIDC_CLIENT_ID=<vault>
OIDC_CLIENT_SECRET=<vault>
OIDC_REDIRECT_URI=http://localhost:8734/api/auth/oidc/callback
OIDC_SCOPES=openid profile email
OIDC_CREDENTIALS_FILE=C:\aios\_vault\pocket-id\secrets\zero_bound_client.env
```

- 默认 off：`start`/`callback` 404，登录入口完全不变；
- `shadow`/`on` 才允许 OIDC 流程；B2 才接登录页；
- client 凭据只从环境/vault 读，不入库、不回显。

## 3. 安全实现

- Authorization Code + PKCE(S256)；
- state 服务端单次存储，10 分钟过期；
- nonce 写入 id_token 并校验；
- JWKS 按 kid 缓存 10 分钟；
- PyJWT 校验 `iss/aud/exp/iat/sub` + RS256 签名；
- ID token/access token 不下发浏览器；
- 未映射 `idp_sub` → 403，不自动建身份；
- SessionContext: `idp=pocket_id`，`amr` 来自 id_token（至少 passkey/sso）；
- 仍复用 Phase A 内存 SessionStore/HttpOnly cookie；
- 不改只读 BFF、不改 5 页 auth_request。

## 4. 验证

`portal/tests/test_auth_oidc.py`（6 项）：

- 默认 off：start 404；
- status 自述；
- start 生成 PKCE/state/nonce；
- callback 用 mock RSA/JWKS/id_token 建 session；
- 错误 nonce 401；
- 未映射 403。

```bash
python -m pytest portal/tests/test_auth_oidc.py -q
# 6 passed
```

## 5. 未做

- 未创建 Pocket ID 管理员/passkey/OIDC client（B2）；
- 未改 `/login` UI；
- 未切换密码桥；
- 未验证真实 Pocket ID code exchange（需要 B2 建 client 后才能测）。

## 6. 回滚

- 删除 `portal/auth_oidc.py` + auth_gateway 中 OIDC 转发 + `resolve_oidc`；
- 重启 8091；登录仍是 Phase A 密码桥；
- B0 容器不受影响。

## 7. 下一步

B2：

1. Pocket ID 创建管理员 + passkey；
2. 创建 OIDC client `zero-bound-bff`，回调 `http://localhost:8734/api/auth/oidc/callback`；
3. 把 client_id/secret 写 vault；
4. 老田注册 passkey，取 sub，写 `identity_registry` 的 `idp_sub/idp_issuer`；
5. `AUTH_OIDC_MODE=shadow` 验证 → `on` 切换登录页；
6. 密码桥保留 2 周回退。
